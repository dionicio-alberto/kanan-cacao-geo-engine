"""Bolt 0 tests for healthcheck and EUDR domain invariants."""

from fastapi.testclient import TestClient
from app.core.config import (
    EUDR_CUTOFF_DATE_ISO,
    EUDR_MANDATORY_POLYGON_THRESHOLD_HA,
    TABASCO_YIELD_CAP_MAX_KG_HA,
    TABASCO_YIELD_CAP_MIN_KG_HA,
)
from app.main import app

client = TestClient(app)


def test_health_check_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "kanan-cacao-geo-engine"
    assert payload["eudr_cutoff_baseline"] == "2020-12-31"


def test_domain_invariants() -> None:
    assert EUDR_CUTOFF_DATE_ISO == "2020-12-31"
    assert EUDR_MANDATORY_POLYGON_THRESHOLD_HA == 4.0
    assert TABASCO_YIELD_CAP_MIN_KG_HA == 450.0
    assert TABASCO_YIELD_CAP_MAX_KG_HA == 650.0
