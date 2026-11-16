"""FastAPI endpoints for Supabase PostGIS / SQLite persistence, CUSTF, and offline sync."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app.services.local_persistence_engine import (
    get_active_storage_engine,
    get_cached_parcel_map_image,
    get_or_create_parcel_historical_snapshots,
    list_persisted_parcels,
    record_persisted_reception,
    sync_offline_parcels,
    toggle_parcel_custf,
)

router = APIRouter(prefix="/api/v1", tags=["local-persistence-and-sync"])


class OfflineSyncRequest(BaseModel):
    last_pulled_at_ms: Optional[int] = None
    pushed_parcels: List[Dict[str, Any]] = Field(default_factory=list)


class PersistedReceptionRequest(BaseModel):
    parcel_id: str = Field(..., min_length=1)
    delivered_dry_equivalent_kg: float = Field(..., gt=0.0)
    requester_role: str = Field(default="acopiador")
    read_point_id: str = Field(default="urn:epc:id:sgln:7500000.00001.0")


@router.get("/parcels")
def get_parcels_endpoint() -> Dict[str, Any]:
    parcels = list_persisted_parcels()
    return {
        "storage_engine": get_active_storage_engine(),
        "count": len(parcels),
        "parcels": parcels,
    }


@router.patch("/parcels/{parcel_id}/custf")
def toggle_custf_endpoint(parcel_id: str) -> Dict[str, Any]:
    try:
        updated = toggle_parcel_custf(parcel_id)
        return {
            "status": "UPDATED",
            "storage_engine": get_active_storage_engine(),
            "parcel": updated,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/parcels/sync")
def sync_parcels_endpoint(payload: OfflineSyncRequest) -> Dict[str, Any]:
    try:
        return sync_offline_parcels(
            last_pulled_at_ms=payload.last_pulled_at_ms,
            pushed_parcels=payload.pushed_parcels,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/custody/persisted-reception")
def persisted_reception_endpoint(payload: PersistedReceptionRequest) -> Dict[str, Any]:
    try:
        return record_persisted_reception(
            parcel_id=payload.parcel_id,
            delivered_dry_equivalent_kg=payload.delivered_dry_equivalent_kg,
            requester_role=payload.requester_role,
            read_point_id=payload.read_point_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/parcels/{parcel_id}/historical-snapshots")
def get_historical_snapshots_endpoint(parcel_id: str) -> Dict[str, Any]:
    try:
        return get_or_create_parcel_historical_snapshots(parcel_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/parcels/{parcel_id}/historical-image/{period_key}")
def get_historical_image_endpoint(parcel_id: str, period_key: str) -> Response:
    try:
        img_bytes = get_cached_parcel_map_image(parcel_id, period_key)
        return Response(
            content=img_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
