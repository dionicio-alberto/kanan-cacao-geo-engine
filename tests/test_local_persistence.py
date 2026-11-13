"""Integration tests for SQLite local persistence, CUSTF toggles, and offline sync."""

from pathlib import Path
from app.services.local_persistence_engine import (
    list_persisted_parcels,
    record_persisted_reception,
    sync_offline_parcels,
    toggle_parcel_custf,
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
