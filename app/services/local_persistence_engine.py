"""Supabase PostGIS and SQLite persistence engine for Kanan Cacao R/W and offline sync."""

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
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
DEFAULT_DOCS_DIR = Path(
    os.environ.get(
        "KANAN_DOCS_DIR",
        str(Path(__file__).resolve().parents[2] / "data" / "parcel_docs"),
    )
)
SUPABASE_DOCS_BUCKET = "parcel-documents"

DEFAULT_PRODUCER_UUID = "11111111-1111-4111-8111-111111111111"
DEFAULT_TECH_UUID = "44444444-4444-4444-8444-444444444444"
DEFAULT_COLLECTOR_UUID = "55555555-5555-4555-8555-555555555555"

DOCUMENT_TYPE_LABELS: Dict[str, str] = {
    "CERTIFICADO_PARCELARIO": "Certificado parcelario / ejidal",
    "TITULO_PROPIEDAD": "Título de propiedad o escritura",
    "CUSTF_SEMARNAT": "Constancia ambiental y permiso de limpieza",
}

SEED_FIELD_LOGS: List[Dict[str, Any]] = [
    {
        "id": "LOG-INICIAL-01",
        "parcel_id": "PAR-CHONTALPA-01",
        "parcel_name": "Finca La Chontalpa Lote 1",
        "producer_id": "PROD-MATEO-01",
        "producer_name": "Don Mateo Hernández (Comalcalco)",
        "activity_type": "FERTILIZACION",
        "activity_label": "Fertilización y abono orgánico",
        "performed_date": "2026-10-18",
        "fertilization_date": "2026-10-18",
        "expected_harvest_date": "2026-11-28",
        "input_used": "Composta de cáscara de cacao + micorrizas (120 kg)",
        "notes": (
            "Aplicación de abono orgánico alrededor del cajete en 280 árboles de cacao "
            "criollo tras lluvias ligeras."
        ),
        "photos": [
            {
                "id": "FOTO-DEMO-01",
                "fileName": "fertilizacion_organica_lote_norte.svg",
                "previewDataUrl": (
                    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' "
                    "width='320' height='200' viewBox='0 0 320 200'>"
                    "<rect width='320' height='200' fill='%231E5E36'/>"
                    "<circle cx='160' cy='88' r='46' fill='%234ADE80' opacity='0.75'/>"
                    "<rect x='150' y='105' width='20' height='65' fill='%2352321B'/>"
                    "<text x='160' y='185' font-family='sans-serif' font-size='13' "
                    "fill='%23FFFFFF' text-anchor='middle'>Abono aplicado en cacaotal</text></svg>"
                ),
                "capturedAtIso": "2026-10-18T09:40:00Z",
            }
        ],
        "created_at_iso": "2026-10-18T15:10:00Z",
        "updated_at_ms": 1792336200000,
    },
    {
        "id": "LOG-INICIAL-02",
        "parcel_id": "PAR-CHONTALPA-02",
        "parcel_name": "Parcela El Cacao Sagrado",
        "producer_id": "PROD-LUCIA-02",
        "producer_name": "Doña Lucía Méndez (Cunduacán)",
        "activity_type": "PODA",
        "activity_label": "Poda de formación y sombra",
        "performed_date": "2026-10-25",
        "fertilization_date": None,
        "expected_harvest_date": None,
        "input_used": "Tijeras telescópicas y pasta bordelesa cicatrizante",
        "notes": (
            "Poda fitosanitaria de ramas secas y deschuponado para mejorar la aireación "
            "y mitigar foco de monilia."
        ),
        "photos": [],
        "created_at_iso": "2026-10-25T11:20:00Z",
        "updated_at_ms": 1792927200000,
    },
]

DEFAULT_LOGBOOK_CONFIG: Dict[str, Any] = {
    "id": "global_config",
    "version": 1,
    "enable_photos": 1,
    "enable_inputs_used": 1,
    "enable_doses": 1,
    "enable_labor_and_costs": 1,
    "enable_weather_conditions": 1,
    "enable_satellite_comparison": 1,
    "enable_eudr_flags": 1,
    "enable_custom_fields": 1,
    "allow_producer_custom_tags": 1,
    "min_required_photos": 0,
    "updated_at_iso": "2026-11-18T00:00:00Z",
    "updated_by": "sistema_astra",
}

DEFAULT_LOGBOOK_ACTIVITIES: List[Dict[str, Any]] = [
    {
        "activity_key": "MANEJO_SOMBRA",
        "name": "Manejo de Sombra y Poda Forestal",
        "category": "AGROFORESTERIA",
        "icon": "🌳",
        "description": (
            "Regulación de luz solar mediante poda de árboles de sombra "
            "(guamo, cedro, macuilí) para prevenir exceso de humedad."
        ),
        "default_fields_json": json.dumps(["labor_hours", "workers_count"]),
        "requires_inputs": 0,
        "requires_dose": 0,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 1,
    },
    {
        "activity_key": "PODA_CACAO",
        "name": "Poda de Cacao (Formación / Mantenimiento / Fitosanitaria)",
        "category": "MANEJO_CULTIVO",
        "icon": "✂️",
        "description": (
            "Eliminación de chupones, ramas bajeras, enfermas o secas "
            "para facilitar aireación y floración."
        ),
        "default_fields_json": json.dumps(["labor_hours", "workers_count"]),
        "requires_inputs": 0,
        "requires_dose": 0,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 2,
    },
    {
        "activity_key": "DESMOMIFICACION_FITOSANITARIA",
        "name": "Control Fitosanitario y Desmomificación",
        "category": "FITOSANIDAD",
        "icon": "🩺",
        "description": (
            "Remoción de frutos afectados por moniliasis o escoba de bruja "
            "y cubrimiento con hojarasca en el suelo."
        ),
        "default_fields_json": json.dumps(["labor_hours", "workers_count", "notes"]),
        "requires_inputs": 0,
        "requires_dose": 0,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 3,
    },
    {
        "activity_key": "FERTILIZACION_ORGANICA",
        "name": "Nutrición y Biofertilización Orgánica",
        "category": "NUTRICION",
        "icon": "🌱",
        "description": (
            "Aplicación de composta, bocashi, lixiviados de lombriz o harina de rocas "
            "en corona al suelo."
        ),
        "default_fields_json": json.dumps(["input_used", "dose", "cost_mxn"]),
        "requires_inputs": 1,
        "requires_dose": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 4,
    },
    {
        "activity_key": "CONTROL_MALEZAS",
        "name": "Chapeo y Manejo de Coberturas",
        "category": "MANEJO_CULTIVO",
        "icon": "🌾",
        "description": (
            "Mantenimiento mecánico o manual de arvenses nobles para conservar "
            "humedad y evitar competencia."
        ),
        "default_fields_json": json.dumps(["labor_hours", "workers_count", "cost_mxn"]),
        "requires_inputs": 0,
        "requires_dose": 0,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 5,
    },
    {
        "activity_key": "RIEGO_DRENAJE",
        "name": "Riego y Mantenimiento de Drenes",
        "category": "INFRAESTRUCTURA",
        "icon": "💧",
        "description": (
            "Limpieza de zanjas de drenaje parcelario o activación de riego de "
            "auxilio contra estrés hídrico."
        ),
        "default_fields_json": json.dumps(["labor_hours"]),
        "requires_inputs": 0,
        "requires_dose": 0,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 6,
    },
    {
        "activity_key": "APLICACION_BIOINSUMO",
        "name": "Aplicación de Caldo Bordelés / Sulfocálcico",
        "category": "FITOSANIDAD",
        "icon": "🧪",
        "description": (
            "Fumigación preventiva de biofungicidas autorizados para "
            "agricultura orgánica."
        ),
        "default_fields_json": json.dumps(["input_used", "dose", "weather_condition"]),
        "requires_inputs": 1,
        "requires_dose": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 7,
    },
    {
        "activity_key": "INSPECCION_FLORACION",
        "name": "Monitoreo de Floración y Cuajado",
        "category": "MONITOREO",
        "icon": "🔍",
        "description": (
            "Conteo muestral de cojines florales, chilillos formados y "
            "polinizadores en cacaotales."
        ),
        "default_fields_json": json.dumps(["notes", "photos"]),
        "requires_inputs": 0,
        "requires_dose": 0,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 8,
    },
]

