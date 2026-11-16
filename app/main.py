"""FastAPI entry point for Kanan Cacao Geo-Engine."""

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes_admin_roles import router as admin_roles_router
from app.api.routes_custody import router as custody_router
from app.api.routes_persistence import router as persistence_router
from app.api.routes_spatial import router as spatial_router
from app.core.config import EUDR_CUTOFF_DATE_ISO


def _load_dotenv_if_present() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv_if_present()

DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
]
EXTRA_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]

app = FastAPI(
    title="Kanan Cacao Geo-Engine API",
    description=(
        "Motor Analítico Serverless de ASTRA para validación EUDR (JRC 2020), "
        "Salud Vegetal (Sentinel-2 NDVI), Yield Caps Ciegos, EPCIS 2.0 y TRACES NT."
    ),
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEFAULT_ORIGINS + EXTRA_ORIGINS,
    allow_origin_regex=r"https://.*\.(vercel|railway|up\.railway)\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(spatial_router)
app.include_router(custody_router)
app.include_router(persistence_router)
app.include_router(admin_roles_router)


@app.get("/health")
@app.get("/api/v1/health")
def health_check() -> dict:
    """Serverless probe endpoint for Google Cloud Run and Railway."""
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_configured = bool(
        supabase_url
        and "tu-proyecto" not in supabase_url
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )
    return {
        "status": "ok",
        "service": "kanan-cacao-geo-engine",
        "version": "0.2.0",
        "eudr_cutoff_baseline": EUDR_CUTOFF_DATE_ISO,
        "supabase_configured": supabase_configured,
    }
