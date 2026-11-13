"""Custody, CUSTF legality (HU-05), Blind Yield Caps (HU-06), EPCIS 2.0 (HU-07) & TRACES NT."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional
from app.core.config import (
    EPCIS_CONTEXT_URL,
    EUDR_CUTOFF_DATE_ISO,
    EUDR_LEGAL_DISCLAIMER,
    TABASCO_YIELD_CAP_MAX_KG_HA,
    TABASCO_YIELD_CAP_MIN_KG_HA,
)
from app.services.topology_engine import lint_and_validate_polygon


def compute_sha256_hex(payload_str: str) -> str:
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()


def register_epcis_reception(
    parcel_id: str,
    producer_id: str,
    read_point_id: str,
    area_hectares: float,
    previous_cumulative_dry_kg: float,
    delivered_dry_equivalent_kg: float,
    acahual_removal_post_2020: bool,
    has_verified_custf_permit: bool,
    requester_role: str = "acopiador",
    event_time_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    HU-05, HU-06 & HU-07:
    1. Blocks producer automatically if acahual removal post-2020 lacks verified CUSTF permit.
    2. Evaluates Tabasco biological yield cap (450-650 kg/ha) silently.
    3. Emits signed GS1 EPCIS 2.0 JSON-LD ObjectEvent with SHA-256 hash.
    4. Returns blind receipt to 'acopiador' role hiding yield cap alerts.
    """
    if acahual_removal_post_2020 and not has_verified_custf_permit:
        raise PermissionError(
            "HU-05: Productor bloqueado automáticamente por remoción de acahuales "
            "post-2020 sin permiso CUSTF de SEMARNAT verificado."
        )

    if area_hectares <= 0 or delivered_dry_equivalent_kg <= 0:
        raise ValueError(
            "HU-06/HU-07: El área de la parcela y el peso entregado deben ser > 0."
        )

    new_cumulative_kg = round(
        previous_cumulative_dry_kg + delivered_dry_equivalent_kg, 2
    )
    cumulative_kg_per_ha = round(new_cumulative_kg / area_hectares, 2)

    triggered_yield_cap_alert = cumulative_kg_per_ha > TABASCO_YIELD_CAP_MAX_KG_HA
    within_biological_range = (
        TABASCO_YIELD_CAP_MIN_KG_HA
        <= cumulative_kg_per_ha
        <= TABASCO_YIELD_CAP_MAX_KG_HA
    )

    timestamp = event_time_iso or datetime.now(timezone.utc).isoformat()

    canonical_payload = json.dumps(
        {
            "context": EPCIS_CONTEXT_URL,
            "type": "ObjectEvent",
            "eventTime": timestamp,
            "bizStep": "receiving",
            "disposition": "in_progress",
            "readPointId": read_point_id,
            "parcelId": parcel_id,
            "producerId": producer_id,
            "deliveredDryEquivalentKg": delivered_dry_equivalent_kg,
        },
        sort_keys=True,
    )
    payload_sha256 = compute_sha256_hex(canonical_payload)

    epcis_event = {
        "@context": EPCIS_CONTEXT_URL,
        "type": "ObjectEvent",
        "eventTime": timestamp,
        "eventTimeZoneOffset": "-06:00",
        "bizStep": "receiving",
        "disposition": "in_progress",
        "readPoint": {"id": read_point_id},
        "parcelId": parcel_id,
        "producerId": producer_id,
        "deliveredDryEquivalentKg": delivered_dry_equivalent_kg,
        "payloadSha256": payload_sha256,
    }

    if requester_role in ("admin_astra", "auditor_sustentabilidad"):
        return {
            "status": "REGISTERED",
            "parcel_id": parcel_id,
            "producer_id": producer_id,
            "delivered_dry_equivalent_kg": delivered_dry_equivalent_kg,
            "new_cumulative_dry_kg": new_cumulative_kg,
            "cumulative_kg_per_ha": cumulative_kg_per_ha,
            "triggered_yield_cap_alert": triggered_yield_cap_alert,
            "within_biological_range": within_biological_range,
            "epcis_event": epcis_event,
        }

    return {
        "status": "REGISTERED",
        "parcel_id": parcel_id,
        "producer_id": producer_id,
        "delivered_dry_equivalent_kg": delivered_dry_equivalent_kg,
        "epcis_event": epcis_event,
    }


def build_traces_nt_due_diligence_package(
    parcel_id: str,
    producer_id: str,
    municipality: str,
    coordinates: List[List[float]],
    eudr_status: str,
    acahual_removal_post_2020: bool,
    has_verified_custf_permit: bool,
    delivered_dry_equivalent_kg: float,
) -> Dict[str, Any]:
    """
    Builds a complete European Union TRACES NT Due Diligence Package combining
    repaired RFC 7946 GeoJSON, EUDR 2020 status, CUSTF verification, and EPCIS 2.0 event.
    """
    if acahual_removal_post_2020 and not has_verified_custf_permit:
        raise PermissionError(
            "HU-05 Gate: Exportación TRACES NT bloqueada por falta de permiso CUSTF (SEMARNAT)."
        )

    if eudr_status != "VERDE":
        raise PermissionError(
            "HU-03 Gate: Exportación TRACES NT bloqueada. La parcela no tiene estatus EUDR VERDE."
        )

    topology = lint_and_validate_polygon(coordinates)
    epcis_res = register_epcis_reception(
        parcel_id=parcel_id,
        producer_id=producer_id,
        read_point_id="urn:epc:id:sgln:7500000.00001.0",
        area_hectares=topology["area_hectares"],
        previous_cumulative_dry_kg=0.0,
        delivered_dry_equivalent_kg=delivered_dry_equivalent_kg,
        acahual_removal_post_2020=acahual_removal_post_2020,
        has_verified_custf_permit=has_verified_custf_permit,
        requester_role="admin_astra",
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    canonical = json.dumps(
        {
            "schema_version": "EUDR-TRACES-NT-1.0",
            "parcel_id": parcel_id,
            "producer_id": producer_id,
            "polygon": topology["polygon"],
            "epcis_sha256": epcis_res["epcis_event"]["payloadSha256"],
        },
        sort_keys=True,
    )

    return {
        "schema_version": "EUDR-TRACES-NT-1.0",
        "exporter_entity": "ASTRA S.A.S. - Kanan Cacao (Tabasco, MX)",
        "generated_at_iso": generated_at,
        "parcel_id": parcel_id,
        "producer_id": producer_id,
        "municipality": municipality,
        "commodity_hs_code": "1801.00 (Cacao en grano, entero o partido, crudo o tostado)",
        "eudr_cutoff_baseline": EUDR_CUTOFF_DATE_ISO,
        "eudr_status": eudr_status,
        "legal_disclaimer": EUDR_LEGAL_DISCLAIMER,
        "area_hectares": topology["area_hectares"],
        "geojson_feature": {
            "type": "Feature",
            "properties": {
                "parcel_id": parcel_id,
                "producer_id": producer_id,
                "municipality": municipality,
                "country_iso": "MX",
                "region": "Tabasco",
            },
            "geometry": topology["polygon"],
        },
        "epcis_2_0_event": epcis_res["epcis_event"],
        "package_integrity_sha256": compute_sha256_hex(canonical),
    }
