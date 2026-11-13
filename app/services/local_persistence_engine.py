"""Local SQLite persistence engine for Kanan Cacao offline-first sync and E2E testing."""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.services.custody_epcis_engine import register_epcis_reception
from app.services.topology_engine import lint_and_validate_polygon

DEFAULT_DB_PATH = os.environ.get(
    "KANAN_LOCAL_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "kanan_local.db"),
)

SEED_PARCELS: List[Dict[str, Any]] = [
    {
        "parcel_id": "PAR-CHONTALPA-01",
        "producer_id": "PROD-MATEO-01",
        "producer_name": "Don Mateo Hernández (Comalcalco)",
        "municipality": "Comalcalco",
        "area_hectares": 3.4,
        "eudr_status": "VERDE",
        "ndvi_alert_level": "VIGOR_AGROFORESTAL_OPTIMO",
        "mean_ndvi": 0.78,
        "shade_tree_count": 120,
        "acahual_removal_post_2020": False,
        "has_verified_custf_permit": False,
        "cumulative_dry_kg": 1800.0,
        "is_topology_valid": True,
        "coordinates": [
            [-93.2845, 18.2560],
            [-93.2810, 18.2560],
            [-93.2810, 18.2525],
            [-93.2845, 18.2525],
            [-93.2845, 18.2560],
        ],
        "updated_at_ms": 1731456000000,
    },
    {
        "parcel_id": "PAR-PARAISO-02",
        "producer_id": "PROD-ELENA-02",
        "producer_name": "Doña Elena Córdova (Paraíso)",
        "municipality": "Paraíso",
        "area_hectares": 2.8,
        "eudr_status": "VERDE",
        "ndvi_alert_level": "ESTRES_HIDRICO_TEMPRANO",
        "mean_ndvi": 0.59,
        "shade_tree_count": 95,
        "acahual_removal_post_2020": True,
        "has_verified_custf_permit": True,
        "cumulative_dry_kg": 950.0,
        "is_topology_valid": True,
        "coordinates": [
            [-93.2150, 18.3850],
            [-93.2115, 18.3850],
            [-93.2115, 18.3820],
            [-93.2150, 18.3820],
            [-93.2150, 18.3850],
        ],
        "updated_at_ms": 1731456000000,
    },
    {
        "parcel_id": "PAR-CUNDUACAN-03",
        "producer_id": "PROD-CARLOS-03",
        "producer_name": "Carlos Chontal (Cunduacán)",
        "municipality": "Cunduacán",
        "area_hectares": 4.6,
        "eudr_status": "ROJO",
        "ndvi_alert_level": "FOCO_PLAGA_FUNGICA_ALERTA",
        "mean_ndvi": 0.41,
        "shade_tree_count": 30,
        "acahual_removal_post_2020": True,
        "has_verified_custf_permit": False,
        "cumulative_dry_kg": 400.0,
        "is_topology_valid": True,
        "coordinates": [
            [-93.1850, 18.0650],
            [-93.1810, 18.0650],
            [-93.1810, 18.0610],
            [-93.1850, 18.0610],
            [-93.1850, 18.0650],
        ],
        "updated_at_ms": 1731456000000,
    },
]


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path or DEFAULT_DB_PATH
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def init_local_db(db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parcels (
                parcel_id TEXT PRIMARY KEY,
                producer_id TEXT NOT NULL,
                producer_name TEXT NOT NULL,
                municipality TEXT NOT NULL,
                area_hectares REAL NOT NULL,
                eudr_status TEXT NOT NULL,
                ndvi_alert_level TEXT NOT NULL,
                mean_ndvi REAL NOT NULL,
                shade_tree_count INTEGER NOT NULL,
                acahual_removal_post_2020 INTEGER NOT NULL,
                has_verified_custf_permit INTEGER NOT NULL,
                cumulative_dry_kg REAL NOT NULL DEFAULT 0.0,
                is_topology_valid INTEGER NOT NULL DEFAULT 1,
                coordinates_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS epcis_receptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parcel_id TEXT NOT NULL,
                producer_id TEXT NOT NULL,
                read_point_id TEXT NOT NULL,
                delivered_dry_equivalent_kg REAL NOT NULL,
                new_cumulative_dry_kg REAL NOT NULL,
                cumulative_kg_per_ha REAL NOT NULL,
                triggered_yield_cap_alert INTEGER NOT NULL,
                payload_sha256 TEXT NOT NULL,
                epcis_json TEXT NOT NULL,
                event_time_iso TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
        if count == 0:
            for item in SEED_PARCELS:
                conn.execute(
                    """
                    INSERT INTO parcels (
                        parcel_id, producer_id, producer_name, municipality,
                        area_hectares, eudr_status, ndvi_alert_level, mean_ndvi,
                        shade_tree_count, acahual_removal_post_2020,
                        has_verified_custf_permit, cumulative_dry_kg,
                        is_topology_valid, coordinates_json, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["parcel_id"],
                        item["producer_id"],
                        item["producer_name"],
                        item["municipality"],
                        item["area_hectares"],
                        item["eudr_status"],
                        item["ndvi_alert_level"],
                        item["mean_ndvi"],
                        item["shade_tree_count"],
                        1 if item["acahual_removal_post_2020"] else 0,
                        1 if item["has_verified_custf_permit"] else 0,
                        item["cumulative_dry_kg"],
                        1 if item["is_topology_valid"] else 0,
                        json.dumps(item["coordinates"]),
                        item["updated_at_ms"],
                    ),
                )
        conn.commit()


def _row_to_parcel_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "parcel_id": row["parcel_id"],
        "producer_id": row["producer_id"],
        "producer_name": row["producer_name"],
        "municipality": row["municipality"],
        "area_hectares": float(row["area_hectares"]),
        "eudr_status": row["eudr_status"],
        "ndvi_alert_level": row["ndvi_alert_level"],
        "mean_ndvi": float(row["mean_ndvi"]),
        "shade_tree_count": int(row["shade_tree_count"]),
        "acahual_removal_post_2020": bool(row["acahual_removal_post_2020"]),
        "has_verified_custf_permit": bool(row["has_verified_custf_permit"]),
        "cumulative_dry_kg": float(row["cumulative_dry_kg"]),
        "is_topology_valid": bool(row["is_topology_valid"]),
        "coordinates": json.loads(row["coordinates_json"]),
        "updated_at_ms": int(row["updated_at_ms"]),
    }


def list_persisted_parcels(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    init_local_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM parcels ORDER BY updated_at_ms DESC, parcel_id ASC"
        ).fetchall()
        return [_row_to_parcel_dict(r) for r in rows]


def toggle_parcel_custf(
    parcel_id: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    init_local_db(db_path)
    now_ms = int(time.time() * 1000)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Parcela {parcel_id} no encontrada en base local.")
        new_val = 0 if row["has_verified_custf_permit"] else 1
        conn.execute(
            """
            UPDATE parcels
            SET has_verified_custf_permit = ?, updated_at_ms = ?
            WHERE parcel_id = ?
            """,
            (new_val, now_ms, parcel_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        return _row_to_parcel_dict(updated)


def sync_offline_parcels(
    last_pulled_at_ms: Optional[int],
    pushed_parcels: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    init_local_db(db_path)
    now_ms = int(time.time() * 1000)
    accepted_ids: List[str] = []

    with _connect(db_path) as conn:
        for incoming in pushed_parcels:
            pid = incoming["parcel_id"]
            raw_coords = incoming.get("coordinates") or [
                [-93.2500, 18.2600],
                [-93.2460, 18.2600],
                [-93.2460, 18.2565],
                [-93.2500, 18.2565],
            ]
            lint_report = lint_and_validate_polygon(raw_coords)
            repaired_coords = lint_report["polygon"]["coordinates"][0]
            area_ha = incoming.get("area_hectares") or lint_report["area_hectares"]
            incoming_ts = int(incoming.get("updated_at_ms") or now_ms)

            existing = conn.execute(
                "SELECT * FROM parcels WHERE parcel_id = ?", (pid,)
            ).fetchone()

            if existing:
                existing_valid = bool(existing["is_topology_valid"])
                existing_ts = int(existing["updated_at_ms"])
                # Deterministic conflict resolution: valid topology wins, then latest timestamp.
                if existing_valid and not lint_report["is_topology_valid"]:
                    continue
                if existing_ts > incoming_ts and existing_valid:
                    continue

            conn.execute(
                """
                INSERT INTO parcels (
                    parcel_id, producer_id, producer_name, municipality,
                    area_hectares, eudr_status, ndvi_alert_level, mean_ndvi,
                    shade_tree_count, acahual_removal_post_2020,
                    has_verified_custf_permit, cumulative_dry_kg,
                    is_topology_valid, coordinates_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(parcel_id) DO UPDATE SET
                    producer_id = excluded.producer_id,
                    producer_name = excluded.producer_name,
                    municipality = excluded.municipality,
                    area_hectares = excluded.area_hectares,
                    eudr_status = excluded.eudr_status,
                    ndvi_alert_level = excluded.ndvi_alert_level,
                    mean_ndvi = excluded.mean_ndvi,
                    shade_tree_count = excluded.shade_tree_count,
                    acahual_removal_post_2020 = excluded.acahual_removal_post_2020,
                    has_verified_custf_permit = excluded.has_verified_custf_permit,
                    is_topology_valid = excluded.is_topology_valid,
                    coordinates_json = excluded.coordinates_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    pid,
                    incoming.get("producer_id", f"PROD-{pid}"),
                    incoming.get("producer_name", f"Productor {pid}"),
                    incoming.get("municipality", "Comalcalco"),
                    float(area_ha),
                    incoming.get("eudr_status", "VERDE"),
                    incoming.get("ndvi_alert_level", "VIGOR_AGROFORESTAL_OPTIMO"),
                    float(incoming.get("mean_ndvi", 0.76)),
                    int(incoming.get("shade_tree_count", 85)),
                    1 if incoming.get("acahual_removal_post_2020", False) else 0,
                    1 if incoming.get("has_verified_custf_permit", False) else 0,
                    float(incoming.get("cumulative_dry_kg", 0.0)),
                    1 if lint_report["is_topology_valid"] else 0,
                    json.dumps(repaired_coords),
                    incoming_ts,
                ),
            )
            accepted_ids.append(pid)

        conn.commit()

        if last_pulled_at_ms is not None:
            rows = conn.execute(
                "SELECT * FROM parcels WHERE updated_at_ms >= ? ORDER BY updated_at_ms DESC",
                (last_pulled_at_ms,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM parcels ORDER BY updated_at_ms DESC"
            ).fetchall()

        return {
            "server_timestamp_ms": now_ms,
            "accepted_parcel_ids": accepted_ids,
            "remote_changed_parcels": [_row_to_parcel_dict(r) for r in rows],
        }


def record_persisted_reception(
    parcel_id: str,
    delivered_dry_equivalent_kg: float,
    requester_role: str = "acopiador",
    read_point_id: str = "urn:epc:id:sgln:7500000.00001.0",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    init_local_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Parcela {parcel_id} no encontrada en base local.")

        parcel = _row_to_parcel_dict(row)
        admin_eval = register_epcis_reception(
            parcel_id=parcel["parcel_id"],
            producer_id=parcel["producer_id"],
            read_point_id=read_point_id,
            area_hectares=parcel["area_hectares"],
            previous_cumulative_dry_kg=parcel["cumulative_dry_kg"],
            delivered_dry_equivalent_kg=delivered_dry_equivalent_kg,
            acahual_removal_post_2020=parcel["acahual_removal_post_2020"],
            has_verified_custf_permit=parcel["has_verified_custf_permit"],
            requester_role="admin_astra",
        )

        epcis_ev = admin_eval["epcis_event"]
        conn.execute(
            """
            INSERT INTO epcis_receptions (
                parcel_id, producer_id, read_point_id,
                delivered_dry_equivalent_kg, new_cumulative_dry_kg,
                cumulative_kg_per_ha, triggered_yield_cap_alert,
                payload_sha256, epcis_json, event_time_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parcel["parcel_id"],
                parcel["producer_id"],
                read_point_id,
                delivered_dry_equivalent_kg,
                admin_eval["new_cumulative_dry_kg"],
                admin_eval["cumulative_kg_per_ha"],
                1 if admin_eval["triggered_yield_cap_alert"] else 0,
                epcis_ev["payloadSha256"],
                json.dumps(epcis_ev),
                epcis_ev["eventTime"],
            ),
        )
        conn.execute(
            "UPDATE parcels SET cumulative_dry_kg = ?, updated_at_ms = ? WHERE parcel_id = ?",
            (admin_eval["new_cumulative_dry_kg"], int(time.time() * 1000), parcel_id),
        )
        conn.commit()

        if requester_role in ("admin_astra", "auditor_sustentabilidad"):
            return admin_eval

        return {
            "status": "REGISTERED",
            "parcel_id": parcel["parcel_id"],
            "producer_id": parcel["producer_id"],
            "delivered_dry_equivalent_kg": delivered_dry_equivalent_kg,
            "epcis_event": epcis_ev,
        }
