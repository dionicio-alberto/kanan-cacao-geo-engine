"""Bolt 1 tests for server-side topology linter, EUDR JRC 2020, and Sentinel-2 NDVI."""

from fastapi.testclient import TestClient
from app.core.config import EUDR_LEGAL_DISCLAIMER
from app.main import app

client = TestClient(app)


def test_topology_validate_auto_closes_and_repairs_self_intersection() -> None:
    # Self-intersecting bowtie polygon without closing vertex
    payload = {
        "parcel_id": "parcel-chontalpa-01",
        "coordinates": [
            [-93.230, 18.260],
            [-93.220, 18.270],
            [-93.220, 18.260],
            [-93.230, 18.270],
        ],
    }
    response = client.post("/api/v1/topology/validate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["was_ring_auto_closed"] is True
    assert data["was_self_intersection_repaired"] is True
    assert data["is_topology_valid"] is True
    assert data["area_hectares"] > 4.0
    assert data["requires_mandatory_polygon_eudr"] is True
    ring = data["polygon"]["coordinates"][0]
    assert ring[0] == ring[-1]


def test_eudr_2020_audit_green_and_red_with_legal_disclaimer() -> None:
    coords = [
        [-93.225, 18.265],
        [-93.221, 18.265],
        [-93.221, 18.269],
        [-93.225, 18.269],
    ]
    green_res = client.post(
        "/api/v1/audits/eudr-2020",
        json={
            "parcel_id": "parcel-green",
            "coordinates": coords,
            "jrc_2020_tree_cover_pct": 85.0,
            "current_tree_cover_pct": 83.5,
        },
    )
    assert green_res.status_code == 200
    green_data = green_res.json()
    assert green_data["status"] == "VERDE"
    assert green_data["forest_cover_loss_post_2020"] is False
    assert green_data["legal_disclaimer"] == EUDR_LEGAL_DISCLAIMER

    red_res = client.post(
        "/api/v1/audits/eudr-2020",
        json={
            "parcel_id": "parcel-red",
            "coordinates": coords,
            "jrc_2020_tree_cover_pct": 88.0,
            "current_tree_cover_pct": 42.0,
        },
    )
    assert red_res.status_code == 200
    red_data = red_res.json()
    assert red_data["status"] == "ROJO"
    assert red_data["forest_cover_loss_post_2020"] is True
    assert red_data["legal_disclaimer"] == EUDR_LEGAL_DISCLAIMER


def test_sentinel2_ndvi_multitemporal_classifications() -> None:
    optimal = client.post(
        "/api/v1/audits/ndvi-sentinel2",
        json={
            "parcel_id": "parcel-01",
            "previous_scene_id": "S2B_20261001",
            "previous_mean_ndvi": 0.75,
            "latest_scene_id": "S2B_20261110",
            "latest_mean_ndvi": 0.78,
        },
    )
    assert optimal.status_code == 200
    assert optimal.json()["category"] == "VIGOR_AGROFORESTAL_OPTIMO"

    fungal_alert = client.post(
        "/api/v1/audits/ndvi-sentinel2",
        json={
            "parcel_id": "parcel-01",
            "previous_scene_id": "S2B_20261001",
            "previous_mean_ndvi": 0.79,
            "latest_scene_id": "S2B_20261110",
            "latest_mean_ndvi": 0.50,
        },
    )
    assert fungal_alert.status_code == 200
    assert fungal_alert.json()["category"] == "FOCO_PLAGA_FUNGICA_ALERTA"
    assert "moniliasis" in fungal_alert.json()["agronomic_recommendation"]


def test_sentinel2_live_endpoint_returns_ortho_and_stac_metadata() -> None:
    response = client.post(
        "/api/v1/audits/sentinel2-live",
        json={
            "parcel_id": "PAR-COMALCALCO-LIVE",
            "coordinates": [
                [-93.2845, 18.2560],
                [-93.2810, 18.2560],
                [-93.2810, 18.2525],
                [-93.2845, 18.2525],
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parcel_id"] == "PAR-COMALCALCO-LIVE"
    assert data["collection"] == "sentinel-2-l2a"
    assert "World_Imagery/MapServer/export" in data["satellite_ortho_url"]
    assert len(data["bbox"]) == 4
    assert data["latest_scene_id"]
    assert data["source_mode"] in {"live_stac", "fallback_cache"}


def test_historical_parcel_snapshots_cover_5_years_and_aspect_ratio(tmp_path) -> None:
    from datetime import datetime, timezone
    from pathlib import Path
    from app.services.satellite_audit_engine import (
        assign_distinct_captures_to_horizons,
        build_historical_parcel_snapshots,
        fit_bbox_to_aspect_ratio,
        get_or_download_cached_parcel_map_bytes,
        is_capture_cloudy,
    )

    coords = [
        [-93.391603, 18.034157],
        [-93.390618, 18.034270],
        [-93.390500, 18.032900],
        [-93.391500, 18.032800],
    ]
    bundle = build_historical_parcel_snapshots(
        "PAR-CARRIZAL", coords, registered_at_iso="2026-11-15T12:00:00Z", probe_live=False
    )
    keys = [item["period_key"] for item in bundle["snapshots"]]
    assert keys == [
        "registro_actual",
        "hace_6_meses",
        "hace_1_anio",
        "hace_2_anios",
        "hace_3_anios",
        "hace_4_anios",
        "hace_5_anios",
    ]
    releases = [item["wayback_release"] for item in bundle["snapshots"]]
    dates = [item["captured_date_iso"] for item in bundle["snapshots"]]
    assert len(set(releases)) == 7
    assert len(set(dates)) == 7
    assert dates == [
        "2025-06-09",
        "2022-04-30",
        "2021-02-17",
        "2019-02-24",
        "2017-02-12",
        "2013-03-31",
        "2011-04-25",
    ]

    # Verifica que cambie dinámicamente al seleccionar otra fecha de referencia (2020-01-15)
    # y que descarte automáticamente tomas cubiertas por nubes (> 25%).
    assert is_capture_cloudy(cloud_cover_pct=42.0) is True
    assert is_capture_cloudy(cloud_cover_pct=10.0, tile_bytes=b"x" * 1000) is True
    assert is_capture_cloudy(cloud_cover_pct=5.0, tile_bytes=b"x" * 8000) is False

    custom_pool = [
        {"capture_date": "2026-01-01", "wayback_release": "901", "cloud_cover_pct": 85.0},
        {"capture_date": "2022-04-30", "wayback_release": "36557", "cloud_cover_pct": 0.0},
        {"capture_date": "2019-02-24", "wayback_release": "48376", "cloud_cover_pct": 5.0},
        {"capture_date": "2017-02-12", "wayback_release": "239", "cloud_cover_pct": 0.0},
        {"capture_date": "2013-03-31", "wayback_release": "1052", "cloud_cover_pct": 0.0},
        {"capture_date": "2011-04-25", "wayback_release": "388", "cloud_cover_pct": 0.0},
        {"capture_date": "2010-03-10", "wayback_release": "200", "cloud_cover_pct": 0.0},
        {"capture_date": "2009-01-20", "wayback_release": "150", "cloud_cover_pct": 0.0},
    ]
    assigned = assign_distinct_captures_to_horizons(
        custom_pool, datetime(2020, 1, 15, tzinfo=timezone.utc)
    )
    assigned_dates = [cap["capture_date"] for _, _, _, cap in assigned]
    assert "2026-01-01" not in assigned_dates
    assert assigned_dates[0] == "2019-02-24"

    bbox = bundle["extent_bbox"]
    assert round((bbox[2] - bbox[0]) / (bbox[3] - bbox[1]), 2) == 1.75
    fitted = fit_bbox_to_aspect_ratio([-93.2275, 18.2085, -93.2185, 18.2175], 1.75)
    assert round(fitted[0], 5) == -93.23088 or abs(fitted[0] - (-93.230875)) < 1e-4

    cache_dir = Path(tmp_path) / "map_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_file = cache_dir / "PAR-CARRIZAL_hace_5_anios_388.jpg"
    cached_file.write_bytes(b"JPEG_CACHED_BYTES_" * 12)
    served = get_or_download_cached_parcel_map_bytes(
        "PAR-CARRIZAL", "hace_5_anios", bbox, cache_dir=cache_dir, wayback_release="388"
    )
    assert served.startswith(b"JPEG_CACHED_BYTES_")
