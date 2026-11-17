"""Tests for spectral NDVI, raster analysis and deforestation screening ported from cacao-track."""

from app.services.satellite_audit_engine import (
    compute_pixel_ndvi,
    get_ndvi_color,
    classify_vigor,
    fallback_spatial_ndvi,
    evaluate_deforestation_risk_cacao_track,
    RAW_SATELLITE_CATALOG,
    resolve_tile_template,
)


def test_compute_pixel_ndvi_values_and_classification():
    # Dense healthy canopy (high green, low red/blue)
    high_vigor_ndvi = compute_pixel_ndvi(40, 160, 30)
    assert high_vigor_ndvi >= 0.65
    assert classify_vigor(high_vigor_ndvi) in [
        "Vigor alto (Saludable)",
        "Dosel denso y exuberante",
    ]
    assert get_ndvi_color(high_vigor_ndvi) in ["#91cf60", "#1a9850"]

    # Bare soil / dry clearing (high red/brightness, low green)
    low_vigor_ndvi = compute_pixel_ndvi(190, 130, 100)
    assert low_vigor_ndvi < 0.40
    assert get_ndvi_color(low_vigor_ndvi) in ["#d73027", "#fc8d59"]


def test_fallback_spatial_ndvi_range():
    # Tabasco coordinate
    ndvi = fallback_spatial_ndvi(-93.2289, 18.2612)
    assert 0.08 <= ndvi <= 0.95


def test_satellite_catalog_has_eudr_baseline():
    baseline = [
        s for s in RAW_SATELLITE_CATALOG if s.get("season_tag") and "2020" in s["season_tag"]
    ]
    assert len(baseline) >= 1
    assert baseline[0]["capture_date"] == "2020-12-16"
    assert "wayback.maptiles.arcgis.com" in baseline[0]["tile_url"]
    template = resolve_tile_template("2020-12-16")
    assert "15045" in template


def test_evaluate_deforestation_risk_cacao_track():
    coords = [
        [-93.2289, 18.2612],
        [-93.2274, 18.2612],
        [-93.2274, 18.2601],
        [-93.2289, 18.2601],
        [-93.2289, 18.2612],
    ]
    res = evaluate_deforestation_risk_cacao_track("PARCEL-TEST-TAB-01", coords)
    assert res["status"] in ["NO_ALERTS", "REQUIRES_REVIEW"]
    assert res["baseline_date"] == "2020-12-31"
    assert "disclaimer" in res
