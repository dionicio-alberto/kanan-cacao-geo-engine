"""Tests for field logbook persistence and synchronization endpoints."""

import os
import tempfile
from fastapi.testclient import TestClient
import pytest
from app.main import app
from app.services.local_persistence_engine import (
    create_field_logbook_entry,
    init_local_db,
    list_field_logbook_entries,
    sync_field_logbook_entries,
)


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_local_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_logbook_seed_and_list(temp_db):
    entries = list_field_logbook_entries(db_path=temp_db)
    assert len(entries) >= 2
    entry_ids = [e["id"] for e in entries]
    assert "LOG-INICIAL-01" in entry_ids
    assert "LOG-INICIAL-02" in entry_ids


def test_create_and_filter_logbook_entry(temp_db):
    new_entry = {
        "id": "LOG-TEST-99",
        "parcel_id": "PAR-CHONTALPA-01",
        "activity_type": "CONTROL_PLAGAS",
        "activity_label": "Control preventivo de plagas",
        "performed_date": "2026-11-17",
        "notes": "Aplicación de caldo bordelés preventivo en cuadrante sur.",
        "input_used": "Caldo bordelés 2%",
        "photos": [{"id": "P-1", "fileName": "foto.jpg"}],
    }
    saved = create_field_logbook_entry(new_entry, db_path=temp_db)
    assert saved["id"] == "LOG-TEST-99"
    assert saved["parcel_id"] == "PAR-CHONTALPA-01"
    assert saved["producer_id"] == "PROD-MATEO-01"  # Auto-resolved from parcel
    assert len(saved["photos"]) == 1

    # Filter by parcel
    filtered_parcel = list_field_logbook_entries(parcel_id="PAR-CHONTALPA-01", db_path=temp_db)
    assert all(e["parcel_id"] == "PAR-CHONTALPA-01" for e in filtered_parcel)

    # Filter by producer
    filtered_producer = list_field_logbook_entries(producer_id="Don Mateo", db_path=temp_db)
    assert len(filtered_producer) >= 2
    assert all("Mateo" in (e.get("producer_name") or "") for e in filtered_producer)

    # Filter by date range
    filtered_date = list_field_logbook_entries(
        start_date="2026-11-01", end_date="2026-11-30", db_path=temp_db
    )
    assert any(e["id"] == "LOG-TEST-99" for e in filtered_date)
    assert not any(e["id"] == "LOG-INICIAL-01" for e in filtered_date)


def test_sync_logbook_entries(temp_db):
    pushed = [
        {
            "id": "LOG-OFFLINE-01",
            "parcel_id": "PAR-CHONTALPA-02",
            "activity_type": "CHAPEO_LIMPIEZA",
            "activity_label": "Chapeo y limpieza",
            "performed_date": "2026-11-16",
            "notes": "Limpieza manual con machete sin quema.",
            "photos": [],
        }
    ]
    res = sync_field_logbook_entries(pushed_entries=pushed, db_path=temp_db)
    assert res["status"] == "SYNCED"
    assert "LOG-OFFLINE-01" in res["accepted_ids"]
    assert res["synced_count"] == 1
    remote_ids = [e["id"] for e in res["remote_entries"]]
    assert "LOG-OFFLINE-01" in remote_ids


def test_api_endpoints_logbook():
    client = TestClient(app)

    # GET /api/v1/logbook
    res = client.get("/api/v1/logbook")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "OK"
    assert "entries" in data

    # POST /api/v1/logbook
    payload = {
        "id": "LOG-API-01",
        "parcel_id": "PAR-CHONTALPA-01",
        "activity_type": "PODA",
        "activity_label": "Poda sanitaria",
        "performed_date": "2026-11-17",
        "notes": "Poda de mantenimiento ligero.",
    }
    create_res = client.post("/api/v1/logbook", json=payload)
    assert create_res.status_code == 200
    assert create_res.json()["status"] == "CREATED"
    assert create_res.json()["entry"]["id"] == "LOG-API-01"

    # POST /api/v1/logbook/sync
    sync_payload = {
        "pushed_entries": [
            {
                "id": "LOG-API-SYNC-01",
                "parcel_id": "PAR-CHONTALPA-01",
                "activity_type": "RIEGO_RESIEMBRA",
                "activity_label": "Riego",
                "performed_date": "2026-11-17",
                "notes": "Riego matutino.",
            }
        ]
    }
    sync_res = client.post("/api/v1/logbook/sync", json=sync_payload)
    assert sync_res.status_code == 200
    assert sync_res.json()["status"] == "SYNCED"
    assert "LOG-API-SYNC-01" in sync_res.json()["accepted_ids"]
