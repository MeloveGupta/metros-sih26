"""End-to-end pipeline test: synthetic image + OCR -> Report."""
from __future__ import annotations

import pytest

from backend.pipeline import run_scan
from backend.vision.ocr import OcrResult, Token
from backend.schemas.report import CalibrationVerdict, Status
from backend.reports.render import render_html, render_json


def test_full_scan_calibrated(scene_factory):
    # Glyph 90px tall at 0.1 mm/px => 9 mm (well above any threshold).
    img, meta = scene_factory(marker_mm=40.0, side_px=400, glyph=(600, 150, 60, 90))
    text = "MRP Rs. 45.00 (incl. of all taxes)"
    ocr = OcrResult(text=text, tokens=[Token(text=text, bbox=meta["glyph_bbox_px"], confidence=0.9)])

    # Panel polygon ~ 250 cm^2 (500x500 px at 0.1mm/px = 50x50mm = 25cm^2 -> use bigger)
    # 500x500 px => 50mm x 50mm = 2500 mm^2 = 25 cm^2. Use 1000x500 -> 50 cm^2 band edge;
    # pick 900x560 => 90x56mm = 5040mm^2 = 50.4 cm^2 -> band 50<=A<100 (1.5mm).
    panel = [(560, 120), (1460, 120), (1460, 680), (560, 680)]

    report = run_scan(img, ocr, marker_mm=40.0, panel_polygon_px=panel)

    assert report.calibration.verdict == CalibrationVerdict.CALIBRATED
    mrp = next(d for d in report.declarations if d.id == "mrp")
    assert mrp.status == Status.COMPLIANT           # good MRP format
    assert report.font_analysis.items, "font measured"
    fi = next(i for i in report.font_analysis.items if i.declaration_id == "mrp")
    assert fi.height_mm.value == pytest.approx(9.0, rel=0.05)
    assert fi.status == Status.COMPLIANT            # 9mm >> threshold

    # Report renders in both formats.
    assert "Rule 6(1)(e)" in render_html(report)
    assert "\"disposition\"" in render_json(report)


def test_full_scan_uncalibrated_no_mm(scene_factory):
    # Blank-ish scene: no marker at all.
    import numpy as np
    blank = np.full((600, 800, 3), 255, np.uint8)
    text = "MRP Rs. 45.00 (incl. of all taxes)"
    ocr = OcrResult(text=text, tokens=[Token(text=text, bbox=(50, 50, 60, 90), confidence=0.9)])

    report = run_scan(blank, ocr, marker_mm=40.0)
    assert report.calibration.verdict == CalibrationVerdict.REJECTED
    # No calibration => any font item is not_assessable, no mm verdict.
    for item in report.font_analysis.items:
        assert item.status == Status.NOT_ASSESSABLE
        assert item.height_mm is None


def test_disposition_flags_when_font_below(scene_factory):
    # Tiny glyph: 12px tall at 0.1mm/px => 1.2mm, panel 50<=A<100 threshold 1.5mm.
    img, meta = scene_factory(marker_mm=40.0, side_px=400, glyph=(600, 150, 8, 12))
    text = "MRP Rs. 45.00 (incl. of all taxes)"
    ocr = OcrResult(text=text, tokens=[Token(text=text, bbox=meta["glyph_bbox_px"], confidence=0.9)])
    panel = [(560, 120), (1460, 120), (1460, 680), (560, 680)]  # ~50 cm^2

    report = run_scan(img, ocr, marker_mm=40.0, panel_polygon_px=panel)
    fi = next(i for i in report.font_analysis.items if i.declaration_id == "mrp")
    assert fi.status == Status.POTENTIAL_NON_COMPLIANCE
    assert report.disposition == Status.POTENTIAL_NON_COMPLIANCE
