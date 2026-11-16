"""Supabase PostGIS and SQLite persistence engine for Kanan Cacao R/W and offline sync."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional
import uuid
import httpx
from app.services.custody_epcis_engine import register_epcis_reception
from app.services.satellite_audit_engine import (
    build_historical_parcel_snapshots,
    get_or_download_cached_parcel_map_bytes,
)
from app.services.topology_engine import lint_and_validate_polygon

DEFAULT_DB_PATH = os.environ.get(
    "KANAN_LOCAL_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "kanan_local.db"),
)

DEFAULT_PRODUCER_UUID = "11111111-1111-4111-8111-111111111111"
DEFAULT_TECH_UUID = "44444444-4444-4444-8444-444444444444"
DEFAULT_COLLECTOR_UUID = "55555555-5555-4555-8555-555555555555"

PARCEL_SELECT_FIELDS = (
    "id,producer_id,parcel_name,municipality,area_hectares,geojson_payload,"
    "eudr_status,acahual_removal_post_2020,updated_at,"
    "profiles!parcels_producer_id_fkey(full_name,is_blocked_by_custf),"
    "eudr_audits(mean_ndvi_score,sentinel_scene_id),"
    "shade_tree_inventories(tree_count),"
    "legality_documents(id,document_type,verified_by_admin),"
    "epcis_collection_events(delivered_dry_equivalent_kg)"
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


def _get_supabase_config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return url, key


def is_supabase_rw_active(db_path: Optional[str] = None) -> bool:
    """Return True when default persistence should read/write Supabase PostGIS."""
    if db_path is not None:
        return False
    if os.environ.get("KANAN_FORCE_SQLITE_ONLY") == "1":
        return False
    url, key = _get_supabase_config()
    return bool(url.startswith("http") and "tu-proyecto" not in url and key)


def get_active_storage_engine(db_path: Optional[str] = None) -> str:
    return "supabase_postgis" if is_supabase_rw_active(db_path) else "sqlite3_local"


def _pg_headers(prefer: Optional[str] = None) -> Dict[str, str]:
    _, key = _get_supabase_config()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _classify_ndvi_level(mean_ndvi: float) -> str:
    if mean_ndvi < 0.45:
        return "FOCO_PLAGA_FUNGICA_ALERTA"
    if mean_ndvi < 0.65:
        return "ESTRES_HIDRICO_TEMPRANO"
    return "VIGOR_AGROFORESTAL_OPTIMO"


def _iso_to_ms(iso_value: Optional[str]) -> int:
    if not iso_value:
        return int(time.time() * 1000)
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        return int(time.time() * 1000)


def _as_uuid(raw_id: str, fallback_uuid: Optional[str] = None) -> str:
    try:
        return str(uuid.UUID(raw_id))
    except (ValueError, TypeError):
        if fallback_uuid:
            return fallback_uuid
        return str(uuid.uuid5(uuid.NAMESPACE_OID, str(raw_id)))


def supabase_row_to_parcel_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Supabase PostgREST parcels row with nested relations into API parcel dict."""
    profile = row.get("profiles") or {}
    audits = row.get("eudr_audits") or []
    trees = row.get("shade_tree_inventories") or []
    docs = row.get("legality_documents") or []
    events = row.get("epcis_collection_events") or []

    parcel_name = row.get("parcel_name") or row["id"]
    full_name = profile.get("full_name") or "Productor ASTRA"
    display_name = (
        f"{full_name} ({parcel_name})"
        if parcel_name and parcel_name not in full_name
        else full_name
    )

    mean_ndvi = float(audits[0].get("mean_ndvi_score") or 0.74) if audits else 0.74
    shade_tree_count = sum(int(item.get("tree_count") or 0) for item in trees)
    has_custf = any(
        d.get("document_type") == "CUSTF_SEMARNAT" and bool(d.get("verified_by_admin"))
        for d in docs
    )
    cumulative_dry_kg = sum(
        float(ev.get("delivered_dry_equivalent_kg") or 0.0) for ev in events
    )

    payload = row.get("geojson_payload") or {}
    geom = payload.get("geometry") or {}
    properties = payload.get("properties") or {}
    rings = geom.get("coordinates") or []
    coords = rings[0] if rings and isinstance(rings[0], list) else [
        [-93.2845, 18.2560],
        [-93.2810, 18.2560],
        [-93.2810, 18.2525],
        [-93.2845, 18.2525],
        [-93.2845, 18.2560],
    ]

    persisted_snapshots = properties.get("historical_snapshots")
    persisted_bbox = properties.get("extent_bbox")
    has_wayback = (
        isinstance(persisted_snapshots, list)
        and len(persisted_snapshots) == 7
        and all(bool(item.get("wayback_release")) for item in persisted_snapshots)
    )
    if (
        has_wayback
        and isinstance(persisted_bbox, list)
        and len(persisted_bbox) == 4
    ):
        historical_snapshots = persisted_snapshots
        extent_bbox = persisted_bbox
        snapshots_cached_on_server = True
    else:
        hist_bundle = build_historical_parcel_snapshots(
            row["id"], coords, row.get("updated_at")
        )
        historical_snapshots = hist_bundle["snapshots"]
        extent_bbox = hist_bundle["extent_bbox"]
        snapshots_cached_on_server = False

    return {
        "parcel_id": row["id"],
        "parcel_name": parcel_name,
        "producer_id": row["producer_id"],
        "producer_name": display_name,
        "municipality": row["municipality"],
        "area_hectares": float(row["area_hectares"]),
        "eudr_status": row["eudr_status"],
        "ndvi_alert_level": _classify_ndvi_level(mean_ndvi),
        "mean_ndvi": mean_ndvi,
        "shade_tree_count": shade_tree_count,
        "acahual_removal_post_2020": bool(row.get("acahual_removal_post_2020")),
        "has_verified_custf_permit": has_custf,
        "cumulative_dry_kg": cumulative_dry_kg,
        "is_topology_valid": True,
        "coordinates": coords,
        "extent_bbox": extent_bbox,
        "historical_snapshots": historical_snapshots,
        "persisted_on_server": snapshots_cached_on_server,
        "updated_at_ms": _iso_to_ms(row.get("updated_at")),
    }


