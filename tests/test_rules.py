"""Tests for the rule catalog loader and deterministic engine."""
from __future__ import annotations

import pytest

from backend.rules.catalog import load_catalog
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


def test_engine_is_deterministic(catalog):
    fields = [FieldExtraction(id="mrp", present=True, value="MRP Rs. 10", format_pass=True)]
    out1 = evaluate(catalog, fields, FontInputs(), calibrated=True)
    out2 = evaluate(catalog, fields, FontInputs(), calibrated=True)
    assert [d.status for d in out1[0]] == [d.status for d in out2[0]]
