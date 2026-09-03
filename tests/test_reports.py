"""Tests for report rendering (JSON, HTML, DOCX)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.reports.render import render_docx, render_html, render_json
from backend.schemas.report import (
    Calibration,
    CalibrationVerdict,
    ClauseRef,
    DeclarationFinding,
    Evidence,
    FontAnalysis,
    FontItem,
    Measurement,
    OriginalImage,
    Report,
    RuleCatalogInfo,
    Status,
    Summary,
    TableIBand,
)


@pytest.fixture
def sample_report() -> Report:
    return Report(
        report_id="r-1",
        ref_no="MS-2026-0001",
        generated_at=datetime(2026, 9, 3, 20, 15, tzinfo=timezone.utc),
        rule_catalog=RuleCatalogInfo(version="2011 (amended 2022)", hash="sha256:abc"),
        evidence=Evidence(original=OriginalImage(
            file="chips.jpg", sha256="deadbeef", width=1200, height=1600)),
        calibration=Calibration(
            aruco_dict="DICT_4X4_50", marker_id=0, marker_mm=40.0,
            mm_per_pixel=0.1, homography_residual_px=0.8, detection_confidence=0.95,
            verdict=CalibrationVerdict.CALIBRATED),
        summary=Summary(checked=2, compliant=1, potential_non_compliance=1,
                        overall_confidence=0.5,
                        required_actions=["Verify letter height for mrp (Rule 7)"]),
        declarations=[
            DeclarationFinding(id="mrp", label="Retail sale price (MRP)",
                               clause_ref=ClauseRef(clause="Rule 6(1)(e)"),
                               extracted="MRP Rs. 45.00 (incl. of all taxes)",
                               status=Status.COMPLIANT),
        ],
        font_analysis=FontAnalysis(
            panel_area_cm2=Measurement(value=250.0, uncertainty=5.0, unit="cm^2"),
            table_i_band=TableIBand(area_band="100<=A<500", min_height_mm=2.5,
                                    min_height_mm_molded=4.0),
            items=[FontItem(declaration_id="mrp",
                            height_mm=Measurement(value=1.5, uncertainty=0.1),
                            threshold_mm=2.5,
                            status=Status.POTENTIAL_NON_COMPLIANCE,
                            reason="below the 2.5mm minimum; verify")],
        ),
        legal_basis={"statute": "Section 18, Legal Metrology Act, 2009"},
    )


def test_render_json_roundtrips(sample_report):
    data = json.loads(render_json(sample_report))
    assert data["ref_no"] == "MS-2026-0001"
    assert data["calibration"]["dict"] == "DICT_4X4_50"   # alias preserved
    assert data["font_analysis"]["items"][0]["status"] == "potential_non_compliance"


def test_render_html_has_key_content(sample_report):
    html = render_html(sample_report)
    assert "DECISION-SUPPORT" in html
    assert "Rule 6(1)(e)" in html
    assert "1.50 ± 0.10 mm" in html            # mm with uncertainty
    assert "s-potential_non_compliance" in html  # status color class
    assert "VIOLATION" not in html               # never the word violation


def test_render_docx_writes_file(sample_report, tmp_path):
    out = tmp_path / "report.docx"
    render_docx(sample_report, out)
    assert out.exists() and out.stat().st_size > 0
    # DOCX is a zip; verify it opens.
    from docx import Document
    doc = Document(str(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "DECISION-SUPPORT" in text
