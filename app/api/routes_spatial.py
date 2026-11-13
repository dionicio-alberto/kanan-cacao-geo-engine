"""FastAPI endpoints for topology validation, EUDR JRC 2020, and Sentinel-2 NDVI."""

from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.satellite_audit_engine import (
    evaluate_eudr_2020_baseline,
    evaluate_sentinel2_ndvi,
)
from app.services.topology_engine import lint_and_validate_polygon

router = APIRouter(prefix="/api/v1", tags=["spatial-and-satellite"])


class PolygonValidationRequest(BaseModel):
    parcel_id: str = Field(..., min_length=1)
    coordinates: List[List[float]]


class EudrAuditRequest(BaseModel):
    parcel_id: str = Field(..., min_length=1)
    coordinates: List[List[float]]
    jrc_2020_tree_cover_pct: float = Field(..., ge=0.0, le=100.0)
    current_tree_cover_pct: float = Field(..., ge=0.0, le=100.0)


class NdviAuditRequest(BaseModel):
    parcel_id: str = Field(..., min_length=1)
    previous_scene_id: str = Field(..., min_length=1)
    previous_mean_ndvi: float = Field(..., ge=-1.0, le=1.0)
    latest_scene_id: str = Field(..., min_length=1)
    latest_mean_ndvi: float = Field(..., ge=-1.0, le=1.0)


@router.post("/topology/validate")
def validate_topology_endpoint(payload: PolygonValidationRequest) -> dict:
    try:
        report = lint_and_validate_polygon(payload.coordinates)
        return {"parcel_id": payload.parcel_id, **report}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audits/eudr-2020")
def audit_eudr_2020_endpoint(payload: EudrAuditRequest) -> dict:
    try:
        return evaluate_eudr_2020_baseline(
            parcel_id=payload.parcel_id,
            raw_coordinates=payload.coordinates,
            jrc_2020_tree_cover_pct=payload.jrc_2020_tree_cover_pct,
            current_tree_cover_pct=payload.current_tree_cover_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audits/ndvi-sentinel2")
def audit_ndvi_sentinel2_endpoint(payload: NdviAuditRequest) -> dict:
    try:
        return evaluate_sentinel2_ndvi(
            parcel_id=payload.parcel_id,
            previous_scene_id=payload.previous_scene_id,
            previous_mean_ndvi=payload.previous_mean_ndvi,
            latest_scene_id=payload.latest_scene_id,
            latest_mean_ndvi=payload.latest_mean_ndvi,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
