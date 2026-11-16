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
        compute_image_sharpness,
        fit_bbox_to_aspect_ratio,
        format_date_ddmmyyyy,
        get_or_download_cached_parcel_map_bytes,
        is_capture_cloudy,
        is_low_resolution_image,
        resolve_wmts_tile_url,
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
        "2025-07-01",
        "2024-07-01",
        "2023-07-01",
        "2022-07-01",
        "2021-07-01",
        "2020-07-01",
        "2019-07-01",
    ]
    assert all(r.startswith("s2cloudless-") for r in releases)
    assert all(
        item.get("cloud_filter_version") == "v13_s2_pure_quality"
        for item in bundle["snapshots"]
    )

    # Verifica que cambie dinámicamente al seleccionar otra fecha de referencia (2020-01-15)
    # y que descarte automáticamente tomas cubiertas por nubes (metadatos O píxeles blancos).
    assert is_capture_cloudy(cloud_cover_pct=42.0) is True
    assert is_capture_cloudy(cloud_cover_pct=10.0, tile_bytes=b"x" * 1000) is True

    assert format_date_ddmmyyyy("1998-11-15") == "15/11/1998"
    assert "01/07/2025" in bundle["snapshots"][0]["label"]
    assert "mt1.google.com/vt/lyrs=s" in resolve_wmts_tile_url(
        "google:mt1", 17, 58859, 31535
    )
    assert "collection=sentinel-2-l2a" in resolve_wmts_tile_url(
        "mpc:S2A_TEST", 16, 29429, 15766
    )

    import io
    import numpy as np
    from PIL import Image

    blurry_img = Image.new("RGB", (256, 256), (48, 84, 36))
    blurry_buf = io.BytesIO()
    blurry_img.save(blurry_buf, format="JPEG", quality=92)
    assert compute_image_sharpness(blurry_buf.getvalue()) < 5.5
    assert is_low_resolution_image(blurry_buf.getvalue()) is True
    assert is_low_resolution_image(src_res_meters=10.0) is True

    cloudy_img = Image.new("RGB", (256, 256), (235, 238, 240))
    cloudy_buf = io.BytesIO()
    cloudy_img.save(cloudy_buf, format="JPEG", quality=95)
    assert is_capture_cloudy(cloud_cover_pct=0.0, tile_bytes=cloudy_buf.getvalue()) is True

    nodata_img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    nodata_buf = io.BytesIO()
    nodata_img.save(nodata_buf, format="PNG")
    assert is_capture_cloudy(cloud_cover_pct=0.0, tile_bytes=nodata_buf.getvalue()) is True

    rng = np.random.default_rng(7)
    canopy_arr = np.stack(
        [
            rng.integers(32, 65, (256, 256), dtype=np.uint8),
            rng.integers(68, 110, (256, 256), dtype=np.uint8),
            rng.integers(25, 50, (256, 256), dtype=np.uint8),
        ],
        axis=2,
    )
    clear_img = Image.fromarray(canopy_arr, mode="RGB")
    clear_buf = io.BytesIO()
    clear_img.save(clear_buf, format="JPEG", quality=92)
    assert is_capture_cloudy(cloud_cover_pct=0.0, tile_bytes=clear_buf.getvalue()) is False

    custom_pool = [
        {"capture_date": "2026-01-01", "wayback_release": "901", "cloud_cover_pct": 85.0},
        {"capture_date": "2022-04-30", "wayback_release": "36557", "cloud_cover_pct": 0.0},
        {"capture_date": "2019-02-24", "wayback_release": "48376", "cloud_cover_pct": 5.0},
        {"capture_date": "2017-02-12", "wayback_release": "239", "cloud_cover_pct": 0.0},
        {"capture_date": "2015-05-17", "wayback_release": "26751", "cloud_cover_pct": 0.0},
        {"capture_date": "2013-03-31", "wayback_release": "1052", "cloud_cover_pct": 0.0},
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
    cached_file = cache_dir / "PAR-CARRIZAL_s2v13_hace_5_anios_1052.jpg"
    cached_file.write_bytes(b"JPEG_CACHED_BYTES_" * 80)
    served = get_or_download_cached_parcel_map_bytes(
        "PAR-CARRIZAL", "hace_5_anios", bbox, cache_dir=cache_dir, wayback_release="1052"
    )
    assert served.startswith(b"JPEG_CACHED_BYTES_")

    from app.services.satellite_audit_engine import select_best_candidate_within_10_attempts

    cands_20 = [
        {"capture_date": f"2026-05-{25 - i:02d}", "wayback_release": f"mpc:S{i}"}
        for i in range(22)
    ]

    def fake_render_cloudy_except_least(cand):
        day = int(cand["capture_date"][-2:])
        white_score = 9.0 if day != 10 else 6.5
        return clear_buf.getvalue(), {
            "white_pct": white_score,
            "shadow_pct": 1.0,
            "haze_pct": 4.0,
            "nodata_pct": 0.0,
            "black_pct": 0.0,
        }, 16

    fallback_pick = select_best_candidate_within_10_attempts(
        cands_20, render_fn=fake_render_cloudy_except_least, max_attempts=20
    )
    assert fallback_pick is not None
    assert fallback_pick["capture_date"] == "2026-05-10"
    assert fallback_pick["cloud_fallback_least_cloudy"] is True

    def fake_render_15th_clear(cand):
        day = int(cand["capture_date"][-2:])
        if day > 11:
            return clear_buf.getvalue(), {
                "white_pct": 30.0, "shadow_pct": 0.0, "haze_pct": 25.0,
                "nodata_pct": 0.0, "black_pct": 0.0,
            }, 16
        return clear_buf.getvalue(), {
            "white_pct": 0.5, "shadow_pct": 0.0, "haze_pct": 1.0,
            "nodata_pct": 0.0, "black_pct": 0.0,
        }, 16

    clear_pick = select_best_candidate_within_10_attempts(
        cands_20, render_fn=fake_render_15th_clear, max_attempts=20
    )
    assert clear_pick is not None
    assert clear_pick["capture_date"] == "2026-05-11"
    assert clear_pick["cloud_attempts_used"] == 15
    assert clear_pick["cloud_fallback_least_cloudy"] is False

    from app.services.satellite_audit_engine import compute_optimal_parcel_zoom

    tiny_parcel = [
        [-93.3910, 18.0340], [-93.3905, 18.0340], [-93.3905, 18.0335], [-93.3910, 18.0335],
    ]
    medium_parcel = [
        [-93.3920, 18.0350], [-93.3900, 18.0350], [-93.3900, 18.0330], [-93.3920, 18.0330],
    ]
    large_parcel = [
        [-93.3980, 18.0400], [-93.3850, 18.0400], [-93.3850, 18.0280], [-93.3980, 18.0280],
    ]
    c_lat_t, c_lng_t, z_tiny = compute_optimal_parcel_zoom(tiny_parcel)
    _, _, z_med = compute_optimal_parcel_zoom(medium_parcel)
    _, _, z_large = compute_optimal_parcel_zoom(large_parcel)
    assert z_tiny > z_med > z_large
    assert abs(c_lat_t - 18.03375) < 1e-6 and abs(c_lng_t - (-93.39075)) < 1e-6

    from app.services.satellite_audit_engine import fuse_sentinel2_with_submeter_detail

    wb_hd = Image.fromarray(canopy_arr, mode="RGB").resize((1520, 840))
    s2_hd = Image.new("RGB", (1520, 840), (50, 95, 42))
    fused_hd = fuse_sentinel2_with_submeter_detail(wb_hd, s2_hd)
    assert fused_hd.size == (1520, 840)
