"""FastAPI endpoints for admin profile role assignment and editable role definitions."""

from typing import Any, Dict, Optional
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from app.services.admin_roles_engine import (
    assign_role_to_profile,
    get_effective_profile_role,
    list_admin_profiles_and_roles,
    list_role_definitions,
    upsert_role_definition,
    verify_admin_requester,
    verify_logbook_config_requester,
)
from app.services.local_persistence_engine import (
    delete_or_archive_logbook_activity,
    delete_or_archive_logbook_custom_field,
    delete_or_archive_logbook_input,
    get_logbook_config,
    update_logbook_toggles,
    upsert_logbook_activity,
    upsert_logbook_custom_field,
    upsert_logbook_input,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-roles-control"])


class AssignProfileRoleRequest(BaseModel):
    role_key: str = Field(..., min_length=3, max_length=64)
    requester_role: str = Field(default="")
    changed_by: str = Field(default="Administrador ASTRA")


class UpsertRoleRequest(BaseModel):
    role_key: str = Field(..., min_length=3, max_length=64)
    display_name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(default="", max_length=400)
    permissions: Dict[str, bool] = Field(default_factory=dict)
    requester_role: str = Field(default="")
    changed_by: str = Field(default="Administrador ASTRA")


@router.get("/roles/catalog")
def get_role_catalog_endpoint() -> Dict[str, Any]:
    """Return non-sensitive role display names and permission templates."""
    return {"roles": list_role_definitions()}


@router.get("/profile-role/{user_id}")
def get_profile_effective_role_endpoint(
    user_id: str, base_role: str = "productor"
) -> Dict[str, Any]:
    """Resolve effective role and permission set for a user profile."""
    return get_effective_profile_role(user_id=user_id, base_role=base_role)


@router.get("/roles-panel")
def get_admin_roles_panel_endpoint(
    requester_role: str = "",
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Return user profiles and editable role catalog strictly for admin_astra."""
    try:
        verify_admin_requester(authorization=authorization, requester_role=requester_role)
        return list_admin_profiles_and_roles()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.patch("/profiles/{user_id}/role")
def assign_profile_role_endpoint(
    user_id: str,
    payload: AssignProfileRoleRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        return assign_role_to_profile(
            user_id=user_id,
            new_role_key=payload.role_key,
            changed_by=payload.changed_by,
            requester_role=payload.requester_role,
            authorization=authorization,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/roles")
@router.patch("/roles/{role_key}")
def upsert_role_definition_endpoint(
    payload: UpsertRoleRequest,
    role_key: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        target_key = role_key or payload.role_key
        return upsert_role_definition(
            role_key=target_key,
            display_name=payload.display_name,
            description=payload.description,
            permissions=payload.permissions,
            changed_by=payload.changed_by,
            requester_role=payload.requester_role,
            authorization=authorization,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class LogbookTogglesRequest(BaseModel):
    toggles: Dict[str, Any] = Field(default_factory=dict)
    requester_role: str = Field(default="")
    changed_by: str = Field(default="Administrador ASTRA")


class LogbookActivityRequest(BaseModel):
    activity: Dict[str, Any] = Field(default_factory=dict)
    requester_role: str = Field(default="")
    changed_by: str = Field(default="Administrador ASTRA")


class LogbookInputRequest(BaseModel):
    input_item: Dict[str, Any] = Field(default_factory=dict)
    requester_role: str = Field(default="")
    changed_by: str = Field(default="Administrador ASTRA")


class LogbookCustomFieldRequest(BaseModel):
    custom_field: Dict[str, Any] = Field(default_factory=dict)
    requester_role: str = Field(default="")
    changed_by: str = Field(default="Administrador ASTRA")


@router.get("/logbook/config")
def get_admin_logbook_config_endpoint(
    requester_role: str = "",
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Return full logbook configuration and catalog, including archived entries, for admins."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=requester_role
        )
        return get_logbook_config(include_archived=True)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.put("/logbook/toggles")
def update_admin_logbook_toggles_endpoint(
    payload: LogbookTogglesRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Update feature toggles in the logbook configuration."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=payload.requester_role
        )
        return update_logbook_toggles(
            toggles=payload.toggles, updated_by=payload.changed_by
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/logbook/activities")
def upsert_admin_logbook_activity_endpoint(
    payload: LogbookActivityRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Create or update an agronomic activity in the catalog."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=payload.requester_role
        )
        return upsert_logbook_activity(
            activity_dict=payload.activity, updated_by=payload.changed_by
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/logbook/activities/{activity_key}")
def delete_admin_logbook_activity_endpoint(
    activity_key: str,
    requester_role: str = "",
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Intelligently archive or delete an activity from the catalog."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=requester_role
        )
        return delete_or_archive_logbook_activity(activity_key=activity_key)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/logbook/inputs")
def upsert_admin_logbook_input_endpoint(
    payload: LogbookInputRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Create or update a suggested agricultural input in the catalog."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=payload.requester_role
        )
        return upsert_logbook_input(
            input_dict=payload.input_item, updated_by=payload.changed_by
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/logbook/inputs/{input_key}")
def delete_admin_logbook_input_endpoint(
    input_key: str,
    requester_role: str = "",
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Intelligently archive or delete an input from the catalog."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=requester_role
        )
        return delete_or_archive_logbook_input(input_key=input_key)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/logbook/custom-fields")
def upsert_admin_logbook_custom_field_endpoint(
    payload: LogbookCustomFieldRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Create or update a custom field definition for logbook entries."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=payload.requester_role
        )
        return upsert_logbook_custom_field(
            field_dict=payload.custom_field, updated_by=payload.changed_by
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/logbook/custom-fields/{field_key}")
def delete_admin_logbook_custom_field_endpoint(
    field_key: str,
    requester_role: str = "",
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Intelligently archive or delete a custom field definition."""
    try:
        verify_logbook_config_requester(
            authorization=authorization, requester_role=requester_role
        )
        return delete_or_archive_logbook_custom_field(field_key=field_key)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
