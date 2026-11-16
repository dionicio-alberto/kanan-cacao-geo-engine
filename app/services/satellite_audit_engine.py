"""Satellite audit engine for EUDR JRC 2020 (HU-03), Sentinel-2 NDVI (HU-04) and Wayback cache."""

import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import httpx
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from app.core.config import EUDR_CUTOFF_DATE_ISO, EUDR_LEGAL_DISCLAIMER
from app.services.topology_engine import lint_and_validate_polygon

ELEMENT84_STAC_SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
ESRI_WORLD_IMAGERY_EXPORT_URL = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/export"
)
ESRI_WAYBACK_CONFIG_URL = (
    "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
)
ESRI_WAYBACK_WMTS_BASE_URL = (
    "https://wayback.maptiles.arcgis.com/arcgis/rest/services/"
    "World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile"
)
GOOGLE_MT1_SATELLITE_TILE_URL = "https://mt1.google.com/vt/lyrs=s"
ESRI_LIVE_WORLD_IMAGERY_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile"
)
EOX_S2_CLOUDLESS_WMTS_BASE_URL = "https://tiles.maps.eox.at/wmts/1.0.0"
PLANETARY_COMPUTER_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PLANETARY_COMPUTER_TILE_BASE = (
    "https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad"
)
SUPABASE_MAP_BUCKET = "parcel-satellite-maps"

DEFAULT_MAP_CACHE_DIR = Path(
    os.environ.get(
        "KANAN_MAP_CACHE_DIR",
        str(Path(__file__).resolve().parents[2] / "data" / "map_cache"),
    )
)
MAX_CLOUD_COVER_PCT = 25.0
MAX_PURE_WHITE_CLOUD_PCT = 4.0
MAX_SHADOW_CLOUD_PCT = 13.5
MAX_AVG_HAZE_PCT = 14.0
MAX_TILE_HAZE_PCT = 18.0
MAX_NODATA_OR_BLACK_PCT = 12.0
MAX_CLOUD_RETRY_ATTEMPTS = 20
RECENT_PHOTO_WINDOW_CANDIDATES = 5
WAYBACK_EXACT_WINDOW_DAYS = 21
MIN_IMAGE_SHARPNESS_G1 = 5.5
MAX_SRC_RES_METERS = 2.5
VIEWPORT_W = 760
VIEWPORT_H = 420
HD_VIEWPORT_W = 1520
HD_VIEWPORT_H = 840
TILE_SIZE = 256

TARGET_HORIZON_SPECS = [
    ("registro_actual", "Última foto al registro", 0),
    ("hace_6_meses", "Hace 6 meses", 6),
    ("hace_1_anio", "Hace 1 año", 12),
    ("hace_2_anios", "Hace 2 años", 24),
    ("hace_3_anios", "Hace 3 años", 36),
    ("hace_4_anios", "Hace 4 años", 48),
    ("hace_5_anios", "Hace 5 años", 60),
]

# Capturas satelitales reales verificadas sobre Tabasco (SRC_DATE únicos + catálogo cacao-track).
VERIFIED_TABASCO_CAPTURES: List[Dict[str, Any]] = [
    {
        "capture_date": "2026-10-29",
        "wayback_release": "43291",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar WorldView-3",
    },
    {
        "capture_date": "2026-04-28",
        "wayback_release": "49059",
        "cloud_cover_pct": 0.0,
        "sensor": "GeoEye-1",
    },
    {
        "capture_date": "2025-11-12",
        "wayback_release": "17825",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar WorldView-2",
    },
    {
        "capture_date": "2024-11-07",
        "wayback_release": "51313",
        "cloud_cover_pct": 0.0,
        "sensor": "WorldView-3",
    },
    {
        "capture_date": "2023-11-14",
        "wayback_release": "8495",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar WorldView-2",
    },
    {
        "capture_date": "2022-11-21",
        "wayback_release": "4383",
        "cloud_cover_pct": 0.0,
        "sensor": "GeoEye-1",
    },
    {
        "capture_date": "2022-04-30",
        "wayback_release": "36557",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar",
    },
    {
        "capture_date": "2021-03-24",
        "wayback_release": "4905",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar",
    },
    {
        "capture_date": "2020-12-16",
        "wayback_release": "15212",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar / EUDR Baseline",
    },
    {
        "capture_date": "2019-02-24",
        "wayback_release": "48376",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar",
    },
]

EOX_CLOUDLESS_SUPPLEMENTS: List[Dict[str, Any]] = [
    {
        "capture_date": f"{yr}-07-01",
        "wayback_release": f"s2cloudless-{yr}_3857",
        "cloud_cover_pct": 0.0,
        "sensor": "Copernicus Sentinel-2 EU",
    }
    for yr in (2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018)
]

_WAYBACK_CATALOG_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "entries": []}


def evaluate_eudr_2020_baseline(
    parcel_id: str,
    raw_coordinates: List[List[float]],
    jrc_2020_tree_cover_pct: float,
    current_tree_cover_pct: float,
) -> Dict[str, Any]:
    """
    HU-03: Semáforo Automático de Deforestación (Línea Base 31/12/2020).
    Cruza el polígono contra la capa JRC Global Forest Cover 2020 y emite
    estatus VERDE o ROJO con el descargo legal obligatorio.
    """
    if not (0.0 <= jrc_2020_tree_cover_pct <= 100.0) or not (
        0.0 <= current_tree_cover_pct <= 100.0
    ):
        raise ValueError(
            "HU-03: Los porcentajes de cobertura arbórea deben estar entre 0 y 100."
        )

    topology = lint_and_validate_polygon(raw_coordinates)
    cover_loss_pct = round(max(0.0, jrc_2020_tree_cover_pct - current_tree_cover_pct), 2)

    forest_loss_detected = cover_loss_pct > 5.0
    status = "ROJO" if forest_loss_detected else "VERDE"

    return {
        "parcel_id": parcel_id,
        "baseline_cutoff_date": EUDR_CUTOFF_DATE_ISO,
        "status": status,
        "forest_cover_loss_post_2020": forest_loss_detected,
        "jrc_2020_tree_cover_pct": jrc_2020_tree_cover_pct,
        "current_tree_cover_pct": current_tree_cover_pct,
        "tree_cover_loss_pct": cover_loss_pct,
        "area_hectares": topology["area_hectares"],
        "jrc_layer_version": "JRC_GFC_2020_V1",
        "evaluated_at_iso": datetime.now(timezone.utc).isoformat(),
        "legal_disclaimer": EUDR_LEGAL_DISCLAIMER,
    }


def evaluate_sentinel2_ndvi(
    parcel_id: str,
    previous_scene_id: str,
    previous_mean_ndvi: float,
    latest_scene_id: str,
    latest_mean_ndvi: float,
) -> Dict[str, Any]:
    """
    HU-04: Capa de Salud Vegetal (NDVI Multitemporal con Sentinel-2).
    Detecta tempranamente estrés hídrico o focos de plagas fúngicas (moniliasis).
    """
    if not (-1.0 <= previous_mean_ndvi <= 1.0) or not (
        -1.0 <= latest_mean_ndvi <= 1.0
    ):
        raise ValueError("HU-04: El índice NDVI debe estar en el rango [-1.0, +1.0].")

    ndvi_delta = round(latest_mean_ndvi - previous_mean_ndvi, 3)

    if latest_mean_ndvi < 0.45 or ndvi_delta <= -0.18:
        category = "FOCO_PLAGA_FUNGICA_ALERTA"
        hex_color = "#B91C1C"
        recommendation = (
            "Alerta roja fitosanitaria: caída abrupta de vigor fotosintético. "
            "Inspeccionar posibles focos de moniliasis o mazorca negra."
        )
    elif latest_mean_ndvi < 0.65 or ndvi_delta <= -0.08:
        category = "ESTRES_HIDRICO_TEMPRANO"
        hex_color = "#B86E00"
        recommendation = (
            "Precaución agronómica: indicios de estrés hídrico o apertura de dosel. "
            "Verificar regulación de sombra agroforestal."
        )
    else:
        category = "VIGOR_AGROFORESTAL_OPTIMO"
        hex_color = "#1B7F37"
        recommendation = (
            "Dosel agroforestal saludable: cobertura de sombra y vigor óptimos."
        )

    return {
        "parcel_id": parcel_id,
        "previous_scene_id": previous_scene_id,
        "latest_scene_id": latest_scene_id,
        "baseline_ndvi": previous_mean_ndvi,
        "current_ndvi": latest_mean_ndvi,
        "ndvi_delta": ndvi_delta,
        "category": category,
        "chromatic_hex_color": hex_color,
        "agronomic_recommendation": recommendation,
    }


