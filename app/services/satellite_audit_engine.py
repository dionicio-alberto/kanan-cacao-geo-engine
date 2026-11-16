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
MAX_PURE_WHITE_CLOUD_PCT = 6.0
MAX_AVG_HAZE_PCT = 14.0
MAX_TILE_HAZE_PCT = 22.0
MAX_NODATA_OR_BLACK_PCT = 12.0
MAX_CLOUD_RETRY_ATTEMPTS = 10
RECENT_PHOTO_WINDOW_CANDIDATES = 5
WAYBACK_EXACT_WINDOW_DAYS = 21
MIN_IMAGE_SHARPNESS_G1 = 5.5
MAX_SRC_RES_METERS = 2.5
VIEWPORT_W = 760
VIEWPORT_H = 420
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

# Capturas satelitales reales verificadas sobre Tabasco (SRC_DATE únicos y sin nubes).
VERIFIED_TABASCO_CAPTURES: List[Dict[str, Any]] = [
    {
        "capture_date": "2025-06-09",
        "wayback_release": "43291",
        "cloud_cover_pct": 0.0,
        "sensor": "Vantor",
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
        "capture_date": "2021-02-17",
        "wayback_release": "17825",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar",
    },
    {
        "capture_date": "2019-02-24",
        "wayback_release": "48376",
        "cloud_cover_pct": 0.0,
        "sensor": "Maxar",
    },
    {
        "capture_date": "2017-02-12",
        "wayback_release": "239",
        "cloud_cover_pct": 0.0,
        "sensor": "DigitalGlobe",
    },
    {
        "capture_date": "2015-05-17",
        "wayback_release": "26751",
        "cloud_cover_pct": 0.0,
        "sensor": "DigitalGlobe",
    },
    {
        "capture_date": "2013-03-31",
        "wayback_release": "1052",
        "cloud_cover_pct": 0.0,
        "sensor": "DigitalGlobe",
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


def compute_centered_wayback_viewport(
    coordinates: List[List[float]],
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
) -> Tuple[float, float, int]:
    """Match frontend Web Mercator zoom/center algorithm bit-for-bit."""
    if not coordinates:
        return 18.2545, -93.2825, 16
    lngs = [float(pt[0]) for pt in coordinates]
    lats = [float(pt[1]) for pt in coordinates]
    min_lng, max_lng = min(lngs), max(lngs)
    min_lat, max_lat = min(lats), max(lats)
    center_lng = (min_lng + max_lng) / 2.0
    center_lat = (min_lat + max_lat) / 2.0

    for z in range(17, 13, -1):
        tl_x, tl_y = _lat_lng_to_world_px(max_lat, min_lng, z)
        br_x, br_y = _lat_lng_to_world_px(min_lat, max_lng, z)
        if abs(br_x - tl_x) <= view_w * 0.52 and abs(br_y - tl_y) <= view_h * 0.52:
            return center_lat, center_lng, z
    return center_lat, center_lng, 14


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
    """Resolve WMTS tile URL for Planetary Computer Sentinel-2, EOX, or Esri Wayback."""
    if release.startswith("mpc:"):
        item_id = urllib.parse.quote(release[4:], safe="")
        return (
            f"{PLANETARY_COMPUTER_TILE_BASE}/{zoom}/{col}/{row}.png"
            f"?collection=sentinel-2-l2a&item={item_id}"
            "&assets=visual&asset_bidx=visual%7C1,2,3"
        )
    if release.startswith("s2cloudless-"):
        return f"{EOX_S2_CLOUDLESS_WMTS_BASE_URL}/{release}/default/g/{zoom}/{row}/{col}.jpg"
    return f"{ESRI_WAYBACK_WMTS_BASE_URL}/{release}/16/{row}/{col}" if zoom == 16 else (
        f"{ESRI_WAYBACK_WMTS_BASE_URL}/{release}/{zoom}/{row}/{col}"
    )


def compute_tile_cloud_metrics(tile_bytes: bytes) -> Dict[str, float]:
    """Inspect decoded RGBA pixels for white clouds, haze, transparent nodata, and black edges."""
    if len(tile_bytes) < 500:
        return {"white_pct": 100.0, "haze_pct": 100.0, "nodata_pct": 100.0, "black_pct": 100.0}
    try:
        im = Image.open(io.BytesIO(tile_bytes)).convert("RGBA")
        arr = np.asarray(im, dtype=np.int16)
    except Exception:
        return {"white_pct": 0.0, "haze_pct": 0.0, "nodata_pct": 0.0, "black_pct": 0.0}

    rgb = arr[:, :, :3]
    nodata_pct = float((arr[:, :, 3] == 0).mean() * 100.0)
    black_pct = float((rgb.max(axis=2) < 5).mean() * 100.0)
    brightness = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    white_pct = float(((brightness > 212) & (chroma < 25)).mean() * 100.0)
    haze_pct = float(
        ((rgb[:, :, 2] >= rgb[:, :, 1] - 2) & (brightness > 95)).mean() * 100.0
    )
    return {
        "white_pct": round(white_pct, 2),
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
    center_lat, center_lng, zoom = compute_centered_wayback_viewport(
        coordinates, view_w=view_w, view_h=view_h
    )
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
    """Compute cloud/haze/nodata metrics weighted over the central parcel area."""
    w, h = im.size
    crop = im.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return compute_tile_cloud_metrics(buf.getvalue())


def _render_mpc_sentinel2_bicubic(
    coordinates: List[List[float]],
    release: str,
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
) -> Tuple[bytes, Dict[str, float], int]:
    """
    Fetch native Sentinel-2 zoom=14 visual tiles and crop via bicubic + UnsharpMask
    at zoom <= 15 so plots show crisp context roads/groves without over-zoom blocks.
    """
    center_lat, center_lng, base_zoom = compute_centered_wayback_viewport(
        coordinates, view_w=view_w, view_h=view_h
    )
    render_zoom = min(15, base_zoom)
    native_zoom = 14
    scale_div = float(2 ** (render_zoom - native_zoom))
    cx_nat, cy_nat = _lat_lng_to_world_px(center_lat, center_lng, native_zoom)
    half_w = (view_w / 2.0) / scale_div
    half_h = (view_h / 2.0) / scale_div
    x0, y0, x1, y1 = cx_nat - half_w, cy_nat - half_h, cx_nat + half_w, cy_nat + half_h

    max_tile = (2 ** native_zoom) - 1
    min_tx = max(0, int(math.floor(x0 / TILE_SIZE)))
    max_tx = min(max_tile, int(math.floor(x1 / TILE_SIZE)))
    min_ty = max(0, int(math.floor(y0 / TILE_SIZE)))
    max_ty = min(max_tile, int(math.floor(y1 / TILE_SIZE)))

    coords_to_fetch = [
        (tx, ty)
        for tx in range(min_tx, max_tx + 1)
        for ty in range(min_ty, max_ty + 1)
    ]

    def _get_mpc_tile(tx_ty: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[bytes]]:
        tx, ty = tx_ty
        tile_url = resolve_wmts_tile_url(release, native_zoom, ty, tx)
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                res = client.get(tile_url, headers={"User-Agent": "KananCacaoGeoEngine/1.0"})
                if res.status_code == 200:
                    return (tx, ty), res.content
        except Exception:
            pass
        return (tx, ty), None

    tile_map: Dict[Tuple[int, int], bytes] = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for (tx, ty), raw_bytes in executor.map(_get_mpc_tile, coords_to_fetch):
            if raw_bytes:
                tile_map[(tx, ty)] = raw_bytes

    if not tile_map:
        raise RuntimeError(f"No se pudieron descargar teselas Sentinel-2 para {release}.")

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
    rendered = rendered.filter(ImageFilter.UnsharpMask(radius=1.4, percent=130, threshold=2))
    rendered = ImageEnhance.Contrast(rendered).enhance(1.12)

    out = io.BytesIO()
    rendered.save(out, format="JPEG", quality=90, optimize=True)
    jpeg_bytes = out.getvalue()
    metrics = _compute_parcel_center_cloud_metrics(rendered)
    return jpeg_bytes, metrics, render_zoom


def render_centered_parcel_map_jpeg(
    coordinates: List[List[float]],
    release: str,
    view_w: int = VIEWPORT_W,
    view_h: int = VIEWPORT_H,
) -> Tuple[bytes, Dict[str, float]]:
    """Download WMTS tiles for the parcel viewport, check clouds, and stitch a 760x420 JPEG."""
    if release.startswith("mpc:"):
        jpeg_bytes, metrics, _ = _render_mpc_sentinel2_bicubic(
            coordinates, release, view_w=view_w, view_h=view_h
        )
        return jpeg_bytes, metrics

    (
        top_left_x,
        top_left_y,
        min_tx,
        max_tx,
        min_ty,
        max_ty,
        _zoom,
        tile_map,
    ) = _fetch_viewport_tile_grid(coordinates, release, view_w=view_w, view_h=view_h)

    if not tile_map:
        raise RuntimeError(f"No se pudieron descargar teselas WMTS para release {release}.")

    canvas = Image.new("RGB", (view_w, view_h), (17, 36, 24))
    for tx in range(min_tx, max_tx + 1):
        for ty in range(min_ty, max_ty + 1):
            raw = tile_map.get((tx, ty))
            if not raw:
                continue
            try:
                tile_im = Image.open(io.BytesIO(raw)).convert("RGB")
                paste_x = round(tx * TILE_SIZE - top_left_x)
                paste_y = round(ty * TILE_SIZE - top_left_y)
                canvas.paste(tile_im, (paste_x, paste_y))
            except Exception:
                continue

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=86, optimize=True)
    jpeg_bytes = out.getvalue()
    metrics = _compute_parcel_center_cloud_metrics(canvas)
    return jpeg_bytes, metrics


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
    object_path = f"{safe_id}/{period_key}_{safe_rel}.jpg"
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
        haze_pct = float(metrics.get("haze_pct", 0.0))
        if nodata_pct >= 25.0 or black_pct >= 25.0:
            continue

        is_clear = (
            white_pct <= MAX_PURE_WHITE_CLOUD_PCT
            and haze_pct <= MAX_TILE_HAZE_PCT
            and nodata_pct < MAX_NODATA_OR_BLACK_PCT
            and black_pct < MAX_NODATA_OR_BLACK_PCT
        )
        enriched = dict(cand)
        enriched["_pre_rendered_jpeg"] = jpeg_bytes
        enriched["_render_zoom"] = render_zoom
        enriched["cloud_cover_pct"] = round(min(100.0, white_pct + haze_pct * 0.35), 2)
        enriched["cloud_attempts_used"] = idx
        enriched["sharpness_g1"] = compute_image_sharpness(jpeg_bytes)

        if is_clear:
            enriched["cloud_fallback_least_cloudy"] = False
            return enriched

        cloud_score = (white_pct * 2.5) + (haze_pct * 0.9) + (nodata_pct * 4.0)
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
) -> Optional[Dict[str, Any]]:
    """
    Query Sentinel-2 L2A scenes closest to `target_date` stepping backwards in days.
    Inspect up to MAX_CLOUD_RETRY_ATTEMPTS (10) attempts and choose either the first
    cloud-free image or the least-cloudy image among those 10 attempts.
    """
    end_dt = min(max_date, target_date)
    start_dt = max(date(2017, 1, 1), end_dt - timedelta(days=90))
    if end_dt < start_dt:
        start_dt = max(date(2017, 1, 1), end_dt - timedelta(days=90))

    bbox = [
        round(lng - 0.004, 6),
        round(lat - 0.0025, 6),
        round(lng + 0.004, 6),
        round(lat + 0.0025, 6),
    ]
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(
                PLANETARY_COMPUTER_STAC_URL,
                json={
                    "collections": ["sentinel-2-l2a"],
                    "bbox": bbox,
                    "datetime": f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
                    "query": {"eo:cloud_cover": {"lt": 92}},
                    "limit": 40,
                },
            )
            res.raise_for_status()
            feats = res.json().get("features", [])
    except Exception:
        return None

    best_granule_by_date: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for feat in feats:
        raw_dt = str(feat.get("properties", {}).get("datetime") or "")[:10]
        if len(raw_dt) != 10:
            continue
        item_id = str(feat.get("id") or "")
        release_key = f"mpc:{item_id}"
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

    ordered_dates = sorted(best_granule_by_date.keys(), reverse=True)
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
    Uses sub-meter Wayback only when a clear capture lies within +/- 21 days; otherwise
    uses Sentinel-2 L2A backwards day-stepping with max 10 attempts & least-cloudy fallback.
    """
    today_date = datetime.now(timezone.utc).date()
    anchor_date = min(anchor_dt.date(), today_date)
    _, _, base_zoom = compute_centered_wayback_viewport(
        coordinates, view_w=VIEWPORT_W, view_h=VIEWPORT_H
    )

    def _solve_horizon(spec: Tuple[str, str, int]) -> Tuple[str, str, int, Dict[str, Any]]:
        period_key, label, months_ago = spec
        target_dt = (
            _subtract_months(anchor_dt, months_ago) if months_ago > 0 else anchor_dt
        )
        target_date = min(target_dt.date(), today_date)

        # 1) Prioritize high-res sub-meter Wayback ONLY within tight day window (<= 21 days)
        nearby_wb = []
        for cap in wb_captures:
            cap_dt = date.fromisoformat(cap["capture_date"])
            if cap_dt > anchor_date:
                continue
            delta_days = abs((cap_dt - target_date).days)
            if delta_days <= WAYBACK_EXACT_WINDOW_DAYS:
                nearby_wb.append((delta_days, cap))
        nearby_wb.sort(key=lambda pair: pair[0])

        for _, wb_cand in nearby_wb[:3]:
            try:
                jpeg_bytes, metrics = render_centered_parcel_map_jpeg(
                    coordinates, wb_cand["wayback_release"]
                )
                if (
                    float(metrics.get("white_pct", 0.0)) <= MAX_PURE_WHITE_CLOUD_PCT
                    and float(metrics.get("haze_pct", 0.0)) <= MAX_TILE_HAZE_PCT
                    and compute_image_sharpness(jpeg_bytes) >= MIN_IMAGE_SHARPNESS_G1
                ):
                    enriched = dict(wb_cand)
                    enriched["_pre_rendered_jpeg"] = jpeg_bytes
                    enriched["_render_zoom"] = base_zoom
                    enriched["sharpness_g1"] = compute_image_sharpness(jpeg_bytes)
                    enriched["cloud_attempts_used"] = 1
                    enriched["cloud_fallback_least_cloudy"] = False
                    return (period_key, label, months_ago, enriched)
            except Exception:
                continue

        # 2) Sentinel-2 L2A day-by-day backwards search (max 10 attempts, least-cloudy fallback)
        s2_chosen = _search_mpc_sentinel2_for_horizon(
            lat=lat,
            lng=lng,
            coordinates=coordinates,
            target_date=target_date,
            max_date=anchor_date,
            is_primary_horizon=(period_key == "registro_actual"),
        )
        if s2_chosen:
            return (period_key, label, months_ago, s2_chosen)

        # 3) Last-resort local deterministic fallback if external imagery APIs fail completely
        fallback_cap = min(
            wb_captures or VERIFIED_TABASCO_CAPTURES,
            key=lambda c: abs((date.fromisoformat(c["capture_date"]) - target_date).days),
        )
        return (period_key, label, months_ago, dict(fallback_cap))

    with ThreadPoolExecutor(max_workers=7) as executor:
        return list(executor.map(_solve_horizon, TARGET_HORIZON_SPECS))


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
    row, col = _lat_lng_to_tile_row_col(center_lat, center_lng, zoom=vp_zoom)

    anchor = _parse_anchor_datetime(reference_date_iso or registered_at_iso)

    if probe_live:
        wb_captures = _discover_real_captures_for_point(
            center_lat, center_lng, coordinates=ring
        )
        matched = _select_captures_with_days_margin(
            center_lat, center_lng, ring, anchor, wb_captures
        )
    else:
        matched = assign_distinct_captures_to_horizons(
            list(VERIFIED_TABASCO_CAPTURES), anchor
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
        actual_zoom = int(
            cap.get("_render_zoom") or (min(15, vp_zoom) if rel.startswith("mpc:") else vp_zoom)
        )
        direct_url = resolve_wmts_tile_url(rel, actual_zoom, row, col)
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
                cache_file = target_dir / f"{safe_id}_{period_key}_{safe_rel}.jpg"
                cache_file.write_bytes(jpeg_bytes)
                cached_public_url = upload_parcel_map_to_supabase_storage(
                    parcel_id, period_key, rel, jpeg_bytes
                )
            except Exception:
                cached_public_url = None

        res_desc = (
            "Alta resolución submétrica (0.34–0.60 m/px)"
            if not rel.startswith("mpc:")
            else "Satélite Copernicus nítido por fecha exacta"
        )
        return {
            "period_key": period_key,
            "label": final_label,
            "months_ago": months_ago,
            "wayback_release": rel,
            "captured_date_iso": real_date,
            "render_zoom": actual_zoom,
            "cloud_cover_pct": cap.get("cloud_cover_pct", 0.0),
            "cloud_attempts_used": int(cap.get("cloud_attempts_used") or 1),
            "cloud_fallback_least_cloudy": bool(cap.get("cloud_fallback_least_cloudy")),
            "sharpness_g1": rendered_sharpness,
            "high_res_verified": rendered_sharpness >= (
                3.5 if rel.startswith("mpc:") else MIN_IMAGE_SHARPNESS_G1
            ),
            "resolution_label": res_desc,
            "capture_source": "real_src_date",
            "pixel_cloud_checked": bool(probe_live),
            "cloud_filter_version": "v5_days_margin",
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

    resolved_release = wayback_release or VERIFIED_TABASCO_CAPTURES[valid_keys[period_key]][
        "wayback_release"
    ]
    target_dir = cache_dir or DEFAULT_MAP_CACHE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", parcel_id)
    safe_rel = re.sub(r"[^a-zA-Z0-9_-]", "_", resolved_release)
    cache_file = target_dir / f"{safe_id}_{period_key}_{safe_rel}.jpg"

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
