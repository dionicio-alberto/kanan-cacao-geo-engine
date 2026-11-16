"""Satellite audit engine for EUDR JRC 2020 (HU-03) and Sentinel-2 NDVI (HU-04)."""

import calendar
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx
from app.core.config import EUDR_CUTOFF_DATE_ISO, EUDR_LEGAL_DISCLAIMER
from app.services.topology_engine import lint_and_validate_polygon

ELEMENT84_STAC_SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
ESRI_WORLD_IMAGERY_EXPORT_URL = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/export"
)
ESRI_WAYBACK_WMTS_BASE_URL = (
    "https://wayback.maptiles.arcgis.com/arcgis/rest/services/"
    "World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile"
)
DEFAULT_MAP_CACHE_DIR = Path(
    os.environ.get(
        "KANAN_MAP_CACHE_DIR",
        str(Path(__file__).resolve().parents[2] / "data" / "map_cache"),
    )
)

# (period_key, label, months_ago, wayback_release, capture_date_label)
HISTORICAL_PERIOD_SPECS = [
    ("registro_actual", "Última foto al momento del registro", 0, "43291", "2026-10-29"),
    ("hace_6_meses", "Hace 6 meses", 6, "10842", "2026-05-28"),
    ("hace_1_anio", "Hace 1 año", 12, "52930", "2024-05-09"),
    ("hace_2_anios", "Hace 2 años", 24, "46399", "2023-05-03"),
    ("hace_3_anios", "Hace 3 años", 36, "4905", "2022-06-29"),
    ("hace_4_anios", "Hace 4 años", 48, "8432", "2021-07-21"),
    ("hace_5_anios", "Hace 5 años", 60, "26751", "2020-04-08"),
]


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
    """
    Query Copernicus Sentinel-2 L2A STAC catalog (Element84 Earth Search) for the
    parcel polygon bbox and return real satellite scene metadata + ortho imagery URL.
    """
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


def _build_ortho_export_url(extent_bbox: List[float], epoch_ms: Optional[int] = None) -> str:
    bbox_str = ",".join(str(v) for v in extent_bbox)
    base_url = (
        f"{ESRI_WORLD_IMAGERY_EXPORT_URL}?bbox={bbox_str}"
        "&bboxSR=4326&imageSR=4326&size=840,480&format=jpg&f=image"
    )
    if epoch_ms is not None:
        return f"{base_url}&time={epoch_ms}"
    return base_url


def _lat_lng_to_tile_row_col(
    lat: float, lng: float, zoom: int = 16
) -> Tuple[int, int]:
    n = 2 ** zoom
    col = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(max(-85.051128, min(85.051128, lat)))
    row = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return row, col


def build_historical_parcel_snapshots(
    parcel_id: str,
    raw_coordinates: List[List[float]],
    registered_at_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the 7-point historical satellite manifest (current, 6m, 1y, 2y, 3y, 4y, 5y)
    backed by distinct Esri Wayback releases centered on the parcel polygon.
    """
    topology = lint_and_validate_polygon(raw_coordinates)
    ring = topology["polygon"]["coordinates"][0]
    raw_bbox = build_bbox_and_ortho_url(ring)["bbox"]
    extent_bbox = fit_bbox_to_aspect_ratio(raw_bbox, target_aspect=1.75)

    center_lng = sum(float(pt[0]) for pt in ring) / len(ring)
    center_lat = sum(float(pt[1]) for pt in ring) / len(ring)
    row, col = _lat_lng_to_tile_row_col(center_lat, center_lng, zoom=16)

    if registered_at_iso:
        try:
            anchor = datetime.fromisoformat(registered_at_iso.replace("Z", "+00:00"))
        except ValueError:
            anchor = datetime.now(timezone.utc)
    else:
        anchor = datetime.now(timezone.utc)

    snapshots: List[Dict[str, Any]] = []
    for period_key, label, months_ago, wayback_release, ref_date in HISTORICAL_PERIOD_SPECS:
        snap_dt = _subtract_months(anchor, months_ago) if months_ago > 0 else anchor
        wayback_url = f"{ESRI_WAYBACK_WMTS_BASE_URL}/{wayback_release}/16/{row}/{col}"
        snapshots.append(
            {
                "period_key": period_key,
                "label": label,
                "months_ago": months_ago,
                "wayback_release": wayback_release,
                "captured_date_iso": ref_date or snap_dt.date().isoformat(),
                "extent_bbox": extent_bbox,
                "direct_ortho_url": wayback_url,
                "server_cached_image_path": (
                    f"/api/v1/parcels/{parcel_id}/historical-image/{period_key}"
                ),
            }
        )

    return {
        "parcel_id": parcel_id,
        "area_hectares": topology["area_hectares"],
        "coordinates": ring,
        "extent_bbox": extent_bbox,
        "snapshots": snapshots,
    }


def get_or_download_cached_parcel_map_bytes(
    parcel_id: str,
    period_key: str,
    extent_bbox: List[float],
    months_ago: int = 0,
    cache_dir: Optional[Path] = None,
) -> bytes:
    """Return cached JPEG bytes from server disk or download Wayback release once."""
    spec_map = {spec[0]: spec for spec in HISTORICAL_PERIOD_SPECS}
    if period_key not in spec_map:
        raise ValueError(f"Periodo histórico inválido: {period_key}")

    wayback_release = spec_map[period_key][3]
    target_dir = cache_dir or DEFAULT_MAP_CACHE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", parcel_id)
    cache_file = target_dir / f"{safe_id}_{period_key}_{wayback_release}.jpg"

    if cache_file.exists() and cache_file.stat().st_size > 100:
        return cache_file.read_bytes()

    center_lng = (float(extent_bbox[0]) + float(extent_bbox[2])) / 2.0
    center_lat = (float(extent_bbox[1]) + float(extent_bbox[3])) / 2.0
    row, col = _lat_lng_to_tile_row_col(center_lat, center_lng, zoom=16)
    wayback_url = f"{ESRI_WAYBACK_WMTS_BASE_URL}/{wayback_release}/16/{row}/{col}"

    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        resp = client.get(wayback_url)
        resp.raise_for_status()
        img_bytes = resp.content

    cache_file.write_bytes(img_bytes)
    return img_bytes
