"""FastAPI entry point for Kanan Cacao Geo-Engine."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes_custody import router as custody_router
from app.api.routes_spatial import router as spatial_router
from app.core.config import EUDR_CUTOFF_DATE_ISO

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
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(spatial_router)
app.include_router(custody_router)


@app.get("/health")
def health_check() -> dict:
    """Serverless probe endpoint for Google Cloud Run."""
    return {
        "status": "ok",
        "service": "kanan-cacao-geo-engine",
        "version": "0.2.0",
        "eudr_cutoff_baseline": EUDR_CUTOFF_DATE_ISO,
    }