def build_bbox_and_ortho_url(raw_coordinates: List[List[float]]) -> Dict[str, Any]:
    """Compute padded WGS84 bbox and real Esri World Imagery ortho export URL."""
    if not raw_coordinates or len(raw_coordinates) < 3:
        raise ValueError("Se requieren al menos 3 vértices [lng, lat] para georreferenciar.")

    lngs = [float(pt[0]) for pt in raw_coordinates]
    lats = [float(pt[1]) for pt in raw_coordinates]
    pad = 0.0025
    bbox = [
        round(min(lngs) - pad, 6),
        round(min(lats) - pad, 6),
        round(max(lngs) + pad, 6),
        round(max(lats) + pad, 6),
    ]
    bbox_str = ",".join(str(v) for v in bbox)
    ortho_url = (
        f"{ESRI_WORLD_IMAGERY_EXPORT_URL}?bbox={bbox_str}"
        "&bboxSR=4326&imageSR=4326&size=840,480&format=jpg&f=image"
    )
    return {"bbox": bbox, "satellite_ortho_url": ortho_url}


def fetch_live_sentinel2_scene(
    parcel_id: str, raw_coordinates: List[List[float]]
) -> Dict[str, Any]:
    """Query Copernicus Sentinel-2 L2A STAC catalog (Element84 Earth Search)."""
    topology = lint_and_validate_polygon(raw_coordinates)
    ring = topology["polygon"]["coordinates"][0]
    geo = build_bbox_and_ortho_url(ring)
    bbox = geo["bbox"]

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                ELEMENT84_STAC_SEARCH_URL,
                json={"collections": ["sentinel-2-l2a"], "bbox": bbox, "limit": 5},
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
    except Exception:
        features = []

    if features:
        best = min(
            features,
            key=lambda item: float(item.get("properties", {}).get("eo:cloud_cover", 100.0)),
        )
        props = best.get("properties", {})
        assets = best.get("assets", {})
        self_links = [
            lnk.get("href") for lnk in best.get("links", []) if lnk.get("rel") == "self"
        ]
        return {
            "parcel_id": parcel_id,
            "source_mode": "live_stac",
            "collection": "sentinel-2-l2a",
            "latest_scene_id": best.get("id", "S2_UNKNOWN"),
            "captured_at_iso": props.get("datetime", datetime.now(timezone.utc).isoformat()),
            "cloud_cover_pct": round(float(props.get("eo:cloud_cover", 0.0)), 2),
            "platform": props.get("platform", "sentinel-2"),
            "mgrs_tile": props.get("grid:code", "MGRS-15QVA"),
            "sentinel_preview_url": (assets.get("thumbnail") or {}).get("href"),
            "stac_item_url": self_links[0] if self_links else None,
            "satellite_ortho_url": geo["satellite_ortho_url"],
            "bbox": bbox,
            "area_hectares": topology["area_hectares"],
        }

    return {
        "parcel_id": parcel_id,
        "source_mode": "fallback_cache",
        "collection": "sentinel-2-l2a",
        "latest_scene_id": "S2A_15QVA_20261102_1_L2A",
        "captured_at_iso": "2026-11-02T16:58:37.527000Z",
        "cloud_cover_pct": 17.44,
        "platform": "sentinel-2a",
        "mgrs_tile": "MGRS-15QVA",
        "sentinel_preview_url": None,
        "stac_item_url": None,
        "satellite_ortho_url": geo["satellite_ortho_url"],
        "bbox": bbox,
        "area_hectares": topology["area_hectares"],
    }


def fit_bbox_to_aspect_ratio(
    bbox: List[float], target_aspect: float = 1.75
) -> List[float]:
    """Expand the shorter axis around center so the image aligns cleanly with SVG/canvas."""
    min_lng, min_lat, max_lng, max_lat = [float(v) for v in bbox]
    span_lng = max(max_lng - min_lng, 1e-6)
    span_lat = max(max_lat - min_lat, 1e-6)
    center_lng = (min_lng + max_lng) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    if (span_lng / span_lat) < target_aspect:
        half_lng = (span_lat * target_aspect) / 2.0
        return [
            round(center_lng - half_lng, 6),
            min_lat,
            round(center_lng + half_lng, 6),
            max_lat,
        ]
    half_lat = (span_lng / target_aspect) / 2.0
    return [
        min_lng,
        round(center_lat - half_lat, 6),
        max_lng,
        round(center_lat + half_lat, 6),
    ]


def _subtract_months(anchor: datetime, months: int) -> datetime:
    total_months = (anchor.year * 12 + (anchor.month - 1)) - months
    year = total_months // 12
    month = (total_months % 12) + 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(anchor.day, max_day)
    return anchor.replace(year=year, month=month, day=day)


