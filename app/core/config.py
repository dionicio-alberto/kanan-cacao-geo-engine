"""Domain constants and configuration for Kanan Cacao Geo-Engine."""

EUDR_MANDATORY_POLYGON_THRESHOLD_HA: float = 4.0
MAX_GEOJSON_SIZE_BYTES: int = 25 * 1024 * 1024
EUDR_CUTOFF_DATE_ISO: str = "2020-12-31"
EUDR_LEGAL_DISCLAIMER: str = (
    "Esta validación constituye una preevaluación automatizada basada en capas "
    "satelitales JRC 2020 y no representa una certificación legal vinculante."
)
TABASCO_YIELD_CAP_MIN_KG_HA: float = 450.0
TABASCO_YIELD_CAP_MAX_KG_HA: float = 650.0
EPCIS_CONTEXT_URL: str = (
    "https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld"
)