DEFAULT_LOGBOOK_INPUTS: List[Dict[str, Any]] = [
    {
        "input_key": "COMPOSTA_BOCASHI",
        "name": "Composta / Bocashi Artesanal",
        "type": "FERTILIZANTE_SOLIDO",
        "default_unit": "kg/arbol",
        "is_organic_certified": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 1,
    },
    {
        "input_key": "LIXIVIADO_LOMBRIZ",
        "name": "Lixiviado / Humus Líquido de Lombriz",
        "type": "BIOFERTILIZANTE_LIQUIDO",
        "default_unit": "L/ha",
        "is_organic_certified": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 2,
    },
    {
        "input_key": "CALDO_BORDELES",
        "name": "Caldo Bordelés (Sulfato de cobre + Cal)",
        "type": "BIOFUNGICIDA",
        "default_unit": "L/mochila",
        "is_organic_certified": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 3,
    },
    {
        "input_key": "CALDO_SULFOCALCICO",
        "name": "Caldo Sulfocálcico",
        "type": "BIOFUNGICIDA",
        "default_unit": "ml/L agua",
        "is_organic_certified": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 4,
    },
    {
        "input_key": "HARINA_ROCAS",
        "name": "Harina de Rocas Basálticas / Zeolita",
        "type": "ENMIENDA_MINERAL",
        "default_unit": "kg/arbol",
        "is_organic_certified": 1,
        "is_active": 1,
        "is_archived": 0,
        "sort_order": 5,
    },
]

DEFAULT_LOGBOOK_CUSTOM_FIELDS: List[Dict[str, Any]] = []