def _fetch_supabase_parcels(parcel_id: Optional[str] = None) -> List[Dict[str, Any]]:
    url, _ = _get_supabase_config()
    params: Dict[str, str] = {
        "select": PARCEL_SELECT_FIELDS,
        "order": "updated_at.desc",
    }
    if parcel_id:
        params["id"] = f"eq.{parcel_id}"
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{url}/rest/v1/parcels", params=params, headers=_pg_headers())
        resp.raise_for_status()
        rows = resp.json()

        results: List[Dict[str, Any]] = []
        for raw_row in rows:
            mapped = supabase_row_to_parcel_dict(raw_row)
            if not mapped.get("persisted_on_server"):
                payload = dict(raw_row.get("geojson_payload") or {})
                props = dict(payload.get("properties") or {})
                props["extent_bbox"] = mapped["extent_bbox"]
                props["historical_snapshots"] = mapped["historical_snapshots"]
                payload["properties"] = props
                try:
                    client.patch(
                        f"{url}/rest/v1/parcels",
                        params={"id": f"eq.{raw_row['id']}"},
                        headers=_pg_headers(),
                        json={"geojson_payload": payload},
                    )
                    mapped["persisted_on_server"] = True
                except httpx.HTTPError:
                    pass
            results.append(mapped)
    return results


