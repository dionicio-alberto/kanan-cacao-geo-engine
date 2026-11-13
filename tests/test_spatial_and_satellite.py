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
