"""Integration tests for SQLite local persistence, CUSTF toggles, and offline sync."""

from pathlib import Path
from app.services.local_persistence_engine import (
    delete_parcel_record,
    list_persisted_parcels,
    record_persisted_reception,
    supabase_row_to_parcel_dict,
    sync_offline_parcels,
    toggle_parcel_custf,
    update_parcel_metadata,
)


def test_local_sqlite_seed_and_custf_toggle(tmp_path: Path) -> None:
    db_file = str(tmp_path / "test_kanan.db")
    parcels = list_persisted_parcels(db_file)
    assert len(parcels) == 3

    target = next(p for p in parcels if p["parcel_id"] == "PAR-CUNDUACAN-03")
    assert target["has_verified_custf_permit"] is False

    updated = toggle_parcel_custf("PAR-CUNDUACAN-03", db_file)
    assert updated["has_verified_custf_permit"] is True


def test_offline_sync_repairs_polygon_and_persists(tmp_path: Path) -> None:
    db_file = str(tmp_path / "test_sync.db")
    sync_res = sync_offline_parcels(
        last_pulled_at_ms=None,
        pushed_parcels=[
            {
                "parcel_id": "PAR-HUIMANGUILLO-04",
                "producer_id": "PROD-SOFIA-04",
                "producer_name": "Sofía Méndez (Huimanguillo)",
                "municipality": "Huimanguillo",
                "eudr_status": "VERDE",
                "ndvi_alert_level": "VIGOR_AGROFORESTAL_OPTIMO",
                "mean_ndvi": 0.81,
                "shade_tree_count": 110,
                "acahual_removal_post_2020": False,
                "has_verified_custf_permit": False,
                "coordinates": [
                    [-93.3900, 17.8500],
                    [-93.3860, 17.8500],
                    [-93.3860, 17.8465],
                    [-93.3900, 17.8465],
                ],
                "updated_at_ms": 1731500000000,
            }
        ],
        db_path=db_file,
    )

    assert "PAR-HUIMANGUILLO-04" in sync_res["accepted_parcel_ids"]
    all_parcels = list_persisted_parcels(db_file)
    assert len(all_parcels) == 4


def test_persisted_epcis_reception_updates_cumulative_kg(tmp_path: Path) -> None:
    db_file = str(tmp_path / "test_epcis.db")
    blind_res = record_persisted_reception(
        parcel_id="PAR-CHONTALPA-01",
        delivered_dry_equivalent_kg=200.0,
        requester_role="acopiador",
        db_path=db_file,
    )
    assert blind_res["status"] == "REGISTERED"
    assert "cumulative_kg_per_ha" not in blind_res

    admin_res = record_persisted_reception(
        parcel_id="PAR-CHONTALPA-01",
        delivered_dry_equivalent_kg=300.0,
        requester_role="admin_astra",
        db_path=db_file,
    )
    assert admin_res["new_cumulative_dry_kg"] == 2300.0
    assert admin_res["triggered_yield_cap_alert"] is True