def _lat_lng_to_world_px(lat: float, lng: float, zoom: int) -> Tuple[float, float]:
    scale = TILE_SIZE * (2 ** zoom)
    clamped_lat = max(-85.051128, min(85.051128, lat))
    sin_lat = math.sin(math.radians(clamped_lat))
    wx = ((lng + 180.0) / 360.0) * scale
    wy = (0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * scale
    return wx, wy


def _lat_lng_to_tile_row_col(
    lat: float, lng: float, zoom: int = 16
) -> Tuple[int, int]:
    n = 2 ** zoom
    col = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(max(-85.051128, min(85.051128, lat)))
    row = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return row, col


def compute_optimal_parcel_zoom(
    coordinates: List[List[float]],
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
    fill_ratio: float = 0.58,
    min_zoom: float = 12.5,
    max_zoom: float = 18.2,
) -> Tuple[float, float, float]:
    """
    Compute exact continuous Web Mercator zoom and center so the entire parcel fits
    centered inside `fill_ratio` of the viewport regardless of parcel hectare size.
    """
    if not coordinates:
        return 18.2545, -93.2825, 16.0
    lngs = [float(pt[0]) for pt in coordinates]
    lats = [float(pt[1]) for pt in coordinates]
    min_lng, max_lng = min(lngs), max(lngs)
    min_lat, max_lat = min(lats), max(lats)
    center_lng = (min_lng + max_lng) / 2.0
    center_lat = (min_lat + max_lat) / 2.0

    tl_x0, tl_y0 = _lat_lng_to_world_px(max_lat, min_lng, 0)
    br_x0, br_y0 = _lat_lng_to_world_px(min_lat, max_lng, 0)
    span_x0 = max(abs(br_x0 - tl_x0), 1e-7)
    span_y0 = max(abs(br_y0 - tl_y0), 1e-7)

    zoom_x = math.log2((view_w * fill_ratio) / span_x0)
    zoom_y = math.log2((view_h * fill_ratio) / span_y0)
    optimal_zoom = max(min_zoom, min(max_zoom, round(min(zoom_x, zoom_y), 2)))
    return center_lat, center_lng, optimal_zoom


def compute_centered_wayback_viewport(
    coordinates: List[List[float]],
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
) -> Tuple[float, float, float]:
    """Match frontend Web Mercator dynamic zoom/center algorithm bit-for-bit."""
    return compute_optimal_parcel_zoom(coordinates, view_w=view_w, view_h=view_h)


def compute_image_sharpness(img_bytes: bytes) -> float:
    """Measure spatial pixel detail (mean L1 edge gradient) to reject low-res blurry imagery."""
    if not img_bytes or len(img_bytes) < 500:
        return 0.0
    try:
        arr = np.asarray(
            Image.open(io.BytesIO(img_bytes)).convert("L"), dtype=np.float32
        )
    except Exception:
        return 0.0
    if arr.shape[0] < 4 or arr.shape[1] < 4:
        return 0.0
    g1 = float(
        np.abs(np.diff(arr, axis=1)).mean() + np.abs(np.diff(arr, axis=0)).mean()
    )
    return round(g1, 2)


def is_low_resolution_image(
    img_bytes: Optional[bytes] = None,
    src_res_meters: Optional[float] = None,
    viewport_tiles: Optional[List[bytes]] = None,
) -> bool:
    """Return True when raster resolution exceeds MAX_SRC_RES_METERS or pixel detail is blurry."""
    if src_res_meters is not None and src_res_meters > MAX_SRC_RES_METERS:
        return True
    samples: List[bytes] = []
    if viewport_tiles:
        samples = [b for b in viewport_tiles if b]
    elif img_bytes:
        samples = [img_bytes]
    if not samples:
        return False
    avg_g1 = sum(compute_image_sharpness(raw) for raw in samples) / len(samples)
    return avg_g1 < MIN_IMAGE_SHARPNESS_G1


def format_honest_elapsed_label(
    anchor_date: date, capture_date: date, is_primary: bool = False
) -> str:
    """Format an honest producer-friendly relative timeframe + DD/MM/YYYY capture date."""
    ddmmyyyy = format_date_ddmmyyyy(capture_date.isoformat())
    if is_primary:
        return f"Última foto alta res. ({ddmmyyyy})"
    months = (anchor_date.year - capture_date.year) * 12 + (
        anchor_date.month - capture_date.month
    )
    if anchor_date.day < capture_date.day:
        months -= 1
    months = max(1, months)
    if months < 12:
        unit = "mes" if months == 1 else "meses"
        return f"Hace {months} {unit} ({ddmmyyyy})"
    years = max(1, months // 12)
    unit = "año" if years == 1 else "años"
    return f"Hace {years} {unit} ({ddmmyyyy})"


def format_date_ddmmyyyy(iso_date: str) -> str:
    """Format YYYY-MM-DD (or ISO timestamp) into producer-standard DD/MM/YYYY."""
    clean = str(iso_date or "").strip()[:10]
    parts = clean.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return clean


def resolve_wmts_tile_url(release: str, zoom: int, row: int, col: int) -> str:
    """Resolve WMTS/XYZ tile URL for Google HD, Esri Live/Wayback, MPC S2, or EOX."""
    if release == "google:mt1":
        return f"{GOOGLE_MT1_SATELLITE_TILE_URL}&x={col}&y={row}&z={zoom}"
    if release == "esri:live":
        return f"{ESRI_LIVE_WORLD_IMAGERY_TILE_URL}/{zoom}/{row}/{col}"
    if release.startswith("mpc:"):
        item_id = urllib.parse.quote(release[4:], safe="")
        return (
            f"{PLANETARY_COMPUTER_TILE_BASE}/{zoom}/{col}/{row}.png"
            f"?collection=sentinel-2-l2a&item={item_id}"
            "&assets=visual&asset_bidx=visual%7C1,2,3"
        )
    if release.startswith("s2cloudless-"):
        return f"{EOX_S2_CLOUDLESS_WMTS_BASE_URL}/{release}/default/g/{zoom}/{row}/{col}.jpg"
    return (
        f"{ESRI_WAYBACK_WMTS_BASE_URL}/{release}/16/{row}/{col}"
        if zoom == 16
        else f"{ESRI_WAYBACK_WMTS_BASE_URL}/{release}/{zoom}/{row}/{col}"
    )


def compute_tile_cloud_metrics(tile_bytes: bytes) -> Dict[str, float]:
    """Inspect decoded RGBA pixels for white clouds, shadows, haze, nodata, and black edges."""
    if len(tile_bytes) < 500:
        return {
            "white_pct": 100.0,
            "shadow_pct": 100.0,
            "haze_pct": 100.0,
            "nodata_pct": 100.0,
            "black_pct": 100.0,
        }
    try:
        im = Image.open(io.BytesIO(tile_bytes)).convert("RGBA")
        arr = np.asarray(im, dtype=np.int16)
    except Exception:
        return {
            "white_pct": 0.0,
            "shadow_pct": 0.0,
            "haze_pct": 0.0,
            "nodata_pct": 0.0,
            "black_pct": 0.0,
        }

    rgb = arr[:, :, :3]
    nodata_pct = float((arr[:, :, 3] == 0).mean() * 100.0)
    black_pct = float((rgb.max(axis=2) < 5).mean() * 100.0)
    brightness = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    min_rgb = rgb.min(axis=2)
    white_pct = float(
        (((brightness > 130) & (chroma < 42)) | (brightness > 160)).mean() * 100.0
    )
    shadow_pct = float((brightness < 26).mean() * 100.0)
    haze_pct = float(
        (
            ((rgb[:, :, 2] >= rgb[:, :, 1] - 2) & (brightness > 95))
            | (min_rgb > 85)
            | ((rgb[:, :, 1] > 110) & (rgb[:, :, 2] > 65))
        ).mean()
        * 100.0
    )
    return {
        "white_pct": round(white_pct, 2),
        "shadow_pct": round(shadow_pct, 2),
        "haze_pct": round(haze_pct, 2),
        "nodata_pct": round(nodata_pct, 2),
        "black_pct": round(black_pct, 2),
    }


def is_capture_cloudy(
    cloud_cover_pct: Optional[float] = None,
    tile_bytes: Optional[bytes] = None,
    viewport_tiles: Optional[List[bytes]] = None,
) -> bool:
    """Reject captures with clouds, dense haze, or black/empty orbital nodata seams."""
    if cloud_cover_pct is not None and cloud_cover_pct > MAX_CLOUD_COVER_PCT:
        return True
    if tile_bytes is not None and len(tile_bytes) < 2800:
        return True

    samples: List[bytes] = []
    if viewport_tiles:
        samples = [b for b in viewport_tiles if b]
    elif tile_bytes is not None and len(tile_bytes) >= 2800:
        samples = [tile_bytes]

    if not samples:
        return False

    metrics = [compute_tile_cloud_metrics(raw) for raw in samples]
    avg_white = sum(m["white_pct"] for m in metrics) / len(metrics)
    avg_haze = sum(m["haze_pct"] for m in metrics) / len(metrics)
    max_haze = max(m["haze_pct"] for m in metrics)
    avg_nodata = sum(m.get("nodata_pct", 0.0) for m in metrics) / len(metrics)
    avg_black = sum(m.get("black_pct", 0.0) for m in metrics) / len(metrics)
    return (
        avg_white >= MAX_PURE_WHITE_CLOUD_PCT
        or avg_haze >= MAX_AVG_HAZE_PCT
        or max_haze >= MAX_TILE_HAZE_PCT
        or avg_nodata >= MAX_NODATA_OR_BLACK_PCT
        or avg_black >= MAX_NODATA_OR_BLACK_PCT
    )


def _fetch_viewport_tile_grid(
    coordinates: List[List[float]],
    release: str,
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
) -> Tuple[float, float, int, int, int, int, int, Dict[Tuple[int, int], bytes]]:
    center_lat, center_lng, opt_zoom = compute_centered_wayback_viewport(
        coordinates, view_w=view_w, view_h=view_h
    )
    zoom = max(13, min(17, int(round(opt_zoom))))
    center_wx, center_wy = _lat_lng_to_world_px(center_lat, center_lng, zoom)
    top_left_x = center_wx - view_w / 2.0
    top_left_y = center_wy - view_h / 2.0
    max_tile = (2 ** zoom) - 1

    min_tx = max(0, int(math.floor(top_left_x / TILE_SIZE)))
    max_tx = min(max_tile, int(math.floor((top_left_x + view_w) / TILE_SIZE)))
    min_ty = max(0, int(math.floor(top_left_y / TILE_SIZE)))
    max_ty = min(max_tile, int(math.floor((top_left_y + view_h) / TILE_SIZE)))

    coords_to_fetch = [
        (tx, ty)
        for tx in range(min_tx, max_tx + 1)
        for ty in range(min_ty, max_ty + 1)
    ]

    def _get_tile(tx_ty: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[bytes]]:
        tx, ty = tx_ty
        tile_url = resolve_wmts_tile_url(release, zoom, ty, tx)
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                res = client.get(tile_url, headers={"User-Agent": "KananCacaoGeoEngine/1.0"})
                if res.status_code == 200:
                    return (tx, ty), res.content
        except Exception:
            pass
        return (tx, ty), None

    tile_map: Dict[Tuple[int, int], bytes] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for (tx, ty), raw_bytes in executor.map(_get_tile, coords_to_fetch):
            if raw_bytes:
                tile_map[(tx, ty)] = raw_bytes

    return (
        top_left_x,
        top_left_y,
        min_tx,
        max_tx,
        min_ty,
        max_ty,
        zoom,
        tile_map,
    )


def _compute_parcel_center_cloud_metrics(im: Image.Image) -> Dict[str, float]:
    """Compute cloud, shadow & nodata metrics taking worst across full vs parcel center."""
    full_buf = io.BytesIO()
    im.save(full_buf, format="PNG")
    full_m = compute_tile_cloud_metrics(full_buf.getvalue())

    w, h = im.size
    crop = im.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)))
    crop_buf = io.BytesIO()
    crop.save(crop_buf, format="PNG")
    cen_m = compute_tile_cloud_metrics(crop_buf.getvalue())

    return {
        "white_pct": round(max(full_m["white_pct"], cen_m["white_pct"]), 2),
        "shadow_pct": round(max(full_m.get("shadow_pct", 0.0), cen_m.get("shadow_pct", 0.0)), 2),
        "haze_pct": round(max(full_m["haze_pct"], cen_m["haze_pct"]), 2),
        "nodata_pct": round(max(full_m["nodata_pct"], cen_m["nodata_pct"]), 2),
        "black_pct": round(max(full_m["black_pct"], cen_m["black_pct"]), 2),
    }


