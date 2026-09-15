"""Tests for the rule catalog loader and deterministic engine."""
from __future__ import annotations

import pytest

from backend.rules.catalog import load_catalog
from backend.rules.panel import compute_panel_area_cm2
from backend.rules.engine import (
    FieldExtraction,
    FontInputs,
    GlyphInput,
    evaluate,
)
from backend.vision.measure import MmMeasurement
from backend.schemas.report import Status


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()  # loads rules/lmpc-2011.yaml


def test_catalog_loads_and_hashes(catalog):
    assert catalog.hash.startswith("sha256:")
    assert any(d.id == "mrp" for d in catalog.declarations)
    mrp = catalog.declaration("mrp")
    assert mrp.clause.startswith("Rule 6")


def test_band_selection_matches_table_i(catalog):
    # Real Table-I (GSR 629(E)): A<50 -> 1.0mm; 100<=A<500 -> 2.5mm; A>=2500 -> 6.0mm
    assert catalog.select_band(30).min_height_mm == 1.0
    assert catalog.select_band(250).min_height_mm == 2.5
    assert catalog.select_band(5000).min_height_mm == 6.0


def test_missing_declaration_is_not_detected(catalog):
    fields = [FieldExtraction(id="mrp", present=False)]
    decls, _, summary = evaluate(catalog, fields, FontInputs(), calibrated=True)
    mrp = next(d for d in decls if d.id == "mrp")
    assert mrp.status == Status.NOT_DETECTED
    assert summary.not_detected >= 1


def test_bad_format_is_potential_non_compliance(catalog):
    fields = [FieldExtraction(id="mrp", present=True, value="45 rupees",
                              format_pass=False, format_pattern="MRP ...")]
    decls, _, _ = evaluate(catalog, fields, FontInputs(), calibrated=True)
    mrp = next(d for d in decls if d.id == "mrp")
    assert mrp.status == Status.POTENTIAL_NON_COMPLIANCE


def test_good_declaration_is_compliant(catalog):
    fields = [FieldExtraction(id="mrp", present=True,
                              value="MRP Rs. 45.00 (incl. of all taxes)",
                              format_pass=True)]
    decls, _, _ = evaluate(catalog, fields, FontInputs(), calibrated=True)
    mrp = next(d for d in decls if d.id == "mrp")
    assert mrp.status == Status.COMPLIANT


def test_font_below_threshold_flags(catalog):
    # Panel 250 cm^2 -> threshold 2.5mm; glyph 1.5±0.1mm clearly below.
    font = FontInputs(
        panel_area_cm2=MmMeasurement(250.0, 5.0, "cm^2"),
        items=[GlyphInput("mrp", height=MmMeasurement(1.5, 0.1))],
    )
    _, fa, _ = evaluate(catalog, [], font, calibrated=True)
    item = fa.items[0]
    assert item.threshold_mm == 2.5
    assert item.status == Status.POTENTIAL_NON_COMPLIANCE


def test_font_above_threshold_compliant(catalog):
    font = FontInputs(
        panel_area_cm2=MmMeasurement(250.0, 5.0, "cm^2"),
        items=[GlyphInput("mrp", height=MmMeasurement(3.0, 0.1))],
    )
    _, fa, _ = evaluate(catalog, [], font, calibrated=True)
    assert fa.items[0].status == Status.COMPLIANT


def test_uncalibrated_font_is_not_assessable(catalog):
    font = FontInputs(items=[GlyphInput("mrp", height=None)])
    _, fa, _ = evaluate(catalog, [], font, calibrated=False)
    assert fa.items[0].status == Status.NOT_ASSESSABLE


# --- 2.2: Rule 7(3) no longer has its own 1mm/2mm floor (GSR 629(E)) ---

def test_no_panel_area_below_every_band_minimum_flags(catalog):
    # 0.5mm is below Table-I band 1's own minimum (1.0mm) -- fails every band,
    # so this can be ruled out even without knowing the real panel area.
    font = FontInputs(items=[GlyphInput("mrp", height=MmMeasurement(0.5, 0.05))])
    _, fa, _ = evaluate(catalog, [], font, calibrated=True)
    item = fa.items[0]
    assert item.status == Status.POTENTIAL_NON_COMPLIANCE
    assert "Table-I" in item.reason
    assert item.threshold_mm == 1.0


def test_no_panel_area_above_band1_minimum_is_not_assessable(catalog):
    # 3.0mm clears Table-I band 1's minimum, but without the panel area we
    # don't know which band actually applies -- can't confirm compliance.
    font = FontInputs(items=[GlyphInput("mrp", height=MmMeasurement(3.0, 0.1))])
    _, fa, _ = evaluate(catalog, [], font, calibrated=True)
    item = fa.items[0]
    assert item.status == Status.NOT_ASSESSABLE
    assert item.reason == "panel area needed to select the Table-I band"


def test_font_absolute_has_no_height_floor(catalog):
    assert "min_height_mm" not in catalog.font_absolute
    assert "min_height_mm_molded" not in catalog.font_absolute


def test_font_far_from_marker_is_not_assessable(catalog):
    # Text 6 marker-side-lengths from the marker centre (beyond the default
    # 4-side extrapolation limit) can't be trusted, even if it looks compliant.
    font = FontInputs(
        panel_area_cm2=MmMeasurement(250.0, 5.0, "cm^2"),
        items=[GlyphInput("mrp", height=MmMeasurement(3.0, 0.1, extrapolation_d=6.0))],
    )
    _, fa, _ = evaluate(catalog, [], font, calibrated=True, max_extrapolation_sides=4.0)
    item = fa.items[0]
    assert item.status == Status.NOT_ASSESSABLE
    assert "too far" in item.reason


def test_font_near_marker_is_assessable(catalog):
    font = FontInputs(
        panel_area_cm2=MmMeasurement(250.0, 5.0, "cm^2"),
        items=[GlyphInput("mrp", height=MmMeasurement(3.0, 0.1, extrapolation_d=1.0))],
    )
    _, fa, _ = evaluate(catalog, [], font, calibrated=True, max_extrapolation_sides=4.0)
    assert fa.items[0].status == Status.COMPLIANT


def test_engine_is_deterministic(catalog):
    fields = [FieldExtraction(id="mrp", present=True, value="MRP Rs. 10", format_pass=True)]
    out1 = evaluate(catalog, fields, FontInputs(), calibrated=True)
    out2 = evaluate(catalog, fields, FontInputs(), calibrated=True)
    assert [d.status for d in out1[0]] == [d.status for d in out2[0]]


def test_compute_panel_area_rectangular():
    assert compute_panel_area_cm2("rectangular", height_cm=20, width_cm=30) == 600.0


def test_compute_panel_area_cylindrical():
    assert compute_panel_area_cm2("cylindrical", height_cm=10, circumference_cm=31.4) == \
        pytest.approx(125.6)


def test_compute_panel_area_other_uses_officer_value():
    assert compute_panel_area_cm2("other", area_cm2_other=75.0) == 75.0


def test_compute_panel_area_missing_dims_raises():
    with pytest.raises(ValueError):
        compute_panel_area_cm2("rectangular", height_cm=20)
