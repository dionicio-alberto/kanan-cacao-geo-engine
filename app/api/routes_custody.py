"""FastAPI endpoints for EPCIS 2.0 reception, CUSTF check, Blind Yield Caps & TRACES NT."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.custody_epcis_engine import (
    build_traces_nt_due_diligence_package,
    register_epcis_reception,
)

router = APIRouter(prefix="/api/v1", tags=["custody-and-epcis"])


class EpcisReceptionRequest(BaseModel):
    parcel_id: str = Field(..., min_length=1)
    producer_id: str = Field(..., min_length=1)
    read_point_id: str = Field(..., min_length=1)
    area_hectares: float = Field(..., gt=0.0)
    previous_cumulative_dry_kg: float = Field(default=0.0, ge=0.0)
    delivered_dry_equivalent_kg: float = Field(..., gt=0.0)
    acahual_removal_post_2020: bool = Field(default=False)
    has_verified_custf_permit: bool = Field(default=False)
    requester_role: str = Field(default="acopiador")
    event_time_iso: Optional[str] = None


class TracesNtExportRequest(BaseModel):
    parcel_id: str = Field(..., min_length=1)
    producer_id: str = Field(..., min_length=1)
    municipality: str = Field(..., min_length=1)
    coordinates: List[List[float]]
    eudr_status: str = Field(..., min_length=1)
    acahual_removal_post_2020: bool = Field(default=False)
    has_verified_custf_permit: bool = Field(default=False)
    delivered_dry_equivalent_kg: float = Field(..., gt=0.0)


@router.post("/custody/epcis-reception")
def epcis_reception_endpoint(payload: EpcisReceptionRequest) -> dict:
    try:
        return register_epcis_reception(
            parcel_id=payload.parcel_id,
            producer_id=payload.producer_id,
            read_point_id=payload.read_point_id,
            area_hectares=payload.area_hectares,
            previous_cumulative_dry_kg=payload.previous_cumulative_dry_kg,
            delivered_dry_equivalent_kg=payload.delivered_dry_equivalent_kg,
            acahual_removal_post_2020=payload.acahual_removal_post_2020,
            has_verified_custf_permit=payload.has_verified_custf_permit,
            requester_role=payload.requester_role,
            event_time_iso=payload.event_time_iso,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exports/traces-nt-package")
def traces_nt_export_endpoint(payload: TracesNtExportRequest) -> dict:
    try:
        return build_traces_nt_due_diligence_package(
            parcel_id=payload.parcel_id,
            producer_id=payload.producer_id,
            municipality=payload.municipality,
            coordinates=payload.coordinates,
            eudr_status=payload.eudr_status,
            acahual_removal_post_2020=payload.acahual_removal_post_2020,
            has_verified_custf_permit=payload.has_verified_custf_permit,
            delivered_dry_equivalent_kg=payload.delivered_dry_equivalent_kg,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