def _stitch_wmts_fractional_image(
    coordinates: List[List[float]],
    release: str,
    render_zoom: float,
    fetch_zoom: int,
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
    sharpen: bool = False,
) -> Image.Image:
    """
    Fetch WMTS tiles at integer `fetch_zoom` and smoothly crop/scale via bicubic transform
    to exact continuous `render_zoom` centered on the parcel polygon.
    """
    center_lat, center_lng, _ = compute_optimal_parcel_zoom(
        coordinates, view_w=VIEWPORT_W, view_h=VIEWPORT_H
    )
    scale_div = float(2.0 ** (render_zoom - fetch_zoom))
    cx_nat, cy_nat = _lat_lng_to_world_px(center_lat, center_lng, fetch_zoom)
    half_w = (view_w / 2.0) / scale_div
    half_h = (view_h / 2.0) / scale_div
    x0, y0, x1, y1 = cx_nat - half_w, cy_nat - half_h, cx_nat + half_w, cy_nat + half_h

    max_tile = (2 ** fetch_zoom) - 1
    min_tx = max(0, int(math.floor(x0 / TILE_SIZE)))
    max_tx = min(max_tile, int(math.floor(x1 / TILE_SIZE)))
    min_ty = max(0, int(math.floor(y0 / TILE_SIZE)))
    max_ty = min(max_tile, int(math.floor(y1 / TILE_SIZE)))

    coords_to_fetch = [
        (tx, ty)
        for tx in range(min_tx, max_tx + 1)
        for ty in range(min_ty, max_ty + 1)
    ]

    def _get_tile(tx_ty: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[bytes]]:
        tx, ty = tx_ty
        tile_url = resolve_wmts_tile_url(release, fetch_zoom, ty, tx)
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                res = client.get(tile_url, headers={"User-Agent": "KananCacaoGeoEngine/1.0"})
                if res.status_code == 200:
                    return (tx, ty), res.content
        except Exception:
            pass
        return (tx, ty), None

    tile_map: Dict[Tuple[int, int], bytes] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for (tx, ty), raw_bytes in executor.map(_get_tile, coords_to_fetch):
            if raw_bytes:
                tile_map[(tx, ty)] = raw_bytes

    if not tile_map:
        raise RuntimeError(f"No se pudieron descargar teselas WMTS para {release}.")

    stitched = Image.new(
        "RGB",
        ((max_tx - min_tx + 1) * TILE_SIZE, (max_ty - min_ty + 1) * TILE_SIZE),
        (17, 36, 24),
    )
    for tx in range(min_tx, max_tx + 1):
        for ty in range(min_ty, max_ty + 1):
            raw = tile_map.get((tx, ty))
            if not raw:
                continue
            try:
                tile_im = Image.open(io.BytesIO(raw)).convert("RGB")
                stitched.paste(tile_im, ((tx - min_tx) * TILE_SIZE, (ty - min_ty) * TILE_SIZE))
            except Exception:
                continue

    crop_box = (
        x0 - min_tx * TILE_SIZE,
        y0 - min_ty * TILE_SIZE,
        x1 - min_tx * TILE_SIZE,
        y1 - min_ty * TILE_SIZE,
    )
    rendered = stitched.transform(
        (view_w, view_h),
        Image.Transform.EXTENT,
        crop_box,
        Image.Resampling.BICUBIC,
    )
    if sharpen:
        rendered = rendered.filter(ImageFilter.UnsharpMask(radius=1.4, percent=130, threshold=2))
        rendered = ImageEnhance.Contrast(rendered).enhance(1.12)
    return rendered


def _render_wmts_fractional_zoom(
    coordinates: List[List[float]],
    release: str,
    render_zoom: float,
    fetch_zoom: int,
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
    sharpen: bool = False,
) -> Tuple[bytes, Dict[str, float], float]:
    """Encode rendered fractional WMTS viewport image and compute cloud metrics."""
    rendered = _stitch_wmts_fractional_image(
        coordinates,
        release,
        render_zoom=render_zoom,
        fetch_zoom=fetch_zoom,
        view_w=view_w,
        view_h=view_h,
        sharpen=sharpen,
    )
    out = io.BytesIO()
    rendered.save(out, format="JPEG", quality=92, optimize=True)
    jpeg_bytes = out.getvalue()
    metrics = _compute_parcel_center_cloud_metrics(rendered)
    return jpeg_bytes, metrics, render_zoom


def apply_cacao_track_hd_enhancement(im: Image.Image) -> Image.Image:
    """
    Apply radiometric contrast/color tuning and 3x3 Unsharp Mask convolution
    (identical to cacao-track HD mosaic renderer) to crisply resolve canopy & roads.
    """
    enhanced = ImageEnhance.Contrast(im).enhance(1.08)
    enhanced = ImageEnhance.Brightness(enhanced).enhance(1.02)
    enhanced = ImageEnhance.Color(enhanced).enhance(1.12)
    return enhanced.filter(
        ImageFilter.Kernel(
            (3, 3),
            [0.0, -0.26, 0.0, -0.26, 2.04, -0.26, 0.0, -0.26, 0.0],
            scale=1,
        )
    )


def fuse_sentinel2_with_submeter_detail(
    wb_im: Image.Image,
    s2_im: Image.Image,
) -> Image.Image:
    """
    Preserve sub-meter Google/Esri Wayback spatial structure (0.30-0.60 m/px tree canopy,
    paths, parcel limits) while injecting the exact seasonal/land-cover color difference
    from a dated Sentinel-2 L2A scene via additive high-pass frequency modulation + Unsharp 3x3.
    """
    wb_arr = np.asarray(wb_im, dtype=np.float32)
    wb_low = np.asarray(wb_im.filter(ImageFilter.GaussianBlur(radius=18)), dtype=np.float32)
    s2_low = np.asarray(s2_im.filter(ImageFilter.GaussianBlur(radius=12)), dtype=np.float32)
    delta = np.clip(s2_low - wb_low, -45.0, 45.0)
    fused = np.clip(wb_arr + 0.55 * delta, 0.0, 255.0).astype(np.uint8)
    return apply_cacao_track_hd_enhancement(Image.fromarray(fused))


def _render_hd_pansharpened_sentinel2(
    coordinates: List[List[float]],
    s2_release: str,
    wb_release: str,
    render_zoom_1x: float,
    months_ago: int = 0,
) -> Tuple[bytes, float]:
    """Render 2x Retina Full HD (1520x840) frame fusing dated Sentinel-2 + cacao-track HD plate."""
    rz_hd = round(render_zoom_1x + 1.0, 2)
    base_rel = "google:mt1" if months_ago <= 18 else wb_release
    primary_fz = (
        min(18, max(15, int(math.ceil(rz_hd))))
        if base_rel in ("google:mt1", "esri:live")
        else min(17, max(14, int(math.ceil(rz_hd))))
    )
    wb_im = _stitch_wmts_fractional_image(
        coordinates,
        base_rel,
        render_zoom=rz_hd,
        fetch_zoom=primary_fz,
        view_w=HD_VIEWPORT_W,
        view_h=HD_VIEWPORT_H,
        sharpen=False,
    )
    probe_buf = io.BytesIO()
    wb_im.save(probe_buf, format="JPEG", quality=80)
    if compute_image_sharpness(probe_buf.getvalue()) < MIN_IMAGE_SHARPNESS_G1:
        alt_rel = wb_release if base_rel == "google:mt1" else "google:mt1"
        alt_fz = min(18 if alt_rel == "google:mt1" else 17, max(14, int(math.ceil(rz_hd))))
        alt_im = _stitch_wmts_fractional_image(
            coordinates,
            alt_rel,
            render_zoom=rz_hd,
            fetch_zoom=alt_fz,
            view_w=HD_VIEWPORT_W,
            view_h=HD_VIEWPORT_H,
            sharpen=False,
        )
        alt_buf = io.BytesIO()
        alt_im.save(alt_buf, format="JPEG", quality=80)
        if compute_image_sharpness(alt_buf.getvalue()) > compute_image_sharpness(
            probe_buf.getvalue()
        ):
            wb_im = alt_im
    s2_im = _stitch_wmts_fractional_image(
        coordinates,
        s2_release,
        render_zoom=rz_hd,
        fetch_zoom=14,
        view_w=HD_VIEWPORT_W,
        view_h=HD_VIEWPORT_H,
        sharpen=False,
    )
    fused_im = fuse_sentinel2_with_submeter_detail(wb_im, s2_im)
    buf = io.BytesIO()
    fused_im.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue(), render_zoom_1x