def _toggle_supabase_custf(parcel_id: str) -> Dict[str, Any]:
    url, _ = _get_supabase_config()
    with httpx.Client(timeout=10.0) as client:
        res = client.get(
            f"{url}/rest/v1/parcels",
            params={
                "id": f"eq.{parcel_id}",
                "select": "id,producer_id,legality_documents(id,document_type,verified_by_admin)",
            },
            headers=_pg_headers(),
        )
        res.raise_for_status()
        rows = res.json()
        if not rows:
            raise ValueError(f"Parcela {parcel_id} no encontrada en Supabase PostGIS.")

        row = rows[0]
        custf_docs = [
            d for d in (row.get("legality_documents") or [])
            if d.get("document_type") == "CUSTF_SEMARNAT"
        ]
        now_iso = datetime.now(timezone.utc).isoformat()
        if custf_docs:
            doc = custf_docs[0]
            new_val = not bool(doc.get("verified_by_admin"))
            patch_res = client.patch(
                f"{url}/rest/v1/legality_documents",
                params={"id": f"eq.{doc['id']}"},
                headers=_pg_headers(),
                json={
                    "verified_by_admin": new_val,
                    "verified_at": now_iso if new_val else None,
                    "updated_at": now_iso,
                },
            )
            patch_res.raise_for_status()
        else:
            doc_hash = hashlib.sha256(f"{parcel_id}:custf:{now_iso}".encode()).hexdigest()
            post_res = client.post(
                f"{url}/rest/v1/legality_documents",
                headers=_pg_headers(),
                json={
                    "parcel_id": parcel_id,
                    "producer_id": row["producer_id"],
                    "document_type": "CUSTF_SEMARNAT",
                    "encrypted_storage_uri": (
                        f"s3://kanan-cacao-vault/semarnat/{parcel_id}-custf.pdf.enc"
                    ),
                    "sha256_hash": doc_hash,
                    "verified_by_admin": True,
                    "verified_at": now_iso,
                },
            )
            post_res.raise_for_status()

    updated = _fetch_supabase_parcels(parcel_id=parcel_id)
    if not updated:
        raise ValueError(f"No fue posible recargar la parcela {parcel_id} desde Supabase.")
    return updated[0]


