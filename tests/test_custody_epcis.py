"""Bolt 2 tests for CUSTF auto-block (HU-05), Blind Yield Caps (HU-06), and EPCIS 2.0 (HU-07)."""

from fastapi.testclient import TestClient
from app.core.config import EPCIS_CONTEXT_URL
from app.main import app

client = TestClient(app)


def test_custf_blocks_producer_without_semarnat_permit() -> None:
    response = client.post(
        "/api/v1/custody/epcis-reception",
        json={
            "parcel_id": "parcel-acahual",
            "producer_id": "prod-99",
            "read_point_id": "urn:epc:id:sgln:7500000.00001.0",
            "area_hectares": 2.0,
            "previous_cumulative_dry_kg": 0.0,
            "delivered_dry_equivalent_kg": 100.0,
            "acahual_removal_post_2020": True,
            "has_verified_custf_permit": False,
            "requester_role": "acopiador",
        },
    )
    assert response.status_code == 403
    assert "CUSTF de SEMARNAT" in response.json()["detail"]


def test_blind_yield_caps_and_epcis_2_0_signature() -> None:
    # 2.0 ha -> max biological yield in Tabasco = 1300 kg (650 kg/ha)
    # Delivering 300 kg on top of 1100 kg = 1400 kg (700 kg/ha > 650 kg/ha)
    base_payload = {
        "parcel_id": "parcel-01",
        "producer_id": "prod-01",
        "read_point_id": "urn:epc:id:sgln:7500000.00001.0",
        "area_hectares": 2.0,
        "previous_cumulative_dry_kg": 1100.0,
        "delivered_dry_equivalent_kg": 300.0,
        "acahual_removal_post_2020": False,
        "has_verified_custf_permit": False,
        "event_time_iso": "2026-11-12T18:00:00Z",
    }

    # 1. Scale operator (acopiador) receives BLIND response without alert or kg/ha
    blind_res = client.post(
        "/api/v1/custody/epcis-reception",
        json={**base_payload, "requester_role": "acopiador"},
    )
    assert blind_res.status_code == 200
    blind_data = blind_res.json()
    assert "triggered_yield_cap_alert" not in blind_data
    assert "cumulative_kg_per_ha" not in blind_data
    assert blind_data["epcis_event"]["@context"] == EPCIS_CONTEXT_URL
    assert len(blind_data["epcis_event"]["payloadSha256"]) == 64

    # 2. Admin / Sustainability Auditor receives full audit with red alert triggered
    admin_res = client.post(
        "/api/v1/custody/epcis-reception",
        json={**base_payload, "requester_role": "admin_astra"},
    )
    assert admin_res.status_code == 200
    admin_data = admin_res.json()
    assert admin_data["cumulative_kg_per_ha"] == 700.0
    assert admin_data["triggered_yield_cap_alert"] is True


def test_traces_nt_export_package_and_eudr_gate() -> None:
    coords = [
        [-93.225, 18.265],
        [-93.221, 18.265],
        [-93.221, 18.269],
        [-93.225, 18.269],
    ]
    ok_res = client.post(
        "/api/v1/exports/traces-nt-package",
        json={
            "parcel_id": "PAR-CHONTALPA-01",
            "producer_id": "PROD-MATEO-01",
            "municipality": "Comalcalco",
            "coordinates": coords,
            "eudr_status": "VERDE",
            "acahual_removal_post_2020": False,
            "has_verified_custf_permit": False,
            "delivered_dry_equivalent_kg": 220.0,
        },
    )
    assert ok_res.status_code == 200
    pkg = ok_res.json()
    assert pkg["schema_version"] == "EUDR-TRACES-NT-1.0"
    assert len(pkg["package_integrity_sha256"]) == 64

    blocked_res = client.post(
        "/api/v1/exports/traces-nt-package",
        json={
            "parcel_id": "PAR-CUNDUACAN-03",
            "producer_id": "PROD-CARLOS-03",
            "municipality": "Cunduacán",
            "coordinates": coords,
            "eudr_status": "ROJO",
            "acahual_removal_post_2020": False,
            "has_verified_custf_permit": False,
            "delivered_dry_equivalent_kg": 220.0,
        },
    )
    assert blocked_res.status_code == 403