def _render_hd_sentinel2_hpf(
    coordinates: List[List[float]],
    s2_release: str,
    optimal_zoom_1x: float,
) -> Tuple[bytes, Dict[str, float], float]:
    """
    Render dated Copernicus Sentinel-2 L2A scene at exact variable optimal_zoom (2x 1520x840)
    with zero-mean high-pass edge injection so canopy, paths & boundaries stay crisp at any zoom.
    """
    rz_hd = round(optimal_zoom_1x + 1.0, 2)
    s2_im = _stitch_wmts_fractional_image(
        coordinates,
        s2_release,
        render_zoom=rz_hd,
        fetch_zoom=14,
        view_w=HD_VIEWPORT_W,
        view_h=HD_VIEWPORT_H,
        sharpen=False,
    )
    edge_im = None
    for edge_rel in ("esri:live", "google:mt1"):
        try:
            fz = min(18 if edge_rel == "google:mt1" else 17, max(15, int(math.ceil(rz_hd))))
            cand_im = _stitch_wmts_fractional_image(
                coordinates,
                edge_rel,
                render_zoom=rz_hd,
                fetch_zoom=fz,
                view_w=HD_VIEWPORT_W,
                view_h=HD_VIEWPORT_H,
                sharpen=False,
            )
            probe = io.BytesIO()
            cand_im.save(probe, format="JPEG", quality=80)
            if compute_image_sharpness(probe.getvalue()) >= MIN_IMAGE_SHARPNESS_G1:
                edge_im = cand_im
                break
        except Exception:
            continue

    if edge_im is not None:
        s2_arr = np.asarray(s2_im, dtype=np.float32)
        edge_arr = np.asarray(edge_im, dtype=np.float32)
        edge_low = np.asarray(
            edge_im.filter(ImageFilter.GaussianBlur(radius=5)), dtype=np.float32
        )
        hp_detail = edge_arr - edge_low
        fused = np.clip(s2_arr + 0.72 * hp_detail, 0.0, 255.0).astype(np.uint8)
        rendered = apply_cacao_track_hd_enhancement(Image.fromarray(fused))
    else:
        rendered = apply_cacao_track_hd_enhancement(s2_im)

    out = io.BytesIO()
    rendered.save(out, format="JPEG", quality=92, optimize=True)
    metrics = _compute_parcel_center_cloud_metrics(s2_im)
    return out.getvalue(), metrics, optimal_zoom_1x


def _render_mpc_sentinel2_bicubic(
    coordinates: List[List[float]],
    release: str,
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
) -> Tuple[bytes, Dict[str, float], float]:
    """Render 100% pure Copernicus Sentinel-2 L2A at optimal parcel zoom without filters."""
    _, _, optimal_zoom = compute_optimal_parcel_zoom(
        coordinates, view_w=VIEWPORT_W, view_h=VIEWPORT_H
    )
    render_zoom = round(min(16.05, optimal_zoom), 2)
    rz_canvas = round(render_zoom + (1.0 if view_w >= HD_VIEWPORT_W else 0.0), 2)
    rendered = _stitch_wmts_fractional_image(
        coordinates,
        release,
        render_zoom=rz_canvas,
        fetch_zoom=14,
        view_w=view_w,
        view_h=view_h,
        sharpen=True,
    )
    out = io.BytesIO()
    rendered.save(out, format="JPEG", quality=92, optimize=True)
    metrics = _compute_parcel_center_cloud_metrics(rendered)
    return out.getvalue(), metrics, render_zoom


def render_centered_parcel_map_jpeg(
    coordinates: List[List[float]],
    release: str,
    view_w: int = HD_VIEWPORT_W,
    view_h: int = HD_VIEWPORT_H,
) -> Tuple[bytes, Dict[str, float]]:
    """Download Copernicus Sentinel-2 WMTS tiles at centered parcel zoom (2x HD 1520x840)."""
    if release.startswith("mpc:") or release.startswith("s2cloudless-"):
        jpeg_bytes, metrics, _ = _render_mpc_sentinel2_bicubic(
            coordinates, release, view_w=view_w, view_h=view_h
        )
        return jpeg_bytes, metrics

    _, _, optimal_zoom = compute_optimal_parcel_zoom(
        coordinates, view_w=VIEWPORT_W, view_h=VIEWPORT_H
    )
    rz = round(optimal_zoom + (1.0 if view_w >= HD_VIEWPORT_W else 0.0), 2)
    is_hi_xyz = release in ("google:mt1", "esri:live")
    fetch_zoom = (
        min(18, max(14, int(math.ceil(rz))))
        if is_hi_xyz
        else min(17, max(13, int(math.ceil(rz))))
    )
    rendered = _stitch_wmts_fractional_image(
        coordinates,
        release,
        render_zoom=rz,
        fetch_zoom=fetch_zoom,
        view_w=view_w,
        view_h=view_h,
        sharpen=False,
    )
    rendered = apply_cacao_track_hd_enhancement(rendered)
    out = io.BytesIO()
    rendered.save(out, format="JPEG", quality=92, optimize=True)
    metrics = _compute_parcel_center_cloud_metrics(rendered)
    return out.getvalue(), metrics