def _sync_supabase_parcels(pushed_parcels: List[Dict[str, Any]]) -> Dict[str, Any]:
    url, _ = _get_supabase_config()
    now_ms = int(time.time() * 1000)
    accepted_ids: List[str] = []

    with httpx.Client(timeout=12.0) as client:
        for incoming in pushed_parcels:
            raw_pid = str(incoming["parcel_id"])
            pg_id = _as_uuid(raw_pid)
            raw_coords = incoming.get("coordinates") or [
                [-93.3900, 17.8500],
                [-93.3860, 17.8500],
                [-93.3860, 17.8465],
                [-93.3900, 17.8465],
            ]
            lint_report = lint_and_validate_polygon(raw_coords)
            area_ha = float(incoming.get("area_hectares") or lint_report["area_hectares"])
            municipality = incoming.get("municipality", "Comalcalco")
            parcel_name = (
                incoming.get("parcel_name")
                or incoming.get("producer_name")
                or f"Parcela {raw_pid}"
            )
            producer_uuid = _as_uuid(
                str(incoming.get("producer_id", "")), fallback_uuid=DEFAULT_PRODUCER_UUID
            )
            if producer_uuid not in {
                DEFAULT_PRODUCER_UUID,
                "22222222-2222-4222-8222-222222222222",
                "33333333-3333-4333-8333-333333333333",
            }:
                producer_uuid = DEFAULT_PRODUCER_UUID

            hist_bundle = build_historical_parcel_snapshots(
                pg_id, lint_report["polygon"]["coordinates"][0]
            )
            geojson_payload = {
                "type": "Feature",
                "geometry": lint_report["polygon"],
                "properties": {
                    "parcelName": parcel_name,
                    "municipality": municipality,
                    "hectares": area_ha,
                    "externalParcelId": raw_pid,
                    "extent_bbox": hist_bundle["extent_bbox"],
                    "historical_snapshots": hist_bundle["snapshots"],
                },
            }

            upsert_res = client.post(
                f"{url}/rest/v1/parcels",
                params={"on_conflict": "id"},
                headers=_pg_headers("resolution=merge-duplicates,return=representation"),
                json={
                    "id": pg_id,
                    "producer_id": producer_uuid,
                    "captured_by_technician_id": DEFAULT_TECH_UUID,
                    "parcel_name": parcel_name,
                    "municipality": municipality,
                    "area_hectares": area_ha,
                    "geojson_payload": geojson_payload,
                    "eudr_status": incoming.get("eudr_status", "VERDE"),
                    "acahual_removal_post_2020": bool(
                        incoming.get("acahual_removal_post_2020", False)
                    ),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            upsert_res.raise_for_status()

            tree_check = client.get(
                f"{url}/rest/v1/shade_tree_inventories",
                params={"parcel_id": f"eq.{pg_id}", "select": "id"},
                headers=_pg_headers(),
            )
            if tree_check.status_code == 200 and len(tree_check.json()) == 0:
                client.post(
                    f"{url}/rest/v1/shade_tree_inventories",
                    headers=_pg_headers(),
                    json={
                        "parcel_id": pg_id,
                        "common_name": "Cedro Rojo Agroforestal",
                        "scientific_name": "Cedrela odorata",
                        "category": "MADERABLE",
                        "tree_count": int(incoming.get("shade_tree_count", 110)),
                    },
                )

            audit_check = client.get(
                f"{url}/rest/v1/eudr_audits",
                params={"parcel_id": f"eq.{pg_id}", "select": "id"},
                headers=_pg_headers(),
            )
            if audit_check.status_code == 200 and len(audit_check.json()) == 0:
                client.post(
                    f"{url}/rest/v1/eudr_audits",
                    headers=_pg_headers(),
                    json={
                        "parcel_id": pg_id,
                        "status": incoming.get("eudr_status", "VERDE"),
                        "forest_cover_loss_post_2020": bool(
                            incoming.get("acahual_removal_post_2020", False)
                        ),
                        "mean_ndvi_score": round(float(incoming.get("mean_ndvi", 0.78)), 3),
                        "sentinel_scene_id": "S2A_15QVA_20261102_1_L2A",
                    },
                )

            accepted_ids.append(pg_id)

    remote_parcels = _fetch_supabase_parcels()
    return {
        "server_timestamp_ms": now_ms,
        "storage_engine": "supabase_postgis",
        "accepted_parcel_ids": accepted_ids,
        "remote_changed_parcels": remote_parcels,
    }


def _record_supabase_reception(
    parcel_id: str,
    delivered_dry_equivalent_kg: float,
    requester_role: str,
    read_point_id: str,
) -> Dict[str, Any]:
    parcels = _fetch_supabase_parcels(parcel_id=parcel_id)
    if not parcels:
        raise ValueError(f"Parcela {parcel_id} no encontrada en Supabase PostGIS.")

    parcel = parcels[0]
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
    url, _ = _get_supabase_config()
    with httpx.Client(timeout=10.0) as client:
        res = client.post(
            f"{url}/rest/v1/epcis_collection_events",
            headers=_pg_headers(),
            json={
                "parcel_id": parcel["parcel_id"],
                "producer_id": parcel["producer_id"],
                "collector_id": DEFAULT_COLLECTOR_UUID,
                "delivered_wet_kg": round(delivered_dry_equivalent_kg * 2.5, 2),
                "delivered_dry_equivalent_kg": delivered_dry_equivalent_kg,
                "read_point_id": read_point_id,
                "epcis_jsonld_payload": epcis_ev,
                "payload_sha256": epcis_ev["payloadSha256"],
            },
        )
        res.raise_for_status()

    if requester_role in ("admin_astra", "auditor_sustentabilidad"):
        return admin_eval

    return {
        "status": "REGISTERED",
        "parcel_id": parcel["parcel_id"],
        "producer_id": parcel["producer_id"],
        "delivered_dry_equivalent_kg": delivered_dry_equivalent_kg,
        "epcis_event": epcis_ev,
    }


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
                historical_snapshots_json TEXT,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        try:
            conn.execute(
                "ALTER TABLE parcels ADD COLUMN historical_snapshots_json TEXT"
            )
        except sqlite3.OperationalError:
            pass
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
                hist_bundle = build_historical_parcel_snapshots(
                    item["parcel_id"], item["coordinates"]
                )
                conn.execute(
                    """
                    INSERT INTO parcels (
                        parcel_id, producer_id, producer_name, municipality,
                        area_hectares, eudr_status, ndvi_alert_level, mean_ndvi,
                        shade_tree_count, acahual_removal_post_2020,
                        has_verified_custf_permit, cumulative_dry_kg,
                        is_topology_valid, coordinates_json,
                        historical_snapshots_json, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(hist_bundle),
                        item["updated_at_ms"],
                    ),
                )
        conn.commit()


def _row_to_parcel_dict(row: sqlite3.Row) -> Dict[str, Any]:
    coords = json.loads(row["coordinates_json"])
    has_hist_col = "historical_snapshots_json" in row.keys()
    raw_hist = row["historical_snapshots_json"] if has_hist_col else None
    if raw_hist:
        hist_bundle = json.loads(raw_hist)
        persisted_flag = True
    else:
        hist_bundle = build_historical_parcel_snapshots(row["parcel_id"], coords)
        persisted_flag = False
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
        "coordinates": coords,
        "extent_bbox": hist_bundle.get("extent_bbox"),
        "historical_snapshots": hist_bundle.get("snapshots", []),
        "persisted_on_server": persisted_flag,
        "updated_at_ms": int(row["updated_at_ms"]),
    }


def list_persisted_parcels(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    if is_supabase_rw_active(db_path):
        try:
            return _fetch_supabase_parcels()
        except httpx.HTTPError:
            pass

    init_local_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM parcels ORDER BY updated_at_ms DESC, parcel_id ASC"
        ).fetchall()
        return [_row_to_parcel_dict(r) for r in rows]


def toggle_parcel_custf(
    parcel_id: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    if is_supabase_rw_active(db_path):
        return _toggle_supabase_custf(parcel_id)

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
    if is_supabase_rw_active(db_path):
        return _sync_supabase_parcels(pushed_parcels)

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
                if existing_valid and not lint_report["is_topology_valid"]:
                    continue
                if existing_ts > incoming_ts and existing_valid:
                    continue

            hist_bundle = build_historical_parcel_snapshots(pid, repaired_coords)
            conn.execute(
                """
                INSERT INTO parcels (
                    parcel_id, producer_id, producer_name, municipality,
                    area_hectares, eudr_status, ndvi_alert_level, mean_ndvi,
                    shade_tree_count, acahual_removal_post_2020,
                    has_verified_custf_permit, cumulative_dry_kg,
                    is_topology_valid, coordinates_json,
                    historical_snapshots_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    historical_snapshots_json = COALESCE(
                        parcels.historical_snapshots_json,
                        excluded.historical_snapshots_json
                    ),
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
                    json.dumps(hist_bundle),
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
            "storage_engine": "sqlite3_local",
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
    if is_supabase_rw_active(db_path):
        return _record_supabase_reception(
            parcel_id=parcel_id,
            delivered_dry_equivalent_kg=delivered_dry_equivalent_kg,
            requester_role=requester_role,
            read_point_id=read_point_id,
        )

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


def get_or_create_parcel_historical_snapshots(
    parcel_id: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Return the persisted 7 historical snapshots centered on the parcel without recalculating."""
    if is_supabase_rw_active(db_path):
        try:
            rows = _fetch_supabase_parcels(parcel_id=parcel_id)
            if rows:
                target = rows[0]
                return {
                    "parcel_id": target["parcel_id"],
                    "storage_engine": "supabase_postgis",
                    "persisted_on_server": True,
                    "extent_bbox": target["extent_bbox"],
                    "snapshots": target["historical_snapshots"],
                }
        except httpx.HTTPError:
            pass

    init_local_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Parcela {parcel_id} no encontrada en base de datos.")
        parcel = _row_to_parcel_dict(row)
        if not parcel.get("persisted_on_server"):
            hist_bundle = build_historical_parcel_snapshots(
                parcel["parcel_id"], parcel["coordinates"]
            )
            conn.execute(
                "UPDATE parcels SET historical_snapshots_json = ? WHERE parcel_id = ?",
                (json.dumps(hist_bundle), parcel_id),
            )
            conn.commit()
            snapshots = hist_bundle["snapshots"]
            bbox = hist_bundle["extent_bbox"]
        else:
            snapshots = parcel["historical_snapshots"]
            bbox = parcel["extent_bbox"]

    return {
        "parcel_id": parcel["parcel_id"],
        "storage_engine": get_active_storage_engine(db_path),
        "persisted_on_server": True,
        "extent_bbox": bbox,
        "snapshots": snapshots,
    }


def get_cached_parcel_map_image(
    parcel_id: str,
    period_key: str,
    db_path: Optional[str] = None,
    cache_dir: Optional[Path] = None,
) -> bytes:
    """Serve cached JPEG map centered on parcel, downloading only once if absent."""
    manifest = get_or_create_parcel_historical_snapshots(parcel_id, db_path=db_path)
    matching = next(
        (s for s in manifest["snapshots"] if s["period_key"] == period_key), None
    )
    if not matching:
        raise ValueError(f"Periodo {period_key} no encontrado para parcela {parcel_id}.")

    return get_or_download_cached_parcel_map_bytes(
        parcel_id=parcel_id,
        period_key=period_key,
        extent_bbox=matching["extent_bbox"],
        months_ago=int(matching["months_ago"]),
        cache_dir=cache_dir,
    )