def test_supabase_row_maps_to_parcel_contract() -> None:
    mapped = supabase_row_to_parcel_dict(
        {
            "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "producer_id": "11111111-1111-4111-8111-111111111111",
            "parcel_name": "Finca La Joya de Chontalpa",
            "municipality": "Comalcalco",
            "area_hectares": 3.8,
            "eudr_status": "VERDE",
            "acahual_removal_post_2020": False,
            "updated_at": "2026-11-15T21:49:47.832586+00:00",
            "profiles": {"full_name": "Mateo Hernández Chablé"},
            "eudr_audits": [{"mean_ndvi_score": 0.74}],
            "shade_tree_inventories": [{"tree_count": 45}, {"tree_count": 30}],
            "legality_documents": [{"document_type": "CUSTF_SEMARNAT", "verified_by_admin": True}],
            "epcis_collection_events": [{"delivered_dry_equivalent_kg": 300.0}],
            "geojson_payload": {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-93.2845, 18.256],
                        [-93.281, 18.256],
                        [-93.281, 18.2525],
                        [-93.2845, 18.2525],
                        [-93.2845, 18.256],
                    ]],
                },
            },
        }
    )
    assert mapped["parcel_id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert mapped["shade_tree_count"] == 75
    assert mapped["has_verified_custf_permit"] is True
    assert mapped["cumulative_dry_kg"] == 300.0
    assert mapped["ndvi_alert_level"] == "VIGOR_AGROFORESTAL_OPTIMO"
    assert len(mapped["coordinates"]) == 5


def test_historical_snapshots_are_persisted_once_with_parcel_sync(tmp_path: Path) -> None:
    from app.services.local_persistence_engine import (
        get_or_create_parcel_historical_snapshots,
    )

    db_file = str(tmp_path / "test_hist_sync.db")
    sync_offline_parcels(
        last_pulled_at_ms=None,
        pushed_parcels=[
            {
                "parcel_id": "PAR-CACHE-ONCE-01",
                "producer_name": "Don Mateo",
                "municipality": "Comalcalco",
                "area_hectares": 3.0,
                "coordinates": [
                    [-93.2845, 18.2560],
                    [-93.2810, 18.2560],
                    [-93.2810, 18.2525],
                    [-93.2845, 18.2525],
                ],
            }
        ],
        db_path=db_file,
    )

    manifest = get_or_create_parcel_historical_snapshots("PAR-CACHE-ONCE-01", db_path=db_file)
    assert manifest["persisted_on_server"] is True
    assert len(manifest["snapshots"]) == 7
    assert manifest["snapshots"][0]["period_key"] == "registro_actual"
    assert manifest["snapshots"][-1]["period_key"] == "hace_5_anios"


def test_parcel_legal_documents_upload_and_download(tmp_path: Path) -> None:
    from app.services.local_persistence_engine import (
        get_parcel_document_file,
        list_parcel_documents,
        upload_parcel_document,
    )

    db_file = str(tmp_path / "test_docs.db")
    docs_dir = tmp_path / "docs_vault"
    sync_offline_parcels(
        last_pulled_at_ms=None,
        pushed_parcels=[
            {
                "parcel_id": "PAR-DOCS-01",
                "producer_name": "Doña Elena",
                "municipality": "Paraíso",
                "area_hectares": 2.5,
                "coordinates": [
                    [-93.215, 18.385],
                    [-93.211, 18.385],
                    [-93.211, 18.382],
                    [-93.215, 18.382],
                ],
            }
        ],
        db_path=db_file,
    )

    uploaded = upload_parcel_document(
        parcel_id="PAR-DOCS-01",
        document_type="CERTIFICADO_PARCELARIO",
        file_name="certificado_ejidal.pdf",
        file_bytes=b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n",
        content_type="application/pdf",
        db_path=db_file,
        docs_dir=docs_dir,
    )
    assert uploaded["document_type"] == "CERTIFICADO_PARCELARIO"
    assert uploaded["file_name"] == "certificado_ejidal.pdf"
    assert len(uploaded["sha256_hash"]) == 64

    docs = list_parcel_documents("PAR-DOCS-01", db_path=db_file)
    assert len(docs) == 1
    assert docs[0]["id"] == uploaded["id"]

    raw_bytes, ctype, fname = get_parcel_document_file(
        "PAR-DOCS-01", uploaded["id"], db_path=db_file
    )
    assert raw_bytes.startswith(b"%PDF-1.4")
    assert ctype == "application/pdf"
    assert fname == "certificado_ejidal.pdf"


def test_only_admin_can_update_registration_date_with_confirmar(tmp_path: Path) -> None:
    import pytest
    from app.services.local_persistence_engine import update_parcel_registration_date

    db_file = str(tmp_path / "test_admin_regdate.db")
    sync_offline_parcels(
        last_pulled_at_ms=None,
        pushed_parcels=[
            {
                "parcel_id": "PAR-REGDATE-01",
                "producer_name": "Don Carlos",
                "municipality": "Comalcalco",
                "area_hectares": 3.2,
                "coordinates": [
                    [-93.2845, 18.256],
                    [-93.281, 18.256],
                    [-93.281, 18.2525],
                    [-93.2845, 18.2525],
                ],
            }
        ],
        db_path=db_file,
    )

    with pytest.raises(PermissionError):
        update_parcel_registration_date(
            parcel_id="PAR-REGDATE-01",
            new_date_iso="2024-05-10",
            confirmation_word="confirmar",
            changed_by="Técnico Campo",
            changed_by_role="acopiador",
            db_path=db_file,
        )

    with pytest.raises(ValueError):
        update_parcel_registration_date(
            parcel_id="PAR-REGDATE-01",
            new_date_iso="2024-05-10",
            confirmation_word="si",
            changed_by="Admin ASTRA",
            changed_by_role="admin_astra",
            db_path=db_file,
        )

    updated = update_parcel_registration_date(
        parcel_id="PAR-REGDATE-01",
        new_date_iso="2024-05-10",
        confirmation_word="confirmar",
        changed_by="Dirección de Operaciones ASTRA",
        changed_by_role="admin_astra",
        db_path=db_file,
    )
    assert updated["registered_at_iso"].startswith("2024-05-10")
    assert len(updated["registration_audit_log"]) == 1
    assert updated["registration_audit_log"][0]["new_date_iso"] == "2024-05-10"
    assert updated["registration_audit_log"][0]["changed_by"] == "Dirección de Operaciones ASTRA"


def test_admin_can_assign_profile_roles_and_edit_role_definitions(tmp_path: Path) -> None:
    import pytest
    from app.services.admin_roles_engine import (
        assign_role_to_profile,
        list_admin_profiles_and_roles,
        upsert_role_definition,
    )

    db_file = str(tmp_path / "test_admin_roles.db")
    panel = list_admin_profiles_and_roles(db_path=db_file)
    assert len(panel["profiles"]) >= 4
    assert any(r["role_key"] == "admin_astra" for r in panel["roles"])

    with pytest.raises(PermissionError):
        assign_role_to_profile(
            user_id="11111111-1111-4111-8111-111111111111",
            new_role_key="auditor_sustentabilidad",
            changed_by="Usuario no admin",
            requester_role="acopiador",
            db_path=db_file,
        )

    after_create = upsert_role_definition(
        role_key="inspector_campo",
        display_name="Inspector de Campo Cacao",
        description="Supervisa levantamiento en parcelas y bitácora.",
        permissions={"can_view_parcels": True, "can_edit_logbook": True},
        changed_by="Dirección de Operaciones ASTRA",
        requester_role="admin_astra",
        db_path=db_file,
    )
    assert any(r["role_key"] == "inspector_campo" for r in after_create["roles"])

    after_assign = assign_role_to_profile(
        user_id="11111111-1111-4111-8111-111111111111",
        new_role_key="inspector_campo",
        changed_by="Dirección de Operaciones ASTRA",
        requester_role="admin_astra",
        db_path=db_file,
    )
    mateo = next(
        p
        for p in after_assign["profiles"]
        if p["user_id"] == "11111111-1111-4111-8111-111111111111"
    )
    assert mateo["role"] == "inspector_campo"
    assert mateo["role_display_name"] == "Inspector de Campo Cacao"


def test_update_and_delete_parcel_metadata(tmp_path: Path) -> None:
    db_file = str(tmp_path / "test_update_delete.db")
    parcels = list_persisted_parcels(db_file)
    assert len(parcels) == 3

    # Update parcel name and producer name
    updated = update_parcel_metadata(
        parcel_id="PAR-CHONTALPA-01",
        parcel_name="Finca La Chontalpa Lote 1 - Editada",
        producer_name="Mateo Chablé Méndez (Actualizado)",
        municipality="Comalcalco Norte",
        db_path=db_file,
    )
    assert updated["parcel_name"] == "Finca La Chontalpa Lote 1 - Editada"
    assert updated["producer_name"] == "Mateo Chablé Méndez (Actualizado)"
    assert updated["municipality"] == "Comalcalco Norte"

    # Delete parcel
    res = delete_parcel_record("PAR-CHONTALPA-01", db_path=db_file)
    assert res is True

    remaining = list_persisted_parcels(db_file)
    assert len(remaining) == 2
    assert not any(p["parcel_id"] == "PAR-CHONTALPA-01" for p in remaining)


def test_api_patch_and_delete_parcel_endpoints(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    db_file = str(tmp_path / "test_api.db")
    monkeypatch.setenv("KANAN_FORCE_SQLITE_ONLY", "1")
    monkeypatch.setattr("app.services.local_persistence_engine.DEFAULT_DB_PATH", db_file)

    client = TestClient(app)

    # Test PATCH endpoint
    patch_res = client.patch(
        "/api/v1/parcels/PAR-CUNDUACAN-03",
        json={
            "parcel_name": "Rancho San Antonio - Editado API",
            "producer_name": "Rosa Gómez Cundua (API)",
            "municipality": "Cunduacán Centro",
        },
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["status"] == "UPDATED"
    assert data["parcel"]["parcel_name"] == "Rancho San Antonio - Editado API"
    assert data["parcel"]["producer_name"] == "Rosa Gómez Cundua (API)"

    # Test DELETE endpoint
    del_res = client.delete("/api/v1/parcels/PAR-CUNDUACAN-03")
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["status"] == "DELETED"
