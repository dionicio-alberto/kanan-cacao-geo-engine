"""FastAPI endpoint for EPCIS 2.0 reception, CUSTF check, and Blind Yield Caps."""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.custody_epcis_engine import register_epcis_reception

router = APIRouter(prefix="/api/v1/custody", tags=["custody-and-epcis"])


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


@router.post("/epcis-reception")
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