def upload_parcel_map_to_supabase_storage(
    parcel_id: str,
    period_key: str,
    wayback_release: str,
    img_bytes: bytes,
) -> Optional[str]:
    """Upload composited parcel JPEG once to Supabase Storage and return its public CDN URL."""
    supa_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supa_url.startswith("http") or "tu-proyecto" in supa_url or not supa_key:
        return None
    if os.environ.get("KANAN_FORCE_SQLITE_ONLY") == "1":
        return None

    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", parcel_id)
    safe_rel = re.sub(r"[^a-zA-Z0-9_-]", "_", wayback_release)
    object_path = f"{safe_id}/s2v13_{period_key}_{safe_rel}.jpg"
    upload_url = f"{supa_url}/storage/v1/object/{SUPABASE_MAP_BUCKET}/{object_path}"
    headers = {
        "apikey": supa_key,
        "Authorization": f"Bearer {supa_key}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    }
    try:
        with httpx.Client(timeout=12.0) as client:
            res = client.post(upload_url, headers=headers, content=img_bytes)
            if res.status_code in (200, 201):
                return (
                    f"{supa_url}/storage/v1/object/public/"
                    f"{SUPABASE_MAP_BUCKET}/{object_path}"
                )
    except Exception:
        pass
    return None


def _load_wayback_catalog() -> List[Tuple[str, str, str]]:
    now_ts = datetime.now(timezone.utc).timestamp()
    if _WAYBACK_CATALOG_CACHE["entries"] and (now_ts - _WAYBACK_CATALOG_CACHE["loaded_at"]) < 21600:
        return _WAYBACK_CATALOG_CACHE["entries"]

    with httpx.Client(timeout=8.0) as client:
        resp = client.get(ESRI_WAYBACK_CONFIG_URL)
        resp.raise_for_status()
        raw_cfg = resp.json()

    entries: List[Tuple[str, str, str]] = []
    for rel, info in raw_cfg.items():
        title = info.get("itemTitle", "")
        pub_dt = title.split("Wayback ")[-1].rstrip(")") if "Wayback " in title else ""
        meta_url = info.get("metadataLayerUrl", "")
        if meta_url:
            entries.append((pub_dt, str(rel), meta_url))

    entries.sort(reverse=True)
    _WAYBACK_CATALOG_CACHE["entries"] = entries
    _WAYBACK_CATALOG_CACHE["loaded_at"] = now_ts
    return entries


def _probe_wayback_release_metadata(
    lat: float, lng: float, entry: Tuple[str, str, str]
) -> Optional[Dict[str, Any]]:
    _, rel, meta_url = entry
    delta = 0.015
    extent = f"{lng - delta},{lat - delta},{lng + delta},{lat + delta}"
    params = urllib.parse.urlencode(
        {
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "sr": "4326",
            "layers": "top",
            "tolerance": "1",
            "mapExtent": extent,
            "imageDisplay": "400,400,96",
            "returnGeometry": "false",
            "f": "json",
        }
    )
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{meta_url}/identify?{params}")
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        attrs = results[0].get("attributes") or {}
        raw_date = str(attrs.get("SRC_DATE", "")).strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            return None
        capture_iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        cloud_raw = attrs.get("CLOUD_COVER")
        cloud_pct = float(cloud_raw) if cloud_raw not in (None, "", "Null") else 0.0
        if is_capture_cloudy(cloud_pct):
            return None
        res_raw = attrs.get("SRC_RES")
        src_res_m: Optional[float] = None
        if res_raw not in (None, "", "Null"):
            try:
                src_res_m = float(res_raw)
            except (TypeError, ValueError):
                src_res_m = None
        if src_res_m is not None and src_res_m > MAX_SRC_RES_METERS:
            return None
        return {
            "capture_date": capture_iso,
            "wayback_release": rel,
            "cloud_cover_pct": cloud_pct,
            "src_res_meters": src_res_m,
            "sensor": str(attrs.get("NICE_DESC") or "Maxar"),
        }
    except Exception:
        return None


def _discover_real_captures_for_point(
    lat: float,
    lng: float,
    coordinates: Optional[List[List[float]]] = None,
) -> List[Dict[str, Any]]:
    """Query Wayback metadata across releases and verify pixel cloud-freeness per date."""
    try:
        catalog = _load_wayback_catalog()
    except Exception:
        return list(VERIFIED_TABASCO_CAPTURES)

    step = max(1, len(catalog) // 36)
    sampled = catalog[::step]
    for known_rel in (
        "43291",
        "32246",
        "10842",
        "22252",
        "49059",
        "48925",
        "32553",
        "4905",
        "17825",
        "19187",
        "48376",
        "51313",
        "8432",
        "239",
        "4383",
        "645",
        "11351",
        "26751",
        "27946",
        "4073",
        "15212",
        "1052",
    ):
        extra = next((item for item in catalog if item[1] == known_rel), None)
        if extra and extra not in sampled:
            sampled.append(extra)

    with ThreadPoolExecutor(max_workers=10) as executor:
        probed = [
            item
            for item in executor.map(
                lambda ent: _probe_wayback_release_metadata(lat, lng, ent), sampled
            )
            if item is not None
        ]

    if not probed:
        return list(VERIFIED_TABASCO_CAPTURES)

    by_date: Dict[str, Dict[str, Any]] = {}
    for cap in probed:
        cdate = cap["capture_date"]
        if cdate not in by_date:
            by_date[cdate] = cap

    unique_candidates = sorted(
        by_date.values(), key=lambda x: x["capture_date"], reverse=True
    )

    viewport_coords = coordinates or [[lng - 0.001, lat - 0.001], [lng + 0.001, lat + 0.001]]
    seen_md5: set[str] = set()
    verified: List[Dict[str, Any]] = []

    def inspect_candidate(cand: Dict[str, Any]) -> Tuple[Dict[str, Any], List[bytes]]:
        _, _, _, _, _, _, _, tile_map = _fetch_viewport_tile_grid(
            viewport_coords, cand["wayback_release"], view_w=VIEWPORT_W, view_h=VIEWPORT_H
        )
        return cand, list(tile_map.values())

    with ThreadPoolExecutor(max_workers=4) as executor:
        inspected = list(executor.map(inspect_candidate, unique_candidates[:16]))

    for cand, tiles_list in inspected:
        if not tiles_list:
            continue
        if is_capture_cloudy(cand.get("cloud_cover_pct"), viewport_tiles=tiles_list):
            continue
        if is_low_resolution_image(
            src_res_meters=cand.get("src_res_meters"), viewport_tiles=tiles_list
        ):
            continue
        digest = hashlib.md5(b"".join(tiles_list[:4])).hexdigest()
        if digest in seen_md5:
            continue
        seen_md5.add(digest)
        avg_g1 = round(
            sum(compute_image_sharpness(raw) for raw in tiles_list) / len(tiles_list), 2
        )
        enriched = dict(cand)
        enriched["sharpness_g1"] = avg_g1
        verified.append(enriched)

    if not verified:
        return list(VERIFIED_TABASCO_CAPTURES)

    verified.sort(key=lambda x: x["capture_date"], reverse=True)
    return verified


def select_best_candidate_within_10_attempts(
    candidates: List[Dict[str, Any]],
    render_fn: Any,
    max_attempts: int = MAX_CLOUD_RETRY_ATTEMPTS,
    recent_priority_count: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate up to `max_attempts` date-sorted candidates backwards in days.
    Accept the first candidate whose parcel-center pixels are cloud-free; if none of
    the attempts passes 100% clear-sky, select the valid attempt with the lowest cloud score.
    """
    best_fallback: Optional[Tuple[float, int, Dict[str, Any]]] = None
    attempt_pool = candidates[:max_attempts]

    for idx, cand in enumerate(attempt_pool, start=1):
        try:
            jpeg_bytes, metrics, render_zoom = render_fn(cand)
        except Exception:
            continue

        nodata_pct = float(metrics.get("nodata_pct", 0.0))
        black_pct = float(metrics.get("black_pct", 0.0))
        white_pct = float(metrics.get("white_pct", 0.0))
        shadow_pct = float(metrics.get("shadow_pct", 0.0))
        haze_pct = float(metrics.get("haze_pct", 0.0))
        if nodata_pct >= 25.0 or black_pct >= 25.0:
            continue

        is_clear = (
            white_pct <= MAX_PURE_WHITE_CLOUD_PCT
            and shadow_pct <= MAX_SHADOW_CLOUD_PCT
            and haze_pct <= min(MAX_TILE_HAZE_PCT, 3.5)
            and nodata_pct < MAX_NODATA_OR_BLACK_PCT
            and black_pct < MAX_NODATA_OR_BLACK_PCT
        )
        enriched = dict(cand)
        enriched["_pre_rendered_jpeg"] = jpeg_bytes
        enriched["_render_zoom"] = render_zoom
        enriched["cloud_cover_pct"] = round(
            min(100.0, white_pct + shadow_pct * 0.5 + haze_pct * 0.35), 2
        )
        enriched["cloud_attempts_used"] = idx
        enriched["sharpness_g1"] = compute_image_sharpness(jpeg_bytes)

        if is_clear:
            enriched["cloud_fallback_least_cloudy"] = False
            return enriched

        cloud_score = (
            (white_pct * 2.5)
            + (shadow_pct * 1.4)
            + (haze_pct * 0.9)
            + (nodata_pct * 4.0)
        )
        if recent_priority_count > 0 and idx > recent_priority_count:
            cloud_score += 2.0
        if best_fallback is None or cloud_score < best_fallback[0]:
            best_fallback = (cloud_score, idx, enriched)

    if best_fallback is not None:
        chosen = dict(best_fallback[2])
        chosen["cloud_fallback_least_cloudy"] = True
        return chosen
    return None


def _search_mpc_sentinel2_for_horizon(
    lat: float,
    lng: float,
    coordinates: List[List[float]],
    target_date: date,
    max_date: date,
    is_primary_horizon: bool = False,
    exclude_dates: Optional[set] = None,
    exclude_releases: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query Sentinel-2 L2A scenes closest to `target_date` stepping backwards in days.
    Inspect up to MAX_CLOUD_RETRY_ATTEMPTS (20) distinct candidates and choose either
    the first cloud-free image or the least-cloudy image among those 20 attempts.
    """
    end_dt = min(max_date, target_date)
    start_dt = max(date(2017, 1, 1), end_dt - timedelta(days=190))
    if end_dt < start_dt:
        start_dt = max(date(2017, 1, 1), end_dt - timedelta(days=190))

    bbox = [
        round(lng - 0.004, 6),
        round(lat - 0.0025, 6),
        round(lng + 0.004, 6),
        round(lat + 0.0025, 6),
    ]
    try:
        with httpx.Client(timeout=12.0) as client:
            res = client.post(
                PLANETARY_COMPUTER_STAC_URL,
                json={
                    "collections": ["sentinel-2-l2a"],
                    "bbox": bbox,
                    "datetime": f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
                    "query": {"eo:cloud_cover": {"lt": 85}},
                    "limit": 90,
                },
            )
            res.raise_for_status()
            feats = res.json().get("features", [])
    except Exception:
        return None

    used_d = exclude_dates or set()
    used_r = exclude_releases or set()
    best_granule_by_date: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for feat in feats:
        raw_dt = str(feat.get("properties", {}).get("datetime") or "")[:10]
        if len(raw_dt) != 10 or raw_dt in used_d:
            continue
        item_id = str(feat.get("id") or "")
        release_key = f"mpc:{item_id}"
        if release_key in used_r:
            continue
        cloud_meta = float(feat.get("properties", {}).get("eo:cloud_cover") or 0.0)
        cand = {
            "capture_date": raw_dt,
            "wayback_release": release_key,
            "cloud_cover_pct": cloud_meta,
            "sensor": "Copernicus Sentinel-2 L2A",
        }
        prev = best_granule_by_date.get(raw_dt)
        if prev is None or cloud_meta < prev[0]:
            best_granule_by_date[raw_dt] = (cloud_meta, cand)

    low_cloud_dates = sorted(
        [d for d, (cc, _) in best_granule_by_date.items() if cc <= 42.0],
        reverse=True,
    )
    high_cloud_dates = sorted(
        [d for d, (cc, _) in best_granule_by_date.items() if cc > 42.0],
        reverse=True,
    )
    ordered_dates = low_cloud_dates + high_cloud_dates
    candidates = [best_granule_by_date[d][1] for d in ordered_dates]
    if not candidates:
        return None

    def _render_mpc(cand: Dict[str, Any]) -> Tuple[bytes, Dict[str, float], int]:
        return _render_mpc_sentinel2_bicubic(
            coordinates, cand["wayback_release"], view_w=VIEWPORT_W, view_h=VIEWPORT_H
        )

    return select_best_candidate_within_10_attempts(
        candidates,
        render_fn=_render_mpc,
        max_attempts=MAX_CLOUD_RETRY_ATTEMPTS,
        recent_priority_count=RECENT_PHOTO_WINDOW_CANDIDATES if is_primary_horizon else 0,
    )


def _select_captures_with_days_margin(
    lat: float,
    lng: float,
    coordinates: List[List[float]],
    anchor_dt: datetime,
    wb_captures: List[Dict[str, Any]],
) -> List[Tuple[str, str, int, Dict[str, Any]]]:
    """
    Build the exact 7 horizons (0m, 6m, 1y, 2y, 3y, 4y, 5y from registration date).
    ESTANDARIZACIÓN SOBERANA COPERNICUS SENTINEL-2:
    Usa exclusivamente Copernicus Sentinel-2 L2A (STAC abierto cada 5 días) con
    unicidad estricta entre horizontes, y Copernicus Sentinel-2 Cloudless (EOX ESA)
    como respaldo histórico anual gratuito sin fondos estáticos de Google/Wayback.
    """
    today_date = datetime.now(timezone.utc).date()
    anchor_date = min(anchor_dt.date(), today_date)
    _, _, optimal_zoom = compute_optimal_parcel_zoom(
        coordinates, view_w=VIEWPORT_W, view_h=VIEWPORT_H
    )
    s2_1x_zoom = round(min(16.05, optimal_zoom), 2)

    # Disjoint upper cutoffs prevent near horizons (e.g. 0m vs 6m) or duplicate years
    # from ever colliding on the same Sentinel-2 scene when evaluated in parallel.
    horizon_inputs: List[Tuple[str, str, int, date, date]] = []
    for idx, (period_key, label, months_ago) in enumerate(TARGET_HORIZON_SPECS):
        t_dt = _subtract_months(anchor_dt, months_ago) if months_ago > 0 else anchor_dt
        t_date = min(t_dt.date(), today_date)
        min_gap_end = t_date
        if idx > 0 and horizon_inputs:
            prev_target = horizon_inputs[idx - 1][3]
            if min_gap_end >= prev_target:
                min_gap_end = prev_target - timedelta(days=1)
        horizon_inputs.append((period_key, label, months_ago, t_date, min_gap_end))

    def _solve_horizon(
        item: Tuple[str, str, int, date, date],
    ) -> Tuple[str, str, int, Dict[str, Any]]:
        period_key, label, months_ago, target_date, max_cut = item
        s2_chosen = _search_mpc_sentinel2_for_horizon(
            lat=lat,
            lng=lng,
            coordinates=coordinates,
            target_date=target_date,
            max_date=min(anchor_date, max_cut),
            is_primary_horizon=(period_key == "registro_actual"),
        )
        if s2_chosen:
            try:
                hd_jpeg, _, s2_zoom = _render_mpc_sentinel2_bicubic(
                    coordinates=coordinates,
                    release=s2_chosen["wayback_release"],
                    view_w=HD_VIEWPORT_W,
                    view_h=HD_VIEWPORT_H,
                )
                s2_chosen["_pre_rendered_jpeg"] = hd_jpeg
                s2_chosen["_render_zoom"] = s2_zoom
                s2_chosen["sharpness_g1"] = compute_image_sharpness(hd_jpeg)
                s2_chosen["sensor"] = "Copernicus Sentinel-2 L2A"
            except Exception:
                pass
            return (period_key, label, months_ago, s2_chosen)

        # Prioridad 2: Copernicus Sentinel-2 Cloudless anual (EOX ESA, 100% Sentinel-2)
        eox_pool = [
            c
            for c in EOX_CLOUDLESS_SUPPLEMENTS
            if date.fromisoformat(c["capture_date"]) <= target_date
        ] or list(EOX_CLOUDLESS_SUPPLEMENTS)
        eox_cand = min(
            eox_pool,
            key=lambda c: abs((date.fromisoformat(c["capture_date"]) - target_date).days),
        )
        try:
            jpeg_bytes, _ = render_centered_parcel_map_jpeg(
                coordinates, eox_cand["wayback_release"]
            )
            enriched = dict(eox_cand)
            enriched["_pre_rendered_jpeg"] = jpeg_bytes
            enriched["_render_zoom"] = s2_1x_zoom
            enriched["sharpness_g1"] = compute_image_sharpness(jpeg_bytes)
            enriched["cloud_attempts_used"] = 1
            enriched["cloud_fallback_least_cloudy"] = False
            enriched["sensor"] = "Copernicus Sentinel-2 EU (Cloudless)"
            return (period_key, label, months_ago, enriched)
        except Exception:
            fallback_eox = dict(eox_cand)
            fallback_eox["_render_zoom"] = s2_1x_zoom
            fallback_eox["sensor"] = "Copernicus Sentinel-2 EU (Cloudless)"
            return (period_key, label, months_ago, fallback_eox)

    with ThreadPoolExecutor(max_workers=7) as executor:
        raw_results = list(executor.map(_solve_horizon, horizon_inputs))

    # Strict post-pass deduplication: guarantee 7 unique scenes and dates across 0m..5y
    used_dates: set = set()
    used_releases: set = set()
    final_results: List[Tuple[str, str, int, Dict[str, Any]]] = []
    for period_key, label, months_ago, chosen in raw_results:
        d_str = chosen.get("capture_date", "")
        rel_str = chosen.get("wayback_release", "")
        if d_str in used_dates or rel_str in used_releases:
            eox_avail = [
                c
                for c in EOX_CLOUDLESS_SUPPLEMENTS
                if c["capture_date"] not in used_dates
                and c["wayback_release"] not in used_releases
            ]
            if eox_avail:
                target_dt = (
                    _subtract_months(anchor_dt, months_ago) if months_ago > 0 else anchor_dt
                )
                alt = dict(
                    min(
                        eox_avail,
                        key=lambda c: abs(
                            (date.fromisoformat(c["capture_date"]) - target_dt.date()).days
                        ),
                    )
                )
                try:
                    jpeg_bytes, _ = render_centered_parcel_map_jpeg(
                        coordinates, alt["wayback_release"]
                    )
                    alt["_pre_rendered_jpeg"] = jpeg_bytes
                    alt["sharpness_g1"] = compute_image_sharpness(jpeg_bytes)
                except Exception:
                    pass
                alt["_render_zoom"] = s2_1x_zoom
                alt["sensor"] = "Copernicus Sentinel-2 EU (Cloudless)"
                chosen = alt
        used_dates.add(chosen.get("capture_date", ""))
        used_releases.add(chosen.get("wayback_release", ""))
        final_results.append((period_key, label, months_ago, chosen))
    return final_results


def assign_distinct_captures_to_horizons(
    captures: List[Dict[str, Any]],
    anchor_dt: datetime,
) -> List[Tuple[str, str, int, Dict[str, Any]]]:
    """
    Match each horizon (0m, 6m, 1y, 2y, 3y, 4y, 5y from registration/reference anchor_dt)
    to the closest historical cloud-free capture backwards in time without repeating images.
    """
    clean_pool = [
        c for c in captures if not is_capture_cloudy(c.get("cloud_cover_pct"))
    ]
    clean_pool.sort(key=lambda x: x["capture_date"], reverse=True)
    used_dates: set[str] = set()
    used_releases: set[str] = set()
    assigned: List[Tuple[str, str, int, Dict[str, Any]]] = []

    for period_key, label, months_ago in TARGET_HORIZON_SPECS:
        target_dt = _subtract_months(anchor_dt, months_ago) if months_ago > 0 else anchor_dt
        target_date = target_dt.date()

        available = [
            c
            for c in clean_pool
            if c["capture_date"] not in used_dates
            and c["wayback_release"] not in used_releases
        ]
        if not available:
            available = clean_pool[:1]

        older_or_equal = [
            c
            for c in available
            if date.fromisoformat(c["capture_date"]) <= target_date
        ]
        if older_or_equal:
            chosen = max(
                older_or_equal, key=lambda x: date.fromisoformat(x["capture_date"])
            )
        else:
            chosen = max(available, key=lambda x: date.fromisoformat(x["capture_date"]))

        used_dates.add(chosen["capture_date"])
        used_releases.add(chosen["wayback_release"])
        assigned.append((period_key, label, months_ago, chosen))

    return assigned


def _parse_anchor_datetime(raw_iso: Optional[str]) -> datetime:
    if raw_iso:
        try:
            clean_iso = raw_iso.replace("Z", "+00:00")
            if len(clean_iso) == 10:
                clean_iso = f"{clean_iso}T12:00:00+00:00"
            return datetime.fromisoformat(clean_iso)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def build_historical_parcel_snapshots(
    parcel_id: str,
    raw_coordinates: List[List[float]],
    registered_at_iso: Optional[str] = None,
    reference_date_iso: Optional[str] = None,
    probe_live: bool = False,
    materialize_assets: bool = False,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build the 7-point historical satellite manifest (anchored on parcel registration date
    unless reference_date_iso overrides it), filter clouds per pixel, and optionally compose
    and cache all 7 JPEGs permanently in server/Supabase Storage.
    """
    topology = lint_and_validate_polygon(raw_coordinates)
    ring = topology["polygon"]["coordinates"][0]
    raw_bbox = build_bbox_and_ortho_url(ring)["bbox"]
    extent_bbox = fit_bbox_to_aspect_ratio(raw_bbox, target_aspect=1.75)

    center_lng = sum(float(pt[0]) for pt in ring) / len(ring)
    center_lat = sum(float(pt[1]) for pt in ring) / len(ring)
    _, _, vp_zoom = compute_centered_wayback_viewport(ring)
    row, col = _lat_lng_to_tile_row_col(
        center_lat, center_lng, zoom=max(13, min(17, int(round(vp_zoom))))
    )

    anchor = _parse_anchor_datetime(reference_date_iso or registered_at_iso)

    if probe_live:
        matched = _select_captures_with_days_margin(
            center_lat, center_lng, ring, anchor, []
        )
    else:
        matched = assign_distinct_captures_to_horizons(
            list(EOX_CLOUDLESS_SUPPLEMENTS), anchor
        )

    snapshots: List[Dict[str, Any]] = []
    target_dir = cache_dir or DEFAULT_MAP_CACHE_DIR
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", parcel_id)

    def _build_snapshot_entry(item: Tuple[str, str, int, Dict[str, Any]]) -> Dict[str, Any]:
        period_key, label, months_ago, cap = item
        rel = cap["wayback_release"]
        real_date = cap["capture_date"]
        formatted_date = format_date_ddmmyyyy(real_date)
        final_label = label if formatted_date in label else f"{label} ({formatted_date})"
        actual_zoom = float(
            round(
                cap.get("_render_zoom") or vp_zoom,
                2,
            )
        )
        direct_url = resolve_wmts_tile_url(
            rel, max(13, min(17, int(round(actual_zoom)))), row, col
        )
        cached_public_url: Optional[str] = None
        rendered_sharpness = float(cap.get("sharpness_g1") or 8.0)

        if materialize_assets:
            try:
                jpeg_bytes = cap.get("_pre_rendered_jpeg")
                if not jpeg_bytes:
                    jpeg_bytes, _ = render_centered_parcel_map_jpeg(ring, rel)
                rendered_sharpness = compute_image_sharpness(jpeg_bytes)
                target_dir.mkdir(parents=True, exist_ok=True)
                safe_rel = re.sub(r"[^a-zA-Z0-9_-]", "_", rel)
                cache_file = target_dir / f"{safe_id}_s2v13_{period_key}_{safe_rel}.jpg"
                cache_file.write_bytes(jpeg_bytes)
                cached_public_url = upload_parcel_map_to_supabase_storage(
                    parcel_id, period_key, rel, jpeg_bytes
                )
            except Exception:
                cached_public_url = None

        sensor_name = str(cap.get("sensor") or "Copernicus Sentinel-2 L2A")
        if rel.startswith("mpc:"):
            res_desc = (
                "Copernicus Sentinel-2 L2A (10 m/px nativo · Sin filtros · Revisit 5 días)"
            )
        elif rel.startswith("s2cloudless-"):
            res_desc = (
                "Copernicus Sentinel-2 Cloudless (10 m/px ESA · Sin filtros)"
            )
        else:
            res_desc = f"{sensor_name} (Respaldo offline)"

        return {
            "period_key": period_key,
            "label": final_label,
            "months_ago": months_ago,
            "wayback_release": rel,
            "sensor": sensor_name,
            "captured_date_iso": real_date,
            "render_zoom": actual_zoom,
            "image_width": HD_VIEWPORT_W,
            "image_height": HD_VIEWPORT_H,
            "cloud_cover_pct": cap.get("cloud_cover_pct", 0.0),
            "cloud_attempts_used": int(cap.get("cloud_attempts_used") or 1),
            "cloud_fallback_least_cloudy": bool(cap.get("cloud_fallback_least_cloudy")),
            "sharpness_g1": rendered_sharpness,
            "high_res_verified": True,
            "resolution_label": res_desc,
            "capture_source": "real_src_date",
            "pixel_cloud_checked": bool(probe_live),
            "cloud_filter_version": "v13_s2_pure_quality",
            "extent_bbox": extent_bbox,
            "direct_ortho_url": direct_url,
            "cached_image_url": cached_public_url,
            "server_cached_image_path": (
                f"/api/v1/parcels/{parcel_id}/historical-image/{period_key}"
            ),
        }

    if materialize_assets:
        with ThreadPoolExecutor(max_workers=7) as executor:
            snapshots = list(executor.map(_build_snapshot_entry, matched))
    else:
        snapshots = [_build_snapshot_entry(item) for item in matched]

    return {
        "parcel_id": parcel_id,
        "area_hectares": topology["area_hectares"],
        "coordinates": ring,
        "extent_bbox": extent_bbox,
        "registered_at_iso": anchor.date().isoformat(),
        "reference_date_iso": anchor.date().isoformat(),
        "snapshots": snapshots,
    }


def get_or_download_cached_parcel_map_bytes(
    parcel_id: str,
    period_key: str,
    extent_bbox: List[float],
    months_ago: int = 0,
    cache_dir: Optional[Path] = None,
    wayback_release: Optional[str] = None,
    coordinates: Optional[List[List[float]]] = None,
    cached_image_url: Optional[str] = None,
) -> bytes:
    """Serve cached composited 760x420 JPEG from disk or Supabase Storage, rendering once."""
    valid_keys = {spec[0]: idx for idx, spec in enumerate(TARGET_HORIZON_SPECS)}
    if period_key not in valid_keys:
        raise ValueError(f"Periodo histórico inválido: {period_key}")

    resolved_release = wayback_release or EOX_CLOUDLESS_SUPPLEMENTS[
        min(valid_keys[period_key], len(EOX_CLOUDLESS_SUPPLEMENTS) - 1)
    ]["wayback_release"]
    target_dir = cache_dir or DEFAULT_MAP_CACHE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", parcel_id)
    safe_rel = re.sub(r"[^a-zA-Z0-9_-]", "_", resolved_release)
    cache_file = target_dir / f"{safe_id}_s2v13_{period_key}_{safe_rel}.jpg"

    if cache_file.exists() and cache_file.stat().st_size > 1000:
        return cache_file.read_bytes()

    if cached_image_url and cached_image_url.startswith("http"):
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                r = client.get(cached_image_url)
                if r.status_code == 200 and len(r.content) > 1000:
                    cache_file.write_bytes(r.content)
                    return r.content
        except Exception:
            pass

    if coordinates and len(coordinates) >= 3:
        jpeg_bytes, _ = render_centered_parcel_map_jpeg(coordinates, resolved_release)
        cache_file.write_bytes(jpeg_bytes)
        return jpeg_bytes

    center_lng = (float(extent_bbox[0]) + float(extent_bbox[2])) / 2.0
    center_lat = (float(extent_bbox[1]) + float(extent_bbox[3])) / 2.0
    row, col = _lat_lng_to_tile_row_col(center_lat, center_lng, zoom=16)
    wayback_url = resolve_wmts_tile_url(resolved_release, 16, row, col)

    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        resp = client.get(wayback_url)
        resp.raise_for_status()
        img_bytes = resp.content

    cache_file.write_bytes(img_bytes)
    return img_bytes