PARCEL_SELECT_FIELDS = (
    "id,producer_id,parcel_name,municipality,area_hectares,geojson_payload,"
    "eudr_status,acahual_removal_post_2020,captured_at,created_at,updated_at,"
    "profiles!parcels_producer_id_fkey(full_name,is_blocked_by_custf),"
    "eudr_audits(mean_ndvi_score,sentinel_scene_id),"
    "shade_tree_inventories(tree_count),"
    "legality_documents(id,document_type,encrypted_storage_uri,"
    "sha256_hash,verified_by_admin,uploaded_at,verified_at),"
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


def _format_legal_doc_row(parcel_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    doc_id = str(doc.get("id") or "legacy-doc")
    doc_type = str(doc.get("document_type") or "CERTIFICADO_PARCELARIO")
    uri = str(doc.get("encrypted_storage_uri") or "")
    file_name = uri.rsplit("/", 1)[-1] if "/" in uri else f"{doc_type.lower()}.pdf"
    if file_name.endswith(".enc"):
        file_name = file_name[:-4]
    can_download = uri.startswith("storage://") or uri.startswith("local://")
    return {
        "id": doc_id,
        "parcel_id": parcel_id,
        "document_type": doc_type,
        "document_label": DOCUMENT_TYPE_LABELS.get(doc_type, doc_type),
        "file_name": file_name,
        "sha256_hash": str(doc.get("sha256_hash") or ""),
        "verified_by_admin": bool(doc.get("verified_by_admin")),
        "uploaded_at": doc.get("uploaded_at") or doc.get("verified_at"),
        "download_url": (
            f"/api/v1/parcels/{parcel_id}/documents/{doc_id}/file"
            if can_download
            else None
        ),
    }


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
    registered_at_iso = (
        row.get("captured_at") or row.get("created_at") or row.get("updated_at")
    )
    mapped_documents = [_format_legal_doc_row(str(row["id"]), d) for d in docs]

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
    has_real_distinct_dates = (
        isinstance(persisted_snapshots, list)
        and len(persisted_snapshots) == 7
        and all(
            item.get("capture_source") == "real_src_date"
            and bool(item.get("wayback_release"))
            for item in persisted_snapshots
        )
        and len({item.get("captured_date_iso") for item in persisted_snapshots}) == 7
        and len({item.get("wayback_release") for item in persisted_snapshots}) == 7
    )
    if (
        has_real_distinct_dates
        and isinstance(persisted_bbox, list)
        and len(persisted_bbox) == 4
    ):
        historical_snapshots = persisted_snapshots
        extent_bbox = persisted_bbox
        snapshots_cached_on_server = True
    else:
        hist_bundle = build_historical_parcel_snapshots(
            row["id"],
            coords,
            registered_at_iso=registered_at_iso,
            probe_live=False,
        )
        historical_snapshots = hist_bundle["snapshots"]
        extent_bbox = hist_bundle["extent_bbox"]
        snapshots_cached_on_server = False

    audit_log = properties.get("registration_audit_log") or []
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
        "registered_at_iso": registered_at_iso,
        "registration_audit_log": audit_log if isinstance(audit_log, list) else [],
        "documents": mapped_documents,
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
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(f"{url}/rest/v1/parcels", params=params, headers=_pg_headers())
        resp.raise_for_status()
        rows = resp.json()

        def _HydrateRow(raw_row: Dict[str, Any]) -> Dict[str, Any]:
            mapped = supabase_row_to_parcel_dict(raw_row)
            if not mapped.get("persisted_on_server"):
                reg_iso = mapped.get("registered_at_iso")
                hist_live = build_historical_parcel_snapshots(
                    raw_row["id"],
                    mapped["coordinates"],
                    registered_at_iso=reg_iso,
                    probe_live=True,
                    materialize_assets=True,
                )
                mapped["historical_snapshots"] = hist_live["snapshots"]
                mapped["extent_bbox"] = hist_live["extent_bbox"]
                payload = dict(raw_row.get("geojson_payload") or {})
                props = dict(payload.get("properties") or {})
                props["extent_bbox"] = mapped["extent_bbox"]
                props["historical_snapshots"] = mapped["historical_snapshots"]
                payload["properties"] = props
                try:
                    with httpx.Client(timeout=15.0) as patch_client:
                        patch_client.patch(
                            f"{url}/rest/v1/parcels",
                            params={"id": f"eq.{raw_row['id']}"},
                            headers=_pg_headers(),
                            json={"geojson_payload": payload},
                        )
                    mapped["persisted_on_server"] = True
                except httpx.HTTPError:
                    pass
            return mapped

        stale_count = sum(
            1 for r in rows if not supabase_row_to_parcel_dict(r).get("persisted_on_server")
        )
        if stale_count > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(3, stale_count)) as executor:
                results = list(executor.map(_HydrateRow, rows))
        else:
            results = [_HydrateRow(r) for r in rows]
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

            reg_iso = (
                incoming.get("registered_at_iso")
                or datetime.now(timezone.utc).isoformat()
            )
            hist_bundle = build_historical_parcel_snapshots(
                pg_id,
                lint_report["polygon"]["coordinates"][0],
                registered_at_iso=reg_iso,
                probe_live=True,
                materialize_assets=True,
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
                    "captured_at": reg_iso,
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
                parcel_name TEXT,
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
        try:
            conn.execute(
                "ALTER TABLE parcels ADD COLUMN parcel_name TEXT"
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parcel_documents (
                id TEXT PRIMARY KEY,
                parcel_id TEXT NOT NULL,
                document_type TEXT NOT NULL,
                file_name TEXT NOT NULL,
                content_type TEXT NOT NULL,
                sha256_hash TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                verified_by_admin INTEGER NOT NULL DEFAULT 1,
                uploaded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registration_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parcel_id TEXT NOT NULL,
                previous_date_iso TEXT NOT NULL,
                new_date_iso TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                changed_by_role TEXT NOT NULL,
                changed_at_iso TEXT NOT NULL,
                confirmation_text TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS field_logbook_entries (
                id TEXT PRIMARY KEY,
                parcel_id TEXT NOT NULL,
                parcel_name TEXT NOT NULL,
                producer_id TEXT,
                producer_name TEXT,
                activity_type TEXT NOT NULL,
                activity_label TEXT NOT NULL,
                performed_date TEXT NOT NULL,
                fertilization_date TEXT,
                expected_harvest_date TEXT,
                input_used TEXT,
                notes TEXT NOT NULL,
                photos_json TEXT NOT NULL DEFAULT '[]',
                created_at_iso TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        log_count = conn.execute("SELECT COUNT(*) FROM field_logbook_entries").fetchone()[0]
        if log_count == 0:
            for log_item in SEED_FIELD_LOGS:
                conn.execute(
                    """
                    INSERT INTO field_logbook_entries (
                        id, parcel_id, parcel_name, producer_id, producer_name,
                        activity_type, activity_label, performed_date, fertilization_date,
                        expected_harvest_date, input_used, notes, photos_json,
                        created_at_iso, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        log_item["id"],
                        log_item["parcel_id"],
                        log_item["parcel_name"],
                        log_item.get("producer_id"),
                        log_item.get("producer_name"),
                        log_item["activity_type"],
                        log_item["activity_label"],
                        log_item["performed_date"],
                        log_item.get("fertilization_date"),
                        log_item.get("expected_harvest_date"),
                        log_item.get("input_used"),
                        log_item["notes"],
                        json.dumps(log_item.get("photos", [])),
                        log_item["created_at_iso"],
                        log_item["updated_at_ms"],
                    ),
                )
        for extra_log_col in (
            "ALTER TABLE field_logbook_entries ADD COLUMN dose TEXT",
            "ALTER TABLE field_logbook_entries ADD COLUMN cost_mxn REAL",
            "ALTER TABLE field_logbook_entries ADD COLUMN labor_hours REAL",
            "ALTER TABLE field_logbook_entries ADD COLUMN workers_count INTEGER",
            "ALTER TABLE field_logbook_entries ADD COLUMN weather_condition TEXT",
            "ALTER TABLE field_logbook_entries ADD COLUMN custom_fields_json TEXT",
        ):
            try:
                conn.execute(extra_log_col)
            except sqlite3.OperationalError:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logbook_config (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL DEFAULT 1,
                enable_photos INTEGER NOT NULL DEFAULT 1,
                enable_inputs_used INTEGER NOT NULL DEFAULT 1,
                enable_doses INTEGER NOT NULL DEFAULT 1,
                enable_labor_and_costs INTEGER NOT NULL DEFAULT 1,
                enable_weather_conditions INTEGER NOT NULL DEFAULT 1,
                enable_satellite_comparison INTEGER NOT NULL DEFAULT 1,
                enable_eudr_flags INTEGER NOT NULL DEFAULT 1,
                enable_custom_fields INTEGER NOT NULL DEFAULT 1,
                allow_producer_custom_tags INTEGER NOT NULL DEFAULT 1,
                min_required_photos INTEGER NOT NULL DEFAULT 0,
                updated_at_iso TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logbook_activity_catalog (
                activity_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                icon TEXT NOT NULL,
                description TEXT NOT NULL,
                default_fields_json TEXT NOT NULL DEFAULT '[]',
                requires_inputs INTEGER NOT NULL DEFAULT 0,
                requires_dose INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_archived INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at_iso TEXT NOT NULL,
                updated_at_iso TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logbook_input_catalog (
                input_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                default_unit TEXT NOT NULL,
                is_organic_certified INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_archived INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at_iso TEXT NOT NULL,
                updated_at_iso TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logbook_custom_fields (
                field_key TEXT PRIMARY KEY,
                activity_key TEXT,
                label TEXT NOT NULL,
                field_type TEXT NOT NULL,
                options_json TEXT NOT NULL DEFAULT '[]',
                required INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_archived INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at_iso TEXT NOT NULL,
                updated_at_iso TEXT NOT NULL
            )
            """
        )

        cfg_count = conn.execute("SELECT COUNT(*) FROM logbook_config").fetchone()[0]
        if cfg_count == 0:
            c = DEFAULT_LOGBOOK_CONFIG
            conn.execute(
                """
                INSERT INTO logbook_config (
                    id, version, enable_photos, enable_inputs_used, enable_doses,
                    enable_labor_and_costs, enable_weather_conditions,
                    enable_satellite_comparison, enable_eudr_flags, enable_custom_fields,
                    allow_producer_custom_tags, min_required_photos,
                    updated_at_iso, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    c["id"],
                    c["version"],
                    c["enable_photos"],
                    c["enable_inputs_used"],
                    c["enable_doses"],
                    c["enable_labor_and_costs"],
                    c["enable_weather_conditions"],
                    c["enable_satellite_comparison"],
                    c["enable_eudr_flags"],
                    c["enable_custom_fields"],
                    c["allow_producer_custom_tags"],
                    c["min_required_photos"],
                    c["updated_at_iso"],
                    c["updated_by"],
                ),
            )

        act_count = conn.execute("SELECT COUNT(*) FROM logbook_activity_catalog").fetchone()[0]
        if act_count == 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            for act in DEFAULT_LOGBOOK_ACTIVITIES:
                conn.execute(
                    """
                    INSERT INTO logbook_activity_catalog (
                        activity_key, name, category, icon, description,
                        default_fields_json, requires_inputs, requires_dose,
                        is_active, is_archived, sort_order, created_at_iso, updated_at_iso
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        act["activity_key"],
                        act["name"],
                        act["category"],
                        act["icon"],
                        act["description"],
                        act["default_fields_json"],
                        act["requires_inputs"],
                        act["requires_dose"],
                        act["is_active"],
                        act["is_archived"],
                        act["sort_order"],
                        now_iso,
                        now_iso,
                    ),
                )

        inp_count = conn.execute("SELECT COUNT(*) FROM logbook_input_catalog").fetchone()[0]
        if inp_count == 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            for inp in DEFAULT_LOGBOOK_INPUTS:
                conn.execute(
                    """
                    INSERT INTO logbook_input_catalog (
                        input_key, name, type, default_unit, is_organic_certified,
                        is_active, is_archived, sort_order, created_at_iso, updated_at_iso
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inp["input_key"],
                        inp["name"],
                        inp["type"],
                        inp["default_unit"],
                        inp["is_organic_certified"],
                        inp["is_active"],
                        inp["is_archived"],
                        inp["sort_order"],
                        now_iso,
                        now_iso,
                    ),
                )

        for extra_col in (
            "ALTER TABLE parcels ADD COLUMN registered_at_iso TEXT",
            "ALTER TABLE parcels ADD COLUMN historical_snapshots_json TEXT",
        ):
            try:
                conn.execute(extra_col)
            except sqlite3.OperationalError:
                pass
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
        "parcel_name": (
            row["parcel_name"]
            if "parcel_name" in row.keys() and row["parcel_name"]
            else row["producer_name"]
        ),
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
        "registered_at_iso": (
            row["registered_at_iso"]
            if "registered_at_iso" in row.keys() and row["registered_at_iso"]
            else hist_bundle.get("registered_at_iso")
        ),
        "registration_audit_log": hist_bundle.get("registration_audit_log", []),
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
    parcel_id: str,
    db_path: Optional[str] = None,
    reference_date_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Return 7 distinct cloud-free historical snapshots centered on the parcel."""
    if is_supabase_rw_active(db_path):
        try:
            rows = _fetch_supabase_parcels(parcel_id=parcel_id)
            if rows:
                target = rows[0]
                if reference_date_iso:
                    custom_hist = build_historical_parcel_snapshots(
                        target["parcel_id"],
                        target["coordinates"],
                        registered_at_iso=target.get("registered_at_iso"),
                        reference_date_iso=reference_date_iso,
                        probe_live=True,
                        materialize_assets=False,
                    )
                    return {
                        "parcel_id": target["parcel_id"],
                        "storage_engine": "supabase_postgis",
                        "persisted_on_server": True,
                        "registered_at_iso": target.get("registered_at_iso"),
                        "extent_bbox": custom_hist["extent_bbox"],
                        "snapshots": custom_hist["snapshots"],
                    }
                return {
                    "parcel_id": target["parcel_id"],
                    "storage_engine": "supabase_postgis",
                    "persisted_on_server": True,
                    "registered_at_iso": target.get("registered_at_iso"),
                    "extent_bbox": target["extent_bbox"],
                    "snapshots": target["historical_snapshots"],
                    "coordinates": target["coordinates"],
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
        if reference_date_iso:
            custom_hist = build_historical_parcel_snapshots(
                parcel["parcel_id"],
                parcel["coordinates"],
                reference_date_iso=reference_date_iso,
                probe_live=False,
            )
            snapshots = custom_hist["snapshots"]
            bbox = custom_hist["extent_bbox"]
        elif not parcel.get("persisted_on_server"):
            hist_bundle = build_historical_parcel_snapshots(
                parcel["parcel_id"], parcel["coordinates"], probe_live=False
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
        "coordinates": parcel["coordinates"],
        "snapshots": snapshots,
    }


def get_cached_parcel_map_image(
    parcel_id: str,
    period_key: str,
    db_path: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    wayback_release: Optional[str] = None,
) -> bytes:
    """Serve cached JPEG map centered on parcel, composing/downloading only once."""
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
        wayback_release=wayback_release or matching.get("wayback_release"),
        coordinates=manifest.get("coordinates"),
        cached_image_url=None if wayback_release else matching.get("cached_image_url"),
    )


def _guess_content_type(file_name: str) -> str:
    lower = file_name.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def list_parcel_documents(
    parcel_id: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return all legal documents attached to a parcel."""
    if is_supabase_rw_active(db_path):
        url, _ = _get_supabase_config()
        with httpx.Client(timeout=10.0) as client:
            res = client.get(
                f"{url}/rest/v1/legality_documents",
                params={
                    "parcel_id": f"eq.{parcel_id}",
                    "select": (
                        "id,parcel_id,document_type,encrypted_storage_uri,"
                        "sha256_hash,verified_by_admin,uploaded_at,verified_at"
                    ),
                    "order": "uploaded_at.desc",
                },
                headers=_pg_headers(),
            )
            res.raise_for_status()
            return [_format_legal_doc_row(parcel_id, d) for d in res.json()]

    init_local_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM parcel_documents WHERE parcel_id = ? ORDER BY uploaded_at DESC",
            (parcel_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "parcel_id": r["parcel_id"],
                "document_type": r["document_type"],
                "document_label": DOCUMENT_TYPE_LABELS.get(
                    r["document_type"], r["document_type"]
                ),
                "file_name": r["file_name"],
                "sha256_hash": r["sha256_hash"],
                "verified_by_admin": bool(r["verified_by_admin"]),
                "uploaded_at": r["uploaded_at"],
                "download_url": (
                    f"/api/v1/parcels/{parcel_id}/documents/{r['id']}/file"
                ),
            }
            for r in rows
        ]


def upload_parcel_document(
    parcel_id: str,
    document_type: str,
    file_name: str,
    file_bytes: bytes,
    content_type: Optional[str] = None,
    db_path: Optional[str] = None,
    docs_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate, hash, store file in Supabase Storage (or disk), and record DB metadata."""
    if document_type not in DOCUMENT_TYPE_LABELS:
        raise ValueError(f"Tipo de documento no permitido: {document_type}")
    if not file_bytes:
        raise ValueError("El archivo adjunto está vacío.")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise ValueError("El archivo supera el límite de 10 MB.")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(file_name).name) or "documento.pdf"
    doc_id = str(uuid.uuid4())
    sha256_hash = hashlib.sha256(file_bytes).hexdigest()
    resolved_ctype = content_type or _guess_content_type(safe_name)
    now_iso = datetime.now(timezone.utc).isoformat()

    if is_supabase_rw_active(db_path):
        url, key = _get_supabase_config()
        with httpx.Client(timeout=15.0) as client:
            p_res = client.get(
                f"{url}/rest/v1/parcels",
                params={"id": f"eq.{parcel_id}", "select": "id,producer_id"},
                headers=_pg_headers(),
            )
            p_res.raise_for_status()
            p_rows = p_res.json()
            if not p_rows:
                raise ValueError(f"Parcela {parcel_id} no encontrada.")
            producer_id = p_rows[0]["producer_id"]

            object_path = f"{parcel_id}/{doc_id}/{safe_name}"
            st_res = client.post(
                f"{url}/storage/v1/object/{SUPABASE_DOCS_BUCKET}/{object_path}",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": resolved_ctype,
                    "x-upsert": "true",
                },
                content=file_bytes,
            )
            st_res.raise_for_status()

            db_res = client.post(
                f"{url}/rest/v1/legality_documents",
                headers=_pg_headers("return=representation"),
                json={
                    "id": doc_id,
                    "parcel_id": parcel_id,
                    "producer_id": producer_id,
                    "document_type": document_type,
                    "encrypted_storage_uri": f"storage://{SUPABASE_DOCS_BUCKET}/{object_path}",
                    "sha256_hash": sha256_hash,
                    "verified_by_admin": True,
                    "verified_at": now_iso,
                    "uploaded_at": now_iso,
                    "updated_at": now_iso,
                },
            )
            db_res.raise_for_status()
            inserted = db_res.json()[0]
            return _format_legal_doc_row(parcel_id, inserted)

    init_local_db(db_path)
    target_dir = docs_dir or DEFAULT_DOCS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_pid = re.sub(r"[^a-zA-Z0-9_-]", "_", parcel_id)
    local_file = target_dir / f"{safe_pid}_{doc_id}_{safe_name}"
    local_file.write_bytes(file_bytes)

    with _connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        if not exists:
            raise ValueError(f"Parcela {parcel_id} no encontrada en base local.")
        conn.execute(
            """
            INSERT INTO parcel_documents (
                id, parcel_id, document_type, file_name, content_type,
                sha256_hash, storage_path, verified_by_admin, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                doc_id,
                parcel_id,
                document_type,
                safe_name,
                resolved_ctype,
                sha256_hash,
                str(local_file),
                now_iso,
            ),
        )
        conn.commit()

    return {
        "id": doc_id,
        "parcel_id": parcel_id,
        "document_type": document_type,
        "document_label": DOCUMENT_TYPE_LABELS[document_type],
        "file_name": safe_name,
        "sha256_hash": sha256_hash,
        "verified_by_admin": True,
        "uploaded_at": now_iso,
        "download_url": f"/api/v1/parcels/{parcel_id}/documents/{doc_id}/file",
    }


def get_parcel_document_file(
    parcel_id: str,
    doc_id: str,
    db_path: Optional[str] = None,
) -> Tuple[bytes, str, str]:
    """Download file bytes, content_type and file_name for a stored parcel document."""
    if is_supabase_rw_active(db_path):
        url, key = _get_supabase_config()
        with httpx.Client(timeout=15.0) as client:
            res = client.get(
                f"{url}/rest/v1/legality_documents",
                params={
                    "id": f"eq.{doc_id}",
                    "parcel_id": f"eq.{parcel_id}",
                    "select": "id,document_type,encrypted_storage_uri",
                },
                headers=_pg_headers(),
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                raise ValueError(f"Documento {doc_id} no encontrado para parcela {parcel_id}.")
            uri = str(rows[0].get("encrypted_storage_uri") or "")
            prefix = f"storage://{SUPABASE_DOCS_BUCKET}/"
            if not uri.startswith(prefix):
                raise ValueError("Este documento legado solo cuenta con folio de referencia.")
            object_path = uri[len(prefix):]
            file_name = object_path.rsplit("/", 1)[-1]
            dl_res = client.get(
                f"{url}/storage/v1/object/{SUPABASE_DOCS_BUCKET}/{object_path}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            dl_res.raise_for_status()
            ctype = dl_res.headers.get("content-type") or _guess_content_type(file_name)
            return dl_res.content, ctype, file_name

    init_local_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parcel_documents WHERE id = ? AND parcel_id = ?",
            (doc_id, parcel_id),
        ).fetchone()
        if not row:
            raise ValueError(f"Documento {doc_id} no encontrado.")
        path = Path(row["storage_path"])
        if not path.exists():
            raise ValueError("Archivo físico no disponible en disco.")
        return path.read_bytes(), row["content_type"], row["file_name"]


def update_parcel_registration_date(
    parcel_id: str,
    new_date_iso: str,
    confirmation_word: str,
    changed_by: str,
    changed_by_role: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Allow ONLY admin_astra to modify parcel registration date with explicit 'confirmar'
    confirmation, recalculating high-res snapshots and saving an immutable audit log entry.
    """
    if changed_by_role.strip().lower() != "admin_astra":
        raise PermissionError(
            "Acceso denegado: únicamente un administrador (admin_astra) puede cambiar "
            "la fecha de registro de una parcela."
        )
    if confirmation_word.strip().lower() != "confirmar":
        raise ValueError(
            "Confirmación inválida: debes escribir exactamente la palabra 'confirmar' "
            "para alterar la fecha de registro."
        )
    try:
        parsed_new = date.fromisoformat(new_date_iso.strip()[:10])
    except ValueError as exc:
        raise ValueError("Formato de fecha inválido; se esperaba YYYY-MM-DD.") from exc

    new_iso = f"{parsed_new.isoformat()}T12:00:00+00:00"
    changed_at_iso = datetime.now(timezone.utc).isoformat()

    if is_supabase_rw_active(db_path):
        url, _ = _get_supabase_config()
        with httpx.Client(timeout=20.0) as client:
            res = client.get(
                f"{url}/rest/v1/parcels",
                params={"id": f"eq.{parcel_id}", "select": PARCEL_SELECT_FIELDS},
                headers=_pg_headers(),
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                raise ValueError(f"Parcela {parcel_id} no encontrada.")
            raw_row = rows[0]
            previous_raw = (
                raw_row.get("captured_at")
                or raw_row.get("created_at")
                or raw_row.get("updated_at")
                or changed_at_iso
            )
            mapped = supabase_row_to_parcel_dict(raw_row)
            hist_live = build_historical_parcel_snapshots(
                parcel_id,
                mapped["coordinates"],
                registered_at_iso=new_iso,
                probe_live=True,
                materialize_assets=True,
            )
            payload = dict(raw_row.get("geojson_payload") or {})
            props = dict(payload.get("properties") or {})
            audit_list = list(props.get("registration_audit_log") or [])
            audit_entry = {
                "previous_date_iso": str(previous_raw)[:10],
                "new_date_iso": parsed_new.isoformat(),
                "changed_by": changed_by.strip() or "Administrador ASTRA",
                "changed_by_role": changed_by_role,
                "changed_at_iso": changed_at_iso,
                "confirmation_text": "confirmar",
            }
            audit_list.insert(0, audit_entry)
            props["extent_bbox"] = hist_live["extent_bbox"]
            props["historical_snapshots"] = hist_live["snapshots"]
            props["registration_audit_log"] = audit_list
            payload["properties"] = props

            patch_res = client.patch(
                f"{url}/rest/v1/parcels",
                params={"id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
                json={
                    "captured_at": new_iso,
                    "geojson_payload": payload,
                    "updated_at": changed_at_iso,
                },
            )
            patch_res.raise_for_status()
        updated = _fetch_supabase_parcels(parcel_id=parcel_id)
        if not updated:
            raise ValueError(f"No se pudo recargar la parcela {parcel_id}.")
        return updated[0]

    init_local_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Parcela {parcel_id} no encontrada en base local.")
        parcel = _row_to_parcel_dict(row)
        prev_iso = str(parcel.get("registered_at_iso") or changed_at_iso)[:10]
        hist_bundle = build_historical_parcel_snapshots(
            parcel_id,
            parcel["coordinates"],
            registered_at_iso=new_iso,
            probe_live=False,
        )
        audit_list = list(parcel.get("registration_audit_log") or [])
        audit_entry = {
            "previous_date_iso": prev_iso,
            "new_date_iso": parsed_new.isoformat(),
            "changed_by": changed_by.strip() or "Administrador ASTRA",
            "changed_by_role": changed_by_role,
            "changed_at_iso": changed_at_iso,
            "confirmation_text": "confirmar",
        }
        audit_list.insert(0, audit_entry)
        hist_bundle["registration_audit_log"] = audit_list
        conn.execute(
            """
            INSERT INTO registration_audit_logs (
                parcel_id, previous_date_iso, new_date_iso,
                changed_by, changed_by_role, changed_at_iso, confirmation_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parcel_id,
                prev_iso,
                parsed_new.isoformat(),
                audit_entry["changed_by"],
                changed_by_role,
                changed_at_iso,
                "confirmar",
            ),
        )
        conn.execute(
            """
            UPDATE parcels
            SET registered_at_iso = ?, historical_snapshots_json = ?, updated_at_ms = ?
            WHERE parcel_id = ?
            """,
            (
                new_iso,
                json.dumps(hist_bundle),
                int(time.time() * 1000),
                parcel_id,
            ),
        )
        conn.commit()
        updated_row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        return _row_to_parcel_dict(updated_row)


def update_parcel_metadata(
    parcel_id: str,
    parcel_name: Optional[str] = None,
    producer_name: Optional[str] = None,
    municipality: Optional[str] = None,
    area_hectares: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Allow modifying parcel name, producer name, municipality and area.
    Updates Supabase PostGIS (both parcels and profiles table) when active,
    or local SQLite database.
    """
    if is_supabase_rw_active(db_path):
        url, _ = _get_supabase_config()
        with httpx.Client(timeout=15.0) as client:
            res = client.get(
                f"{url}/rest/v1/parcels",
                params={"id": f"eq.{parcel_id}", "select": PARCEL_SELECT_FIELDS},
                headers=_pg_headers(),
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                raise ValueError(f"Parcela {parcel_id} no encontrada en Supabase.")
            row = rows[0]

            patch_payload: Dict[str, Any] = {
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if parcel_name and parcel_name.strip():
                patch_payload["parcel_name"] = parcel_name.strip()
            if municipality and municipality.strip():
                patch_payload["municipality"] = municipality.strip()
            if area_hectares is not None and area_hectares > 0:
                patch_payload["area_hectares"] = round(area_hectares, 4)

            payload = dict(row.get("geojson_payload") or {})
            props = dict(payload.get("properties") or {})
            if parcel_name and parcel_name.strip():
                props["parcel_name"] = parcel_name.strip()
            if producer_name and producer_name.strip():
                props["producer_name"] = producer_name.strip()
            if municipality and municipality.strip():
                props["municipality"] = municipality.strip()
            payload["properties"] = props
            patch_payload["geojson_payload"] = payload

            client.patch(
                f"{url}/rest/v1/parcels",
                params={"id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
                json=patch_payload,
            )

            if producer_name and producer_name.strip() and row.get("producer_id"):
                try:
                    client.patch(
                        f"{url}/rest/v1/profiles",
                        params={"id": f"eq.{row['producer_id']}"},
                        headers=_pg_headers(),
                        json={
                            "full_name": producer_name.strip(),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                except httpx.HTTPError:
                    pass

        updated = _fetch_supabase_parcels(parcel_id=parcel_id)
        if not updated:
            raise ValueError(f"No se pudo recargar la parcela {parcel_id} actualizada.")
        return updated[0]

    init_local_db(db_path)
    now_ms = int(time.time() * 1000)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Parcela {parcel_id} no encontrada en base local.")

        new_parcel_name = (
            parcel_name.strip()
            if parcel_name and parcel_name.strip()
            else row["parcel_name"]
        )
        new_producer_name = (
            producer_name.strip()
            if producer_name and producer_name.strip()
            else row["producer_name"]
        )
        new_municipality = (
            municipality.strip()
            if municipality and municipality.strip()
            else row["municipality"]
        )
        new_area = (
            float(area_hectares)
            if area_hectares is not None and area_hectares > 0
            else float(row["area_hectares"])
        )

        conn.execute(
            """
            UPDATE parcels
            SET parcel_name = ?, producer_name = ?, municipality = ?,
                area_hectares = ?, updated_at_ms = ?
            WHERE parcel_id = ?
            """,
            (
                new_parcel_name,
                new_producer_name,
                new_municipality,
                new_area,
                now_ms,
                parcel_id,
            ),
        )
        conn.commit()
        updated_row = conn.execute(
            "SELECT * FROM parcels WHERE parcel_id = ?", (parcel_id,)
        ).fetchone()
        return _row_to_parcel_dict(updated_row)


def delete_parcel_record(parcel_id: str, db_path: Optional[str] = None) -> bool:
    """
    Delete a parcel record and all its associated dependencies from Supabase or local SQLite.
    """
    if is_supabase_rw_active(db_path):
        url, _ = _get_supabase_config()
        with httpx.Client(timeout=15.0) as client:
            client.delete(
                f"{url}/rest/v1/legality_documents",
                params={"parcel_id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            client.delete(
                f"{url}/rest/v1/eudr_audits",
                params={"parcel_id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            client.delete(
                f"{url}/rest/v1/shade_tree_inventories",
                params={"parcel_id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            client.delete(
                f"{url}/rest/v1/harvest_benefit_logs",
                params={"parcel_id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            client.delete(
                f"{url}/rest/v1/pre_harvest_logs",
                params={"parcel_id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            client.delete(
                f"{url}/rest/v1/epcis_collection_events",
                params={"parcel_id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            del_res = client.delete(
                f"{url}/rest/v1/parcels",
                params={"id": f"eq.{parcel_id}"},
                headers=_pg_headers(),
            )
            del_res.raise_for_status()
            return True

    init_local_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM parcel_documents WHERE parcel_id = ?", (parcel_id,))
        conn.execute("DELETE FROM parcels WHERE parcel_id = ?", (parcel_id,))
        conn.commit()
        return True


def _row_to_field_log_dict(row: sqlite3.Row) -> Dict[str, Any]:
    photos = []
    if "photos_json" in row.keys() and row["photos_json"]:
        try:
            photos = json.loads(row["photos_json"])
        except Exception:
            photos = []
    custom_fields = {}
    if "custom_fields_json" in row.keys() and row["custom_fields_json"]:
        try:
            custom_fields = json.loads(row["custom_fields_json"])
        except Exception:
            custom_fields = {}
    return {
        "id": row["id"],
        "parcel_id": row["parcel_id"],
        "parcel_name": row["parcel_name"],
        "producer_id": row["producer_id"] if "producer_id" in row.keys() else None,
        "producer_name": row["producer_name"] if "producer_name" in row.keys() else None,
        "activity_type": row["activity_type"],
        "activity_label": row["activity_label"],
        "performed_date": row["performed_date"],
        "fertilization_date": (
            row["fertilization_date"] if "fertilization_date" in row.keys() else None
        ),
        "expected_harvest_date": (
            row["expected_harvest_date"] if "expected_harvest_date" in row.keys() else None
        ),
        "input_used": row["input_used"] if "input_used" in row.keys() else None,
        "dose": row["dose"] if "dose" in row.keys() else None,
        "cost_mxn": row["cost_mxn"] if "cost_mxn" in row.keys() else None,
        "labor_hours": row["labor_hours"] if "labor_hours" in row.keys() else None,
        "workers_count": row["workers_count"] if "workers_count" in row.keys() else None,
        "weather_condition": (
            row["weather_condition"] if "weather_condition" in row.keys() else None
        ),
        "custom_fields": custom_fields,
        "notes": row["notes"],
        "photos": photos,
        "created_at_iso": row["created_at_iso"],
        "updated_at_ms": row["updated_at_ms"],
    }


def list_field_logbook_entries(
    parcel_id: Optional[str] = None,
    producer_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List logbook entries with optional filtering by parcel, producer, and date range."""
    init_local_db(db_path)
    with _connect(db_path) as conn:
        query = "SELECT * FROM field_logbook_entries WHERE 1=1"
        params: List[Any] = []
        if parcel_id:
            query += " AND parcel_id = ?"
            params.append(parcel_id)
        if producer_id:
            query += " AND (producer_id = ? OR producer_name LIKE ?)"
            params.extend([producer_id, f"%{producer_id}%"])
        if start_date:
            query += " AND performed_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND performed_date <= ?"
            params.append(end_date)
        query += " ORDER BY performed_date DESC, updated_at_ms DESC"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_field_log_dict(r) for r in rows]


def create_field_logbook_entry(
    entry_dict: Dict[str, Any],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or upsert a field logbook entry."""
    init_local_db(db_path)
    entry_id = entry_dict.get("id") or f"LOG-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    parcel_id = entry_dict.get("parcel_id") or entry_dict.get("parcelId")
    if not parcel_id:
        raise ValueError("parcel_id es obligatorio para registrar en bitácora.")

    parcel_name = entry_dict.get("parcel_name") or entry_dict.get("parcelName") or parcel_id
    producer_id = entry_dict.get("producer_id") or entry_dict.get("producerId")
    producer_name = entry_dict.get("producer_name") or entry_dict.get("producerName")

    with _connect(db_path) as conn:
        p_row = conn.execute(
            "SELECT parcel_name, producer_id, producer_name FROM parcels WHERE parcel_id = ?",
            (parcel_id,),
        ).fetchone()
        if p_row:
            if not producer_id:
                producer_id = p_row["producer_id"]
            if not producer_name:
                producer_name = p_row["producer_name"]
            if parcel_name == parcel_id and p_row["parcel_name"]:
                parcel_name = p_row["parcel_name"]

    activity_type = (
        entry_dict.get("activity_type") or entry_dict.get("activityType") or "OTRO"
    )
    activity_label = (
        entry_dict.get("activity_label") or entry_dict.get("activityLabel") or activity_type
    )
    performed_date = (
        entry_dict.get("performed_date")
        or entry_dict.get("performedDate")
        or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    fertilization_date = (
        entry_dict.get("fertilization_date") or entry_dict.get("fertilizationDate")
    )
    expected_harvest_date = (
        entry_dict.get("expected_harvest_date") or entry_dict.get("expectedHarvestDate")
    )
    input_used = entry_dict.get("input_used") or entry_dict.get("inputUsed")
    dose = entry_dict.get("dose")
    cost_mxn = entry_dict.get("cost_mxn")
    if cost_mxn is None:
        cost_mxn = entry_dict.get("costMxn")
    if cost_mxn is not None:
        try:
            cost_mxn = float(cost_mxn)
        except (ValueError, TypeError):
            cost_mxn = None
    labor_hours = entry_dict.get("labor_hours")
    if labor_hours is None:
        labor_hours = entry_dict.get("laborHours")
    if labor_hours is not None:
        try:
            labor_hours = float(labor_hours)
        except (ValueError, TypeError):
            labor_hours = None
    workers_count = entry_dict.get("workers_count")
    if workers_count is None:
        workers_count = entry_dict.get("workersCount")
    if workers_count is not None:
        try:
            workers_count = int(workers_count)
        except (ValueError, TypeError):
            workers_count = None
    weather_condition = entry_dict.get("weather_condition") or entry_dict.get("weatherCondition")
    custom_fields = entry_dict.get("custom_fields")
    if custom_fields is None:
        custom_fields = entry_dict.get("customFields")
    custom_fields_json = (
        json.dumps(custom_fields)
        if isinstance(custom_fields, (dict, list))
        else (str(custom_fields) if custom_fields else "{}")
    )
    notes = entry_dict.get("notes") or ""
    photos = entry_dict.get("photos") or []
    photos_json = json.dumps(photos) if isinstance(photos, list) else str(photos)
    created_at_iso = (
        entry_dict.get("created_at_iso")
        or entry_dict.get("createdAtIso")
        or datetime.now(timezone.utc).isoformat()
    )
    updated_at_ms = int(entry_dict.get("updated_at_ms") or (time.time() * 1000))

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO field_logbook_entries (
                id, parcel_id, parcel_name, producer_id, producer_name,
                activity_type, activity_label, performed_date, fertilization_date,
                expected_harvest_date, input_used, dose, cost_mxn, labor_hours,
                workers_count, weather_condition, custom_fields_json,
                notes, photos_json, created_at_iso, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parcel_id = excluded.parcel_id,
                parcel_name = excluded.parcel_name,
                producer_id = excluded.producer_id,
                producer_name = excluded.producer_name,
                activity_type = excluded.activity_type,
                activity_label = excluded.activity_label,
                performed_date = excluded.performed_date,
                fertilization_date = excluded.fertilization_date,
                expected_harvest_date = excluded.expected_harvest_date,
                input_used = excluded.input_used,
                dose = excluded.dose,
                cost_mxn = excluded.cost_mxn,
                labor_hours = excluded.labor_hours,
                workers_count = excluded.workers_count,
                weather_condition = excluded.weather_condition,
                custom_fields_json = excluded.custom_fields_json,
                notes = excluded.notes,
                photos_json = excluded.photos_json,
                updated_at_ms = excluded.updated_at_ms
            """,
            (
                entry_id,
                parcel_id,
                parcel_name,
                producer_id,
                producer_name,
                activity_type,
                activity_label,
                performed_date,
                fertilization_date,
                expected_harvest_date,
                input_used,
                dose,
                cost_mxn,
                labor_hours,
                workers_count,
                weather_condition,
                custom_fields_json,
                notes,
                photos_json,
                created_at_iso,
                updated_at_ms,
            ),
        )
        conn.commit()

    return {
        "id": entry_id,
        "parcel_id": parcel_id,
        "parcel_name": parcel_name,
        "producer_id": producer_id,
        "producer_name": producer_name,
        "activity_type": activity_type,
        "activity_label": activity_label,
        "performed_date": performed_date,
        "fertilization_date": fertilization_date,
        "expected_harvest_date": expected_harvest_date,
        "input_used": input_used,
        "dose": dose,
        "cost_mxn": cost_mxn,
        "labor_hours": labor_hours,
        "workers_count": workers_count,
        "weather_condition": weather_condition,
        "custom_fields": (
            json.loads(custom_fields_json) if custom_fields_json else {}
        ),
        "notes": notes,
        "photos": photos if isinstance(photos, list) else [],
        "created_at_iso": created_at_iso,
        "updated_at_ms": updated_at_ms,
    }


def update_field_logbook_entry(
    entry_id: str,
    payload: Dict[str, Any],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Partially or fully update an existing field logbook entry."""
    init_local_db(db_path)
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM field_logbook_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not existing:
            raise KeyError(f"Entrada de bitácora {entry_id} no encontrada.")

        current = dict(existing)
        activity_type = (
            payload.get("activity_type")
            or payload.get("activityType")
            or current["activity_type"]
        )
        activity_label = (
            payload.get("activity_label")
            or payload.get("activityLabel")
            or current["activity_label"]
        )
        performed_date = (
            payload.get("performed_date")
            or payload.get("performedDate")
            or current["performed_date"]
        )
        fertilization_date = current.get("fertilization_date")
        if "fertilization_date" in payload:
            fertilization_date = payload.get("fertilization_date")
        elif "fertilizationDate" in payload:
            fertilization_date = payload.get("fertilizationDate")

        input_used = current.get("input_used")
        if "input_used" in payload:
            input_used = payload.get("input_used")
        elif "inputUsed" in payload:
            input_used = payload.get("inputUsed")

        dose = payload.get("dose") if "dose" in payload else current.get("dose")
        cost_mxn = current.get("cost_mxn")
        if "cost_mxn" in payload:
            cost_mxn = payload.get("cost_mxn")
        elif "costMxn" in payload:
            cost_mxn = payload.get("costMxn")
        if cost_mxn is not None:
            try:
                cost_mxn = float(cost_mxn)
            except (ValueError, TypeError):
                cost_mxn = None

        labor_hours = current.get("labor_hours")
        if "labor_hours" in payload:
            labor_hours = payload.get("labor_hours")
        elif "laborHours" in payload:
            labor_hours = payload.get("laborHours")
        if labor_hours is not None:
            try:
                labor_hours = float(labor_hours)
            except (ValueError, TypeError):
                labor_hours = None

        workers_count = current.get("workers_count")
        if "workers_count" in payload:
            workers_count = payload.get("workers_count")
        elif "workersCount" in payload:
            workers_count = payload.get("workersCount")
        if workers_count is not None:
            try:
                workers_count = int(workers_count)
            except (ValueError, TypeError):
                workers_count = None

        weather_condition = current.get("weather_condition")
        if "weather_condition" in payload:
            weather_condition = payload.get("weather_condition")
        elif "weatherCondition" in payload:
            weather_condition = payload.get("weatherCondition")
        notes = payload.get("notes") if "notes" in payload else current["notes"]
        photos = payload.get("photos") if "photos" in payload else None
        photos_json = (
            json.dumps(photos) if photos is not None else current.get("photos_json", "[]")
        )
        custom_fields = (
            payload.get("custom_fields")
            if "custom_fields" in payload
            else payload.get("customFields")
        )
        custom_fields_json = (
            json.dumps(custom_fields)
            if custom_fields is not None
            else current.get("custom_fields_json", "{}")
        )
        updated_at_ms = int(time.time() * 1000)

        conn.execute(
            """
            UPDATE field_logbook_entries
            SET activity_type = ?, activity_label = ?, performed_date = ?,
                fertilization_date = ?, input_used = ?, dose = ?, cost_mxn = ?,
                labor_hours = ?, workers_count = ?, weather_condition = ?,
                notes = ?, photos_json = ?, custom_fields_json = ?, updated_at_ms = ?
            WHERE id = ?
            """,
            (
                activity_type,
                activity_label,
                performed_date,
                fertilization_date,
                input_used,
                dose,
                cost_mxn,
                labor_hours,
                workers_count,
                weather_condition,
                notes,
                photos_json,
                custom_fields_json,
                updated_at_ms,
                entry_id,
            ),
        )
        conn.commit()

        updated_row = conn.execute(
            "SELECT * FROM field_logbook_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_field_log_dict(updated_row)


def delete_field_logbook_entry(entry_id: str, db_path: Optional[str] = None) -> bool:
    """Delete a logbook entry by id."""
    init_local_db(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM field_logbook_entries WHERE id = ?", (entry_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_logbook_config(
    include_archived: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the global logbook configuration, activity catalog, inputs, and custom fields."""
    init_local_db(db_path)
    with _connect(db_path) as conn:
        cfg_row = conn.execute("SELECT * FROM logbook_config LIMIT 1").fetchone()
        if not cfg_row:
            cfg = {**DEFAULT_LOGBOOK_CONFIG}
        else:
            cfg = {
                "version": int(cfg_row["version"]),
                "enable_photos": bool(cfg_row["enable_photos"]),
                "enable_inputs_used": bool(cfg_row["enable_inputs_used"]),
                "enable_doses": bool(cfg_row["enable_doses"]),
                "enable_labor_and_costs": bool(cfg_row["enable_labor_and_costs"]),
                "enable_weather_conditions": bool(cfg_row["enable_weather_conditions"]),
                "enable_satellite_comparison": bool(cfg_row["enable_satellite_comparison"]),
                "enable_eudr_flags": bool(cfg_row["enable_eudr_flags"]),
                "enable_custom_fields": bool(cfg_row["enable_custom_fields"]),
                "allow_producer_custom_tags": bool(cfg_row["allow_producer_custom_tags"]),
                "min_required_photos": int(cfg_row["min_required_photos"]),
                "updated_at_iso": cfg_row["updated_at_iso"],
                "updated_by": cfg_row["updated_by"],
            }

        if include_archived:
            act_query = (
                "SELECT * FROM logbook_activity_catalog "
                "ORDER BY is_archived ASC, sort_order ASC"
            )
        else:
            act_query = (
                "SELECT * FROM logbook_activity_catalog "
                "WHERE is_active = 1 AND is_archived = 0 "
                "ORDER BY sort_order ASC"
            )
        act_rows = conn.execute(act_query).fetchall()
        activities = []
        for r in act_rows:
            def_fields = []
            if r["default_fields_json"]:
                try:
                    def_fields = json.loads(r["default_fields_json"])
                except Exception:
                    def_fields = []
            activities.append(
                {
                    "activity_key": r["activity_key"],
                    "name": r["name"],
                    "category": r["category"],
                    "icon": r["icon"],
                    "description": r["description"],
                    "default_fields": def_fields,
                    "requires_inputs": bool(r["requires_inputs"]),
                    "requires_dose": bool(r["requires_dose"]),
                    "is_active": bool(r["is_active"]),
                    "is_archived": bool(r["is_archived"]),
                    "sort_order": int(r["sort_order"]),
                    "created_at_iso": r["created_at_iso"],
                    "updated_at_iso": r["updated_at_iso"],
                }
            )

        if include_archived:
            inp_query = (
                "SELECT * FROM logbook_input_catalog "
                "ORDER BY is_archived ASC, sort_order ASC"
            )
        else:
            inp_query = (
                "SELECT * FROM logbook_input_catalog "
                "WHERE is_active = 1 AND is_archived = 0 "
                "ORDER BY sort_order ASC"
            )
        inp_rows = conn.execute(inp_query).fetchall()
        inputs = [
            {
                "input_key": r["input_key"],
                "name": r["name"],
                "type": r["type"],
                "default_unit": r["default_unit"],
                "is_organic_certified": bool(r["is_organic_certified"]),
                "is_active": bool(r["is_active"]),
                "is_archived": bool(r["is_archived"]),
                "sort_order": int(r["sort_order"]),
            }
            for r in inp_rows
        ]

        if include_archived:
            fld_query = (
                "SELECT * FROM logbook_custom_fields "
                "ORDER BY is_archived ASC, sort_order ASC"
            )
        else:
            fld_query = (
                "SELECT * FROM logbook_custom_fields "
                "WHERE is_active = 1 AND is_archived = 0 "
                "ORDER BY sort_order ASC"
            )
        fld_rows = conn.execute(fld_query).fetchall()
        custom_fields = []
        for r in fld_rows:
            opts = []
            if r["options_json"]:
                try:
                    opts = json.loads(r["options_json"])
                except Exception:
                    opts = []
            custom_fields.append(
                {
                    "field_key": r["field_key"],
                    "activity_key": r["activity_key"],
                    "label": r["label"],
                    "field_type": r["field_type"],
                    "options": opts,
                    "required": bool(r["required"]),
                    "is_active": bool(r["is_active"]),
                    "is_archived": bool(r["is_archived"]),
                    "sort_order": int(r["sort_order"]),
                }
            )

        return {
            "config": cfg,
            "activities": activities,
            "inputs": inputs,
            "custom_fields": custom_fields,
        }


def update_logbook_toggles(
    toggles: Dict[str, Any],
    updated_by: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Update toggle flags in logbook_config and increment version."""
    init_local_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        current = conn.execute("SELECT * FROM logbook_config LIMIT 1").fetchone()
        version = (int(current["version"]) + 1) if current else 2

        fields = [
            "enable_photos",
            "enable_inputs_used",
            "enable_doses",
            "enable_labor_and_costs",
            "enable_weather_conditions",
            "enable_satellite_comparison",
            "enable_eudr_flags",
            "enable_custom_fields",
            "allow_producer_custom_tags",
            "min_required_photos",
        ]
        updates = []
        params = []
        for f in fields:
            if f in toggles:
                updates.append(f"{f} = ?")
                params.append(int(toggles[f]))

        if updates:
            updates.extend(["version = ?", "updated_at_iso = ?", "updated_by = ?"])
            params.extend([version, now_iso, updated_by])
            conn.execute(
                f"UPDATE logbook_config SET {', '.join(updates)} WHERE id = 'global_config'",
                params,
            )
            conn.commit()

    return get_logbook_config(include_archived=True, db_path=db_path)


def upsert_logbook_activity(
    activity_dict: Dict[str, Any],
    updated_by: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or update an activity in logbook_activity_catalog."""
    init_local_db(db_path)
    raw_key = activity_dict.get("activity_key") or activity_dict.get("name", "")
    activity_key = re.sub(r"[^a-zA-Z0-9_]", "_", raw_key).strip("_").upper()
    if len(activity_key) < 3:
        raise ValueError("activity_key debe tener al menos 3 caracteres alfanuméricos.")

    name = (activity_dict.get("name") or activity_key).strip()
    category = (activity_dict.get("category") or "MANEJO_CULTIVO").strip().upper()
    icon = (activity_dict.get("icon") or "📋").strip()
    description = (activity_dict.get("description") or "").strip()
    default_fields = activity_dict.get("default_fields") or []
    default_fields_json = (
        json.dumps(default_fields) if isinstance(default_fields, list) else str(default_fields)
    )
    requires_inputs = 1 if activity_dict.get("requires_inputs") else 0
    requires_dose = 1 if activity_dict.get("requires_dose") else 0
    is_active = 1 if activity_dict.get("is_active", True) else 0
    sort_order = int(activity_dict.get("sort_order") or 0)
    now_iso = datetime.now(timezone.utc).isoformat()

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO logbook_activity_catalog (
                activity_key, name, category, icon, description,
                default_fields_json, requires_inputs, requires_dose,
                is_active, is_archived, sort_order, created_at_iso, updated_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(activity_key) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                icon = excluded.icon,
                description = excluded.description,
                default_fields_json = excluded.default_fields_json,
                requires_inputs = excluded.requires_inputs,
                requires_dose = excluded.requires_dose,
                is_active = excluded.is_active,
                sort_order = excluded.sort_order,
                updated_at_iso = excluded.updated_at_iso
            """,
            (
                activity_key,
                name,
                category,
                icon,
                description,
                default_fields_json,
                requires_inputs,
                requires_dose,
                is_active,
                sort_order,
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            "UPDATE logbook_config SET version = version + 1, updated_at_iso = ?, updated_by = ?",
            (now_iso, updated_by),
        )
        conn.commit()

    return get_logbook_config(include_archived=True, db_path=db_path)


def delete_or_archive_logbook_activity(
    activity_key: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Intelligently archive if entries reference activity_key; otherwise hard-delete."""
    init_local_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        ref_count = conn.execute(
            "SELECT COUNT(*) FROM field_logbook_entries WHERE activity_type = ?",
            (activity_key,),
        ).fetchone()[0]

        if ref_count > 0:
            conn.execute(
                """
                UPDATE logbook_activity_catalog
                SET is_active = 0, is_archived = 1, updated_at_iso = ?
                WHERE activity_key = ?
                """,
                (now_iso, activity_key),
            )
            action = "ARCHIVED"
        else:
            conn.execute(
                "DELETE FROM logbook_activity_catalog WHERE activity_key = ?",
                (activity_key,),
            )
            action = "DELETED"

        conn.execute(
            """
            UPDATE logbook_config
            SET version = version + 1, updated_at_iso = ?, updated_by = 'admin_action'
            """,
            (now_iso,),
        )
        conn.commit()

    return {
        "action": action,
        "activity_key": activity_key,
        "referenced_logs_count": ref_count,
        "full_config": get_logbook_config(include_archived=True, db_path=db_path),
    }


def upsert_logbook_input(
    input_dict: Dict[str, Any],
    updated_by: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or update an input in logbook_input_catalog."""
    init_local_db(db_path)
    raw_key = input_dict.get("input_key") or input_dict.get("name", "")
    input_key = re.sub(r"[^a-zA-Z0-9_]", "_", raw_key).strip("_").upper()
    if len(input_key) < 3:
        raise ValueError("input_key debe tener al menos 3 caracteres alfanuméricos.")

    name = (input_dict.get("name") or input_key).strip()
    input_type = (input_dict.get("type") or "INSUMO_GENERAL").strip().upper()
    default_unit = (input_dict.get("default_unit") or "kg").strip()
    is_organic = 1 if input_dict.get("is_organic_certified", True) else 0
    is_active = 1 if input_dict.get("is_active", True) else 0
    sort_order = int(input_dict.get("sort_order") or 0)
    now_iso = datetime.now(timezone.utc).isoformat()

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO logbook_input_catalog (
                input_key, name, type, default_unit, is_organic_certified,
                is_active, is_archived, sort_order, created_at_iso, updated_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(input_key) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                default_unit = excluded.default_unit,
                is_organic_certified = excluded.is_organic_certified,
                is_active = excluded.is_active,
                sort_order = excluded.sort_order,
                updated_at_iso = excluded.updated_at_iso
            """,
            (
                input_key,
                name,
                input_type,
                default_unit,
                is_organic,
                is_active,
                sort_order,
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            "UPDATE logbook_config SET version = version + 1, updated_at_iso = ?, updated_by = ?",
            (now_iso, updated_by),
        )
        conn.commit()

    return get_logbook_config(include_archived=True, db_path=db_path)


def delete_or_archive_logbook_input(
    input_key: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Intelligently archive if input is referenced in history; otherwise delete."""
    init_local_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        ref_count = conn.execute(
            "SELECT COUNT(*) FROM field_logbook_entries WHERE input_used LIKE ?",
            (f"%{input_key}%",),
        ).fetchone()[0]

        if ref_count > 0:
            conn.execute(
                """
                UPDATE logbook_input_catalog
                SET is_active = 0, is_archived = 1, updated_at_iso = ?
                WHERE input_key = ?
                """,
                (now_iso, input_key),
            )
            action = "ARCHIVED"
        else:
            conn.execute(
                "DELETE FROM logbook_input_catalog WHERE input_key = ?",
                (input_key,),
            )
            action = "DELETED"

        conn.execute(
            """
            UPDATE logbook_config
            SET version = version + 1, updated_at_iso = ?, updated_by = 'admin_action'
            """,
            (now_iso,),
        )
        conn.commit()

    return {
        "action": action,
        "input_key": input_key,
        "referenced_logs_count": ref_count,
        "full_config": get_logbook_config(include_archived=True, db_path=db_path),
    }


def upsert_logbook_custom_field(
    field_dict: Dict[str, Any],
    updated_by: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or update a custom field definition."""
    init_local_db(db_path)
    raw_key = field_dict.get("field_key") or field_dict.get("label", "")
    field_key = re.sub(r"[^a-zA-Z0-9_]", "_", raw_key).strip("_").lower()
    if len(field_key) < 2:
        raise ValueError("field_key debe tener al menos 2 caracteres alfanuméricos.")

    activity_key = field_dict.get("activity_key")
    label = (field_dict.get("label") or field_key).strip()
    field_type = (field_dict.get("field_type") or "TEXT").strip().upper()
    options = field_dict.get("options") or []
    options_json = json.dumps(options) if isinstance(options, list) else str(options)
    required = 1 if field_dict.get("required") else 0
    is_active = 1 if field_dict.get("is_active", True) else 0
    sort_order = int(field_dict.get("sort_order") or 0)
    now_iso = datetime.now(timezone.utc).isoformat()

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO logbook_custom_fields (
                field_key, activity_key, label, field_type, options_json,
                required, is_active, is_archived, sort_order, created_at_iso, updated_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(field_key) DO UPDATE SET
                activity_key = excluded.activity_key,
                label = excluded.label,
                field_type = excluded.field_type,
                options_json = excluded.options_json,
                required = excluded.required,
                is_active = excluded.is_active,
                sort_order = excluded.sort_order,
                updated_at_iso = excluded.updated_at_iso
            """,
            (
                field_key,
                activity_key,
                label,
                field_type,
                options_json,
                required,
                is_active,
                sort_order,
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            "UPDATE logbook_config SET version = version + 1, updated_at_iso = ?, updated_by = ?",
            (now_iso, updated_by),
        )
        conn.commit()

    return get_logbook_config(include_archived=True, db_path=db_path)


def delete_or_archive_logbook_custom_field(
    field_key: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Intelligently archive if custom field is referenced in history; otherwise delete."""
    init_local_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        ref_count = conn.execute(
            "SELECT COUNT(*) FROM field_logbook_entries WHERE custom_fields_json LIKE ?",
            (f"%{field_key}%",),
        ).fetchone()[0]

        if ref_count > 0:
            conn.execute(
                """
                UPDATE logbook_custom_fields
                SET is_active = 0, is_archived = 1, updated_at_iso = ?
                WHERE field_key = ?
                """,
                (now_iso, field_key),
            )
            action = "ARCHIVED"
        else:
            conn.execute(
                "DELETE FROM logbook_custom_fields WHERE field_key = ?",
                (field_key,),
            )
            action = "DELETED"

        conn.execute(
            """
            UPDATE logbook_config
            SET version = version + 1, updated_at_iso = ?, updated_by = 'admin_action'
            """,
            (now_iso,),
        )
        conn.commit()

    return {
        "action": action,
        "field_key": field_key,
        "referenced_logs_count": ref_count,
        "full_config": get_logbook_config(include_archived=True, db_path=db_path),
    }


def sync_field_logbook_entries(
    pushed_entries: List[Dict[str, Any]],
    last_pulled_at_ms: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync offline field logbook entries and return updated entries."""
    accepted_ids = []
    for entry in pushed_entries:
        saved = create_field_logbook_entry(entry, db_path=db_path)
        accepted_ids.append(saved["id"])

    all_entries = list_field_logbook_entries(db_path=db_path)
    if last_pulled_at_ms is not None:
        remote_entries = [
            e for e in all_entries if e["updated_at_ms"] > last_pulled_at_ms
        ]
    else:
        remote_entries = all_entries

    return {
        "status": "SYNCED",
        "accepted_ids": accepted_ids,
        "synced_count": len(accepted_ids),
        "remote_entries": remote_entries,
        "server_time_ms": int(time.time() * 1000),
    }
