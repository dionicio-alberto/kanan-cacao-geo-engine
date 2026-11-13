"""FastAPI entry point for Kanan Cacao Geo-Engine."""

from fastapi import FastAPI
from app.core.config import EUDR_CUTOFF_DATE_ISO

app = FastAPI(
    title="Kanan Cacao Geo-Engine API",
    description=(
        "Motor Analítico Serverless de ASTRA para validación EUDR (JRC 2020), "
        "Salud Vegetal (Sentinel-2 NDVI), Yield Caps Ciegos y EPCIS 2.0."
    ),
    version="0.1.0",
)


@app.get("/health")
def health_check() -> dict:
    """Serverless probe endpoint for Google Cloud Run."""
    return {
        "status": "ok",
        "service": "kanan-cacao-geo-engine",
        "eudr_cutoff_baseline": EUDR_CUTOFF_DATE_ISO,
    }
