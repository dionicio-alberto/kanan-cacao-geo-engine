"""Server-side topological linter and GeoJSON validator (HU-01, HU-02)."""

import json
import math
from typing import Any, Dict, List, Tuple
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid
from app.core.config import (
    EUDR_MANDATORY_POLYGON_THRESHOLD_HA,
    MAX_GEOJSON_SIZE_BYTES,
)


def _are_coords_equal(a: List[float], b: List[float]) -> bool:
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def _calculate_hectares_wgs84(poly: Polygon) -> float:
    """Approximates geodesic area in hectares for WGS84 coordinates."""
    centroid_lat_rad = math.radians(poly.centroid.y)
    meters_per_deg_lat = 111132.92
    meters_per_deg_lng = 111412.84 * math.cos(centroid_lat_rad)
    sq_meters = poly.area * meters_per_deg_lat * meters_per_deg_lng
    return round(sq_meters / 10000.0, 4)


def _extract_largest_polygon(geom: Any) -> Polygon:
    if isinstance(geom, Polygon):
        return geom
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    if hasattr(geom, "geoms"):
        polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if polys:
            flattened: List[Polygon] = []
            for item in polys:
                if isinstance(item, Polygon):
                    flattened.append(item)
                else:
                    flattened.extend(list(item.geoms))
            return max(flattened, key=lambda g: g.area)
    raise ValueError("HU-02: No se pudo extraer un polígono cerrado válido.")


def lint_and_validate_polygon(raw_coordinates: List[List[float]]) -> Dict[str, Any]:
    """
    HU-01 & HU-02: Validates and repairs a polygon coordinate ring.
    1. Deduplicates consecutive identical points.
    2. Forces closed exterior ring (first_vertex == last_vertex).
    3. Repairs self-intersections automatically via Shapely make_valid.
    4. Enforces 25 MB limit and calculates geodesic area in hectares.
    """
    if len(raw_coordinates) < 3:
        raise ValueError(
            "HU-01/HU-02: Se requieren al menos 3 vértices GPS para formar un polígono."
        )

    deduplicated: List[List[float]] = []
    duplicates_removed = 0

    for coord in raw_coordinates:
        if len(coord) < 2:
            raise ValueError("HU-02: Cada coordenada debe tener [longitud, latitud].")
        lng, lat = float(coord[0]), float(coord[1])
        if lng < -180.0 or lng > 180.0 or lat < -90.0 or lat > 90.0:
            raise ValueError(
                f"HU-02: Coordenada fuera de rango WGS84: [{lng}, {lat}]."
            )
        if deduplicated and _are_coords_equal(deduplicated[-1], [lng, lat]):
            duplicates_removed += 1
        else:
            deduplicated.append([lng, lat])

    already_closed = _are_coords_equal(deduplicated[0], deduplicated[-1])
    unique_count = len(deduplicated) - 1 if already_closed else len(deduplicated)
    if unique_count < 3:
        raise ValueError(
            "HU-02: Se requieren al menos 3 vértices distintos para un polígono."
        )

    was_ring_auto_closed = False
    ring: List[Tuple[float, float]] = [(pt[0], pt[1]) for pt in deduplicated]
    if not already_closed:
        ring.append(ring[0])
        was_ring_auto_closed = True
    else:
        ring[-1] = ring[0]

    poly = Polygon(ring)
    was_self_intersection_repaired = False

    if not poly.is_valid:
        repaired_geom = make_valid(poly)
        poly = _extract_largest_polygon(repaired_geom)
        was_self_intersection_repaired = True

    exterior_coords = [[float(x), float(y)] for x, y in poly.exterior.coords]
    exterior_coords[-1] = exterior_coords[0]

    geojson_geometry = {
        "type": "Polygon",
        "coordinates": [exterior_coords],
    }

    byte_size = len(json.dumps(geojson_geometry).encode("utf-8"))
    if byte_size > MAX_GEOJSON_SIZE_BYTES:
        raise ValueError("HU-02: El archivo GeoJSON excede el límite de 25 MB.")

    area_ha = _calculate_hectares_wgs84(poly)
    if area_ha <= 0:
        raise ValueError("HU-02: El polígono resultante tiene área cero.")

    return {
        "polygon": geojson_geometry,
        "area_hectares": area_ha,
        "vertex_count": len(exterior_coords),
        "byte_size": byte_size,
        "was_ring_auto_closed": was_ring_auto_closed,
        "duplicate_vertices_removed": duplicates_removed,
        "was_self_intersection_repaired": was_self_intersection_repaired,
        "is_topology_valid": bool(poly.is_valid),
        "requires_mandatory_polygon_eudr": (
            area_ha > EUDR_MANDATORY_POLYGON_THRESHOLD_HA
        ),
    }
