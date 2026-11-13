"""Custody, CUSTF legality (HU-05), Blind Yield Caps (HU-06), and EPCIS 2.0 (HU-07)."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, Optional
from app.core.config import (
    EPCIS_CONTEXT_URL,
    TABASCO_YIELD_CAP_MAX_KG_HA,
    TABASCO_YIELD_CAP_MIN_KG_HA,
)


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

    # HU-06: Operación "ciega" en la báscula para el rol acopiador
    return {
        "status": "REGISTERED",
        "parcel_id": parcel_id,
        "producer_id": producer_id,
        "delivered_dry_equivalent_kg": delivered_dry_equivalent_kg,
        "epcis_event": epcis_event,
    }
