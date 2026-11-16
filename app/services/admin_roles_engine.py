"""Admin control engine for user profiles, assignable roles, and editable role permissions."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
import httpx

DEFAULT_DB_PATH = os.environ.get(
    "KANAN_LOCAL_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "kanan_local.db"),
)
SUPABASE_CONFIG_BUCKET = "parcel-satellite-maps"
ROLES_CONFIG_PATH = "_config/role_definitions.json"
OVERRIDES_CONFIG_PATH = "_config/custom_profile_roles.json"

STANDARD_ENUM_ROLES = {
    "admin_astra",
    "auditor_sustentabilidad",
    "acopiador",
    "tecnico_campo",
    "productor",
}

DEFAULT_ROLE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "role_key": "admin_astra",
        "display_name": "Administrador ASTRA",
        "description": (
            "Control total del portal, asignación de roles, edición de permisos y "
            "modificación auditada de fechas de registro."
        ),
        "is_system": True,
        "permissions": {
            "can_view_parcels": True,
            "can_create_parcels": True,
            "can_edit_logbook": True,
            "can_register_harvest": True,
            "can_view_carbon": True,
            "can_edit_regdate": True,
            "can_manage_roles": True,
        },
    },
    {
        "role_key": "auditor_sustentabilidad",
        "display_name": "Auditor de Sustentabilidad",
        "description": (
            "Revisión de parcelas, verificación de documentos legales y mapas de cobertura."
        ),
        "is_system": True,
        "permissions": {
            "can_view_parcels": True,
            "can_create_parcels": False,
            "can_edit_logbook": False,
            "can_register_harvest": False,
            "can_view_carbon": True,
            "can_edit_regdate": False,
            "can_manage_roles": False,
        },
    },
    {
        "role_key": "acopiador",
        "display_name": "Centro de Acopio / Báscula",
        "description": (
            "Registro de entregas en mesa de acopio, recepción de cosecha y bitácora."
        ),
        "is_system": True,
        "permissions": {
            "can_view_parcels": True,
            "can_create_parcels": True,
            "can_edit_logbook": True,
            "can_register_harvest": True,
            "can_view_carbon": False,
            "can_edit_regdate": False,
            "can_manage_roles": False,
        },
    },
    {
        "role_key": "tecnico_campo",
        "display_name": "Técnico de Campo",
        "description": (
            "Levantamiento GPS de parcelas en campo, adjunto de documentos y bitácora."
        ),
        "is_system": True,
        "permissions": {
            "can_view_parcels": True,
            "can_create_parcels": True,
            "can_edit_logbook": True,
            "can_register_harvest": False,
            "can_view_carbon": True,
            "can_edit_regdate": False,
            "can_manage_roles": False,
        },
    },
    {
        "role_key": "productor",
        "display_name": "Productor de Cacao",
        "description": (
            "Consulta de parcelas georreferenciadas, bitácora diaria y árbol de sombra."
        ),
        "is_system": True,
        "permissions": {
            "can_view_parcels": True,
            "can_create_parcels": True,
            "can_edit_logbook": True,
            "can_register_harvest": False,
            "can_view_carbon": True,
            "can_edit_regdate": False,
            "can_manage_roles": False,
        },
    },
]

DEFAULT_LOCAL_PROFILES: List[Dict[str, Any]] = [
    {
        "user_id": "77777777-7777-4777-8777-777777777777",
        "email": "admin@cacao-astra.org",
        "full_name": "Dirección de Operaciones ASTRA",
        "role": "admin_astra",
        "cooperative_name": "ASTRA S.A.S.",
    },
    {
        "user_id": "55555555-5555-4555-8555-555555555555",
        "email": "acopio.chontalpa@cacao-astra.org",
        "full_name": "Rodrigo Pech Zapata",
        "role": "acopiador",
        "cooperative_name": "Centro Acopio Chontalpa",
    },
    {
        "user_id": "66666666-6666-4666-8666-666666666666",
        "email": "auditor.eudr@cacao-astra.org",
        "full_name": "Dra. Elena Ruiz Valenzuela",
        "role": "auditor_sustentabilidad",
        "cooperative_name": "Auditoría Chontalpa",
    },
    {
        "user_id": "44444444-4444-4444-8444-444444444444",
        "email": "tecnico.campo@cacao-astra.org",
        "full_name": "Ing. Sofía Méndez Alor",
        "role": "tecnico_campo",
        "cooperative_name": "Chontalpa Cacao",
    },
    {
        "user_id": "11111111-1111-4111-8111-111111111111",
        "email": "mateo.chontalpa@cacao-astra.org",
        "full_name": "Mateo Hernández Chablé",
        "role": "productor",
        "cooperative_name": "Chontalpa Cacao",
    },
]


def _get_supabase_config() -> Tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return url, key


def _is_supabase_active(db_path: Optional[str] = None) -> bool:
    if db_path is not None:
        return False
    if os.environ.get("KANAN_FORCE_SQLITE_ONLY") == "1":
        return False
    url, key = _get_supabase_config()
    return bool(url.startswith("http") and "tu-proyecto" not in url and key)


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path or DEFAULT_DB_PATH
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def _init_roles_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS role_definitions (
            role_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            permissions_json TEXT NOT NULL,
            is_system INTEGER NOT NULL DEFAULT 0,
            updated_at_iso TEXT NOT NULL,
            updated_by TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_profile_roles (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            cooperative_name TEXT NOT NULL DEFAULT 'Chontalpa Cacao',
            updated_at_iso TEXT NOT NULL
        )
        """
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    for role in DEFAULT_ROLE_DEFINITIONS:
        conn.execute(
            """
            INSERT OR IGNORE INTO role_definitions (
                role_key, display_name, description, permissions_json,
                is_system, updated_at_iso, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, 'system')
            """,
            (
                role["role_key"],
                role["display_name"],
                role["description"],
                json.dumps(role["permissions"]),
                1 if role["is_system"] else 0,
                now_iso,
            ),
        )
    for prof in DEFAULT_LOCAL_PROFILES:
        conn.execute(
            """
            INSERT OR IGNORE INTO admin_profile_roles (
                user_id, email, full_name, role, cooperative_name, updated_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prof["user_id"],
                prof["email"],
                prof["full_name"],
                prof["role"],
                prof["cooperative_name"],
                now_iso,
            ),
        )
    conn.commit()


def _normalize_permissions(raw: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    base = {
        "can_view_parcels": True,
        "can_create_parcels": False,
        "can_edit_logbook": False,
        "can_register_harvest": False,
        "can_view_carbon": False,
        "can_edit_regdate": False,
        "can_manage_roles": False,
    }
    if isinstance(raw, dict):
        for k in base:
            if k in raw:
                base[k] = bool(raw[k])
    return base


def _read_storage_json(object_path: str) -> Optional[Any]:
    url, key = _get_supabase_config()
    if not url or not key:
        return None
    try:
        with httpx.Client(timeout=8.0) as client:
            res = client.get(
                f"{url}/storage/v1/object/{SUPABASE_CONFIG_BUCKET}/{object_path}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            if res.status_code == 200:
                return res.json()
    except Exception:
        pass
    return None


def _write_storage_json(object_path: str, data: Any) -> None:
    url, key = _get_supabase_config()
    if not url or not key:
        return
    try:
        with httpx.Client(timeout=8.0) as client:
            client.post(
                f"{url}/storage/v1/object/{SUPABASE_CONFIG_BUCKET}/{object_path}",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "x-upsert": "true",
                },
                content=json.dumps(data, ensure_ascii=False),
            )
    except Exception:
        pass


def _infer_base_enum_role(role_key: str, role_def: Optional[Dict[str, Any]]) -> str:
    if role_key in STANDARD_ENUM_ROLES:
        return role_key
    perms = (role_def or {}).get("permissions") or {}
    if perms.get("can_manage_roles") or perms.get("can_edit_regdate"):
        return "admin_astra"
    if perms.get("can_register_harvest"):
        return "acopiador"
    if perms.get("can_view_carbon") and not perms.get("can_edit_logbook"):
        return "auditor_sustentabilidad"
    if perms.get("can_create_parcels"):
        return "tecnico_campo"
    return "productor"


def verify_admin_requester(
    authorization: Optional[str] = None,
    requester_role: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """
    Ensure the caller is authentically admin_astra.
    When an Authorization Bearer token is passed in Supabase mode, verify JWT against
    Supabase Auth /auth/v1/user and read their authoritative role from public.profiles.
    """
    if (
        _is_supabase_active(db_path)
        and authorization
        and authorization.lower().startswith("bearer ")
    ):
        token = authorization.split(" ", 1)[1].strip()
        url, key = _get_supabase_config()
        try:
            with httpx.Client(timeout=8.0) as client:
                user_res = client.get(
                    f"{url}/auth/v1/user",
                    headers={"apikey": key, "Authorization": f"Bearer {token}"},
                )
                if user_res.status_code != 200:
                    raise PermissionError("Sesión inválida o expirada.")
                uid = user_res.json().get("id")
                prof_res = client.get(
                    f"{url}/rest/v1/profiles",
                    params={"id": f"eq.{uid}", "select": "role,full_name"},
                    headers={"apikey": key, "Authorization": f"Bearer {key}"},
                )
                prof_rows = prof_res.json() if prof_res.status_code == 200 else []
                verified_role = (
                    (prof_rows[0].get("role") or "") if prof_rows else ""
                ).strip().lower()
                if verified_role != "admin_astra":
                    raise PermissionError(
                        "Acceso denegado: únicamente un administrador (admin_astra) puede "
                        "realizar esta operación."
                    )
                return verified_role
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError("No se pudo verificar la identidad de administrador.") from exc

    role_norm = (requester_role or "").strip().lower()
    if role_norm != "admin_astra":
        raise PermissionError(
            "Acceso denegado: únicamente un administrador (admin_astra) puede "
            "realizar esta operación."
        )
    return "admin_astra"


def get_effective_profile_role(
    user_id: str,
    base_role: str = "productor",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return effective role key & definition for a user profile (including custom roles)."""
    roles = list_role_definitions(db_path=db_path)
    role_map = {r["role_key"]: r for r in roles}
    effective_key = base_role

    if _is_supabase_active(db_path):
        overrides = _read_storage_json(OVERRIDES_CONFIG_PATH)
        if isinstance(overrides, dict) and overrides.get(user_id):
            effective_key = str(overrides[user_id])
    else:
        with _connect(db_path) as conn:
            _init_roles_tables(conn)
            row = conn.execute(
                "SELECT role FROM admin_profile_roles WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row and row["role"]:
                effective_key = str(row["role"])

    role_def = role_map.get(effective_key) or role_map.get(base_role) or role_map["productor"]
    return {
        "user_id": user_id,
        "role_key": role_def["role_key"],
        "display_name": role_def["display_name"],
        "permissions": role_def["permissions"],
    }


def list_role_definitions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all editable role definitions (Supabase Storage config backed + SQLite seed)."""
    if _is_supabase_active(db_path):
        stored = _read_storage_json(ROLES_CONFIG_PATH)
        if isinstance(stored, list) and stored:
            return stored
        _write_storage_json(ROLES_CONFIG_PATH, DEFAULT_ROLE_DEFINITIONS)
        return [dict(r) for r in DEFAULT_ROLE_DEFINITIONS]

    with _connect(db_path) as conn:
        _init_roles_tables(conn)
        rows = conn.execute(
            "SELECT * FROM role_definitions ORDER BY is_system DESC, role_key ASC"
        ).fetchall()
        return [
            {
                "role_key": r["role_key"],
                "display_name": r["display_name"],
                "description": r["description"],
                "is_system": bool(r["is_system"]),
                "permissions": json.loads(r["permissions_json"]),
                "updated_at_iso": r["updated_at_iso"],
                "updated_by": r["updated_by"],
            }
            for r in rows
        ]


def list_admin_profiles_and_roles(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Return all user profiles with their active assigned role plus editable role catalog."""
    roles = list_role_definitions(db_path=db_path)
    role_map = {r["role_key"]: r for r in roles}

    if _is_supabase_active(db_path):
        url, key = _get_supabase_config()
        overrides = _read_storage_json(OVERRIDES_CONFIG_PATH)
        overrides_map = overrides if isinstance(overrides, dict) else {}

        with httpx.Client(timeout=10.0) as client:
            prof_res = client.get(
                f"{url}/rest/v1/profiles",
                params={
                    "select": "id,full_name,role,cooperative_name,updated_at",
                    "order": "full_name.asc",
                },
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            prof_res.raise_for_status()
            prof_rows = prof_res.json()

            email_by_id: Dict[str, str] = {}
            try:
                users_res = client.get(
                    f"{url}/auth/v1/admin/users",
                    params={"per_page": "100"},
                    headers={"apikey": key, "Authorization": f"Bearer {key}"},
                )
                if users_res.status_code == 200:
                    for u in users_res.json().get("users") or []:
                        email_by_id[str(u.get("id"))] = str(u.get("email") or "")
            except Exception:
                pass

        profiles_out: List[Dict[str, Any]] = []
        for row in prof_rows:
            uid = str(row["id"])
            assigned_role = str(overrides_map.get(uid) or row.get("role") or "productor")
            r_meta = role_map.get(assigned_role)
            profiles_out.append(
                {
                    "user_id": uid,
                    "email": email_by_id.get(uid) or f"{uid[:8]}@cacao-astra.org",
                    "full_name": row.get("full_name") or "Usuario Kanan Cacao",
                    "role": assigned_role,
                    "role_display_name": (r_meta or {}).get("display_name") or assigned_role,
                    "cooperative_name": row.get("cooperative_name") or "Chontalpa Cacao",
                    "updated_at_iso": row.get("updated_at") or "",
                }
            )
        return {"profiles": profiles_out, "roles": roles}

    with _connect(db_path) as conn:
        _init_roles_tables(conn)
        rows = conn.execute(
            "SELECT * FROM admin_profile_roles ORDER BY full_name ASC"
        ).fetchall()
        profiles_out = [
            {
                "user_id": r["user_id"],
                "email": r["email"],
                "full_name": r["full_name"],
                "role": r["role"],
                "role_display_name": (role_map.get(r["role"]) or {}).get("display_name")
                or r["role"],
                "cooperative_name": r["cooperative_name"],
                "updated_at_iso": r["updated_at_iso"],
            }
            for r in rows
        ]
        return {"profiles": profiles_out, "roles": roles}


def assign_role_to_profile(
    user_id: str,
    new_role_key: str,
    changed_by: str,
    requester_role: str = "admin_astra",
    authorization: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Assign an existing role (standard or custom) to a user profile."""
    verify_admin_requester(
        authorization=authorization, requester_role=requester_role, db_path=db_path
    )
    clean_role = re.sub(r"[^a-z0-9_]", "_", new_role_key.strip().lower()).strip("_")
    roles = list_role_definitions(db_path=db_path)
    role_map = {r["role_key"]: r for r in roles}
    if clean_role not in role_map:
        raise ValueError(f"Rol '{clean_role}' no existe en el catálogo.")

    now_iso = datetime.now(timezone.utc).isoformat()
    if _is_supabase_active(db_path):
        url, key = _get_supabase_config()
        base_enum_role = _infer_base_enum_role(clean_role, role_map[clean_role])
        with httpx.Client(timeout=10.0) as client:
            patch_res = client.patch(
                f"{url}/rest/v1/profiles",
                params={"id": f"eq.{user_id}"},
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                json={"role": base_enum_role, "updated_at": now_iso},
            )
            patch_res.raise_for_status()
            if not patch_res.json():
                raise ValueError(f"Perfil {user_id} no encontrado.")

        overrides = _read_storage_json(OVERRIDES_CONFIG_PATH)
        overrides_map = overrides if isinstance(overrides, dict) else {}
        if clean_role in STANDARD_ENUM_ROLES:
            overrides_map.pop(user_id, None)
        else:
            overrides_map[user_id] = clean_role
        _write_storage_json(OVERRIDES_CONFIG_PATH, overrides_map)
        return list_admin_profiles_and_roles(db_path=db_path)

    with _connect(db_path) as conn:
        _init_roles_tables(conn)
        cur = conn.execute(
            "UPDATE admin_profile_roles SET role = ?, updated_at_iso = ? WHERE user_id = ?",
            (clean_role, now_iso, user_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"Perfil {user_id} no encontrado en base local.")
        conn.commit()
    return list_admin_profiles_and_roles(db_path=db_path)


def upsert_role_definition(
    role_key: str,
    display_name: str,
    description: str,
    permissions: Dict[str, Any],
    changed_by: str,
    requester_role: str = "admin_astra",
    authorization: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a custom role or edit the display name, description, and permissions of any role."""
    verify_admin_requester(
        authorization=authorization, requester_role=requester_role, db_path=db_path
    )
    clean_key = re.sub(r"[^a-z0-9_]", "_", role_key.strip().lower()).strip("_")
    if len(clean_key) < 3:
        raise ValueError("El identificador del rol debe tener al menos 3 caracteres.")
    clean_name = display_name.strip()
    if len(clean_name) < 2:
        raise ValueError("El nombre del rol no puede quedar vacío.")

    norm_perms = _normalize_permissions(permissions)
    if clean_key == "admin_astra":
        norm_perms["can_manage_roles"] = True
        norm_perms["can_edit_regdate"] = True

    now_iso = datetime.now(timezone.utc).isoformat()
    if _is_supabase_active(db_path):
        roles = list_role_definitions(db_path=db_path)
        updated_list: List[Dict[str, Any]] = []
        found = False
        for item in roles:
            if item["role_key"] == clean_key:
                found = True
                updated_list.append(
                    {
                        **item,
                        "display_name": clean_name,
                        "description": description.strip(),
                        "permissions": norm_perms,
                        "updated_at_iso": now_iso,
                        "updated_by": changed_by,
                    }
                )
            else:
                updated_list.append(item)
        if not found:
            updated_list.append(
                {
                    "role_key": clean_key,
                    "display_name": clean_name,
                    "description": description.strip(),
                    "is_system": False,
                    "permissions": norm_perms,
                    "updated_at_iso": now_iso,
                    "updated_by": changed_by,
                }
            )
        _write_storage_json(ROLES_CONFIG_PATH, updated_list)
        return list_admin_profiles_and_roles(db_path=db_path)

    with _connect(db_path) as conn:
        _init_roles_tables(conn)
        existing = conn.execute(
            "SELECT is_system FROM role_definitions WHERE role_key = ?", (clean_key,)
        ).fetchone()
        is_sys = int(existing["is_system"]) if existing else 0
        conn.execute(
            """
            INSERT INTO role_definitions (
                role_key, display_name, description, permissions_json,
                is_system, updated_at_iso, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(role_key) DO UPDATE SET
                display_name = excluded.display_name,
                description = excluded.description,
                permissions_json = excluded.permissions_json,
                updated_at_iso = excluded.updated_at_iso,
                updated_by = excluded.updated_by
            """,
            (
                clean_key,
                clean_name,
                description.strip(),
                json.dumps(norm_perms),
                is_sys,
                now_iso,
                changed_by,
            ),
        )
        conn.commit()
    return list_admin_profiles_and_roles(db_path=db_path)
