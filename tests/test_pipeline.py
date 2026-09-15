"""End-to-end pipeline test: synthetic image + OCR -> Report."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.pipeline import run_scan
from backend.vision.ocr import OcrResult, Token, tesseract_available, tesseract_ocr
from backend.schemas.report import CalibrationVerdict, Status
from backend.reports.render import render_html, render_json


def test_full_scan_calibrated(scene_factory):
    # Glyph 90px tall at 0.1 mm/px => 9 mm (well above any threshold). Placed
    # well below the calibration card's own footprint (see backend.vision.card)
    # so it isn't excluded as card-printed text by the Rule 7 measurement.
    img, meta = scene_factory(marker_mm=40.0, side_px=400, glyph=(600, 650, 60, 90))
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
    fi = report.font_analysis.items[0]              # token-based measurement
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
    # Short glyph: 8px tall at 0.1mm/px => 0.8mm, below the 1.0mm absolute floor.
    # Placed below the calibration card's own footprint (see backend.vision.card)
    # so it isn't excluded as card-printed text by the Rule 7 measurement.
    img, meta = scene_factory(marker_mm=40.0, side_px=400, glyph=(600, 650, 24, 8))
    text = "MRP Rs. 45.00 (incl. of all taxes)"
    ocr = OcrResult(text=text, tokens=[Token(text=text, bbox=meta["glyph_bbox_px"], confidence=0.9)])

    report = run_scan(img, ocr, marker_mm=40.0)
    fi = report.font_analysis.items[0]
    assert fi.status == Status.POTENTIAL_NON_COMPLIANCE
    assert report.disposition == Status.POTENTIAL_NON_COMPLIANCE


def _card_only_image(dpi=200, marker_mm=40.0, dict_name="DICT_4X4_50", marker_id=0):
    """Crop just the printed card (not the whole A4 sheet) from the real generator."""
    from scripts.gen_calibration_card import A4_H_MM, A4_W_MM, build_card, mm_to_px
    from backend.vision.card import CARD_H_MM, CARD_W_MM

    page = build_card(dpi, marker_mm, dict_name, marker_id)
    page_w, page_h = mm_to_px(A4_W_MM, dpi), mm_to_px(A4_H_MM, dpi)
    card_w, card_h = mm_to_px(CARD_W_MM, dpi), mm_to_px(CARD_H_MM, dpi)
    cx0, cy0 = (page_w - card_w) // 2, (page_h - card_h) // 2
    return page[cy0:cy0 + card_h, cx0:cx0 + card_w].copy()


def _product_panel(height, width=900):
    panel = np.full((height, width, 3), 255, np.uint8)
    lines = ["MRP Rs. 45.00 incl. of all taxes", "Net Qty 200 g", "Mfg 05/2026"]
    y = 160
    for line in lines:
        cv2.putText(panel, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 1.9,
                   (0, 0, 0), 4, cv2.LINE_AA)
        y += 190
    return panel


def _card_product_scene(dpi=200, marker_mm=40.0, rotate=None):
    card = _card_only_image(dpi=dpi, marker_mm=marker_mm)
    if rotate == 90:
        card = cv2.rotate(card, cv2.ROTATE_90_CLOCKWISE)
    elif rotate == 180:
        card = cv2.rotate(card, cv2.ROTATE_180)
    panel = _product_panel(height=card.shape[0])
    return np.hstack([card, panel])


@pytest.mark.parametrize("rotate", [None, 90, 180])
def test_rule7_excludes_calibration_card_text(rotate):
    """The card's own printed text (~1.2mm) must never be reported as Rule 7 items.

    Reproduces the review finding: `_font_from_tokens` used to measure every OCR
    token on the marker image, including the card's own "DICT_4X4_50 id=0",
    "marker side = 40.0 mm", "Print at 100%" printing -- which is smaller than
    the product's real declarations and got wrongly flagged.
    """
    if not tesseract_available():
        pytest.skip("tesseract not installed")

    scene = _card_product_scene(dpi=200, marker_mm=40.0, rotate=rotate)
    ocr = tesseract_ocr(scene)

    report = run_scan([scene, scene], [ocr, OcrResult(text="", tokens=[])],
                      marker_mm=40.0, panel_area_cm2=150)

    assert report.calibration.verdict == CalibrationVerdict.CALIBRATED
    assert report.font_analysis.items, "font measured"

    banned = ("DICT_4X4_50", "40.0", "100%", "Metros", "Verify", "id=0")
    for item in report.font_analysis.items:
        label = item.declaration_id
        assert not any(b in label for b in banned), \
            f"font item references calibration card text: {label!r}"

    # At least one item should be the declaration-matched product text (MRP /
    # net quantity / mfg date), proving the fix measured product text, not a
    # lucky miss of the card.
    matched_ids = {i.declaration_id for i in report.font_analysis.items}
    assert matched_ids & {"mrp", "net_quantity", "mfg_date"}, (
        "expected at least one font item measured from a matched product "
        f"declaration, got {matched_ids!r}"
    )


def test_llm_failure_falls_back_to_ocr(monkeypatch):
    """A configured-but-failing LLM (bad/rejected credential) must not produce
    a silent all-empty report: it must fall back to real OCR + regex."""
    if not tesseract_available():
        pytest.skip("tesseract not installed")
    import backend.extract.llm as llm_mod
    from backend.core.errors import ExtractionError

    monkeypatch.setattr(llm_mod, "llm_available", lambda: True)

    def _boom(*a, **kw):
        raise ExtractionError("simulated LLM auth failure")

    monkeypatch.setattr(llm_mod, "extract_fields_llm", _boom)
    monkeypatch.setattr(llm_mod, "extract_fields_from_images", _boom)

    img = np.full((400, 900, 3), 255, np.uint8)
    cv2.putText(img, "MRP Rs. 45.00 incl. of all taxes", (20, 80),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Net Qty 200 g", (20, 160),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Mfg 05/2026", (20, 240),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)

    # Simulate the API layer: OCR was skipped up-front because the LLM path
    # looked available.
    report = run_scan(img, OcrResult(text="", tokens=[]), extract_backend="auto")

    assert report.extraction.backend_used == "ocr_regex"
    assert report.extraction.llm_error and "simulated" in report.extraction.llm_error
    mrp = next(d for d in report.declarations if d.id == "mrp")
    assert mrp.status == Status.COMPLIANT
    net_qty = next(d for d in report.declarations if d.id == "net_quantity")
    assert net_qty.status == Status.COMPLIANT


def test_no_readable_text_needs_officer_review():
    """No text from any source (LLM unavailable, OCR/label text empty) must
    become not_assessable + needs_officer_review, never a wall of not_detected."""
    blank = np.full((300, 300, 3), 255, np.uint8)
    report = run_scan(blank, OcrResult(text="", tokens=[]), extract_backend="regex")

    assert report.disposition == Status.NEEDS_OFFICER_REVIEW
    assert report.declarations, "catalog declarations still enumerated"
    assert all(d.status == Status.NOT_ASSESSABLE for d in report.declarations)
    assert all(d.note == "label text could not be read" for d in report.declarations)
    assert any("could not be read" in w for w in report.extraction.warnings)
