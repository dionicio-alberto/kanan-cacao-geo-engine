"""FastAPI endpoints for Supabase PostGIS / SQLite persistence, CUSTF, and offline sync."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app.services.admin_roles_engine import verify_admin_requester
from app.services.local_persistence_engine import (
    create_field_logbook_entry,
    delete_parcel_record,
    get_active_storage_engine,
    get_cached_parcel_map_image,
    get_or_create_parcel_historical_snapshots,
    get_parcel_document_file,
    list_field_logbook_entries,
    list_parcel_documents,
    list_persisted_parcels,
    record_persisted_reception,
    sync_field_logbook_entries,
    sync_offline_parcels,
    toggle_parcel_custf,
    update_parcel_metadata,
    update_parcel_registration_date,
    upload_parcel_document,
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


class UpdateRegistrationDateRequest(BaseModel):
    new_date_iso: str = Field(..., min_length=10, max_length=32)
    confirmation_word: str = Field(..., min_length=1)
    changed_by: str = Field(..., min_length=1)
    changed_by_role: str = Field(..., min_length=1)


class UpdateParcelMetadataRequest(BaseModel):
    parcel_name: Optional[str] = None
    producer_name: Optional[str] = None
    municipality: Optional[str] = None
    area_hectares: Optional[float] = None


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


@router.patch("/parcels/{parcel_id}")
def update_parcel_endpoint(
    parcel_id: str, payload: UpdateParcelMetadataRequest
) -> Dict[str, Any]:
    try:
        updated = update_parcel_metadata(
            parcel_id=parcel_id,
            parcel_name=payload.parcel_name,
            producer_name=payload.producer_name,
            municipality=payload.municipality,
            area_hectares=payload.area_hectares,
        )
        return {
            "status": "UPDATED",
            "storage_engine": get_active_storage_engine(),
            "parcel": updated,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/parcels/{parcel_id}")
def delete_parcel_endpoint(parcel_id: str) -> Dict[str, Any]:
    try:
        delete_parcel_record(parcel_id=parcel_id)
        return {
            "status": "DELETED",
            "parcel_id": parcel_id,
            "storage_engine": get_active_storage_engine(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/parcels/{parcel_id}/registration-date")
def update_registration_date_endpoint(
    parcel_id: str,
    payload: UpdateRegistrationDateRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        verified_role = verify_admin_requester(
            authorization=authorization, requester_role=payload.changed_by_role
        )
        updated = update_parcel_registration_date(
            parcel_id=parcel_id,
            new_date_iso=payload.new_date_iso,
            confirmation_word=payload.confirmation_word,
            changed_by=payload.changed_by,
            changed_by_role=verified_role,
        )
        return {"status": "UPDATED", "parcel": updated}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
def get_historical_snapshots_endpoint(
    parcel_id: str,
    reference_date_iso: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return get_or_create_parcel_historical_snapshots(
            parcel_id, reference_date_iso=reference_date_iso
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/parcels/{parcel_id}/historical-image/{period_key}")
def get_historical_image_endpoint(
    parcel_id: str,
    period_key: str,
    wayback_release: Optional[str] = None,
) -> Response:
    try:
        img_bytes = get_cached_parcel_map_image(
            parcel_id, period_key, wayback_release=wayback_release
        )
        return Response(
            content=img_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/parcels/{parcel_id}/documents")
def list_parcel_documents_endpoint(parcel_id: str) -> Dict[str, Any]:
    try:
        docs = list_parcel_documents(parcel_id)
        return {"parcel_id": parcel_id, "count": len(docs), "documents": docs}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/parcels/{parcel_id}/documents")
async def upload_parcel_document_endpoint(
    parcel_id: str,
    document_type: str = Form(...),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    try:
        file_bytes = await file.read()
        created = upload_parcel_document(
            parcel_id=parcel_id,
            document_type=document_type,
            file_name=file.filename or "documento.pdf",
            file_bytes=file_bytes,
            content_type=file.content_type,
        )
        return {"status": "UPLOADED", "document": created}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/parcels/{parcel_id}/documents/{doc_id}/file")
def download_parcel_document_endpoint(parcel_id: str, doc_id: str) -> Response:
    try:
        content, ctype, file_name = get_parcel_document_file(parcel_id, doc_id)
        return Response(
            content=content,
            media_type=ctype,
            headers={
                "Content-Disposition": f'inline; filename="{file_name}"',
                "Cache-Control": "private, max-age=3600",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/parcels/refresh-historical")
def refresh_historical_snapshots_endpoint() -> Dict[str, Any]:
    """Force-refresh all persisted parcel historical snapshots to the active Sentinel-2 spec."""
    parcels = list_persisted_parcels()
    return {
        "status": "OK",
        "count": len(parcels),
        "versions": sorted(
            {
                s.get("cloud_filter_version")
                for p in parcels
                for s in (p.get("historical_snapshots") or [])
                if s.get("cloud_filter_version")
            }
        ),
    }


class FieldLogbookEntryRequest(BaseModel):
    id: Optional[str] = None
    parcel_id: Optional[str] = None
    parcelId: Optional[str] = None
    parcel_name: Optional[str] = None
    parcelName: Optional[str] = None
    producer_id: Optional[str] = None
    producerId: Optional[str] = None
    producer_name: Optional[str] = None
    producerName: Optional[str] = None
    activity_type: Optional[str] = None
    activityType: Optional[str] = None
    activity_label: Optional[str] = None
    activityLabel: Optional[str] = None
    performed_date: Optional[str] = None
    performedDate: Optional[str] = None
    fertilization_date: Optional[str] = None
    fertilizationDate: Optional[str] = None
    expected_harvest_date: Optional[str] = None
    expectedHarvestDate: Optional[str] = None
    input_used: Optional[str] = None
    inputUsed: Optional[str] = None
    notes: str = ""
    photos: List[Any] = Field(default_factory=list)
    created_at_iso: Optional[str] = None
    createdAtIso: Optional[str] = None
    updated_at_ms: Optional[int] = None


class FieldLogbookSyncRequest(BaseModel):
    last_pulled_at_ms: Optional[int] = None
    pushed_entries: List[Dict[str, Any]] = Field(default_factory=list)


@router.get("/logbook")
def get_logbook_endpoint(
    parcel_id: Optional[str] = None,
    producer_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    entries = list_field_logbook_entries(
        parcel_id=parcel_id,
        producer_id=producer_id,
        start_date=start_date,
        end_date=end_date,
    )
    return {
        "status": "OK",
        "count": len(entries),
        "entries": entries,
    }


@router.post("/logbook")
def create_logbook_endpoint(payload: FieldLogbookEntryRequest) -> Dict[str, Any]:
    try:
        raw_dict = (
            payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        )
        entry = create_field_logbook_entry(raw_dict)
        return {
            "status": "CREATED",
            "entry": entry,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/logbook/sync")
def sync_logbook_endpoint(payload: FieldLogbookSyncRequest) -> Dict[str, Any]:
    try:
        return sync_field_logbook_entries(
            pushed_entries=payload.pushed_entries,
            last_pulled_at_ms=payload.last_pulled_at_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
