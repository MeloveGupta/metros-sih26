"""End-to-end scan pipeline: image + OCR -> compliance Report.

Wires the moat together: scale recovery -> field extraction -> metric font
measurement -> deterministic rule engine -> canonical report. Every mm figure is
gated on a valid calibration; without a marker, font items are `not_assessable`
and no millimetre verdict is emitted.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .core.config import get_settings
from .rules.catalog import RuleCatalog, load_catalog
from .rules.engine import FieldExtraction, FontInputs, GlyphInput, evaluate
from .extract.dispatch import extract_declarations
from .schemas.report import (
    Calibration,
    CalibrationVerdict,
    DeclarationFinding,
    Evidence,
    Inspection,
    OriginalImage,
    Product,
    Report,
    RuleCatalogInfo,
    Status,
)
from .vision.measure import (
    MmMeasurement,
    glyph_height_mm,
    glyph_width_ratio,
    panel_area_cm2,
)
from .vision.ocr import OcrResult, Token
from .vision.scale import CalibrationResult, detect_scale

APP_VERSION = "0.1.0"


def _sha256_of_image(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        return "sha256:unavailable"
    return "sha256:" + hashlib.sha256(buf.tobytes()).hexdigest()


def _attach_bboxes(fields: List[FieldExtraction], tokens: Sequence[Token]) -> None:
    """Best-effort: give each detected field the bbox of the token it came from."""
    for f in fields:
        if not f.present or f.value is None or f.bbox is not None:
            continue
        needle = f.value.strip().lower()[:24]
        for tok in tokens:
            if tok.bbox is None:
                continue
            hay = tok.text.strip().lower()
            if needle and (needle in hay or hay in needle):
                f.bbox = tok.bbox
                if f.ocr_confidence is None:
                    f.ocr_confidence = tok.confidence
                break


def _mean_side_px(cal: CalibrationResult) -> float:
    if cal.corners_px is None:
        return 0.0
    c = cal.corners_px
    sides = [np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)]
    return float(np.mean(sides))


def _build_font_inputs(
    cal: CalibrationResult,
    fields: List[FieldExtraction],
    panel_polygon_px: Optional[Sequence[Tuple[float, float]]],
    molded: bool,
    panel_area_cm2_known: Optional[float] = None,
):
    """Measure panel area and per-declaration glyph heights when calibrated."""
    if not cal.calibrated:
        # Still surface which declarations *would* be measured, as not_assessable.
        items = [GlyphInput(f.id, molded=molded) for f in fields if f.bbox is not None]
        return FontInputs(items=items)

    mean_side = _mean_side_px(cal)
    area = None
    if panel_polygon_px is not None:
        area = panel_area_cm2(cal.H_img_to_mm, panel_polygon_px, cal.marker_mm,
                              mean_side, cal.residual_px)
    elif panel_area_cm2_known is not None:
        # Officer-supplied panel area; carry a nominal 2% uncertainty.
        area = MmMeasurement(round(panel_area_cm2_known, 3),
                             round(panel_area_cm2_known * 0.02, 3), unit="cm^2")

    items: List[GlyphInput] = []
    for f in fields:
        if f.bbox is None:
            continue
        height = glyph_height_mm(cal.H_img_to_mm, f.bbox, cal.marker_mm,
                                 mean_side, cal.residual_px)
        ratio = glyph_width_ratio(cal.H_img_to_mm, f.bbox)
        items.append(GlyphInput(f.id, height=height, width_ratio=ratio, molded=molded))
    return FontInputs(panel_area_cm2=area, items=items)


def _to_calibration_schema(cal: CalibrationResult) -> Calibration:
    return Calibration(
        reference="aruco_card",
        aruco_dict=cal.dict_name,
        marker_id=cal.marker_id,
        marker_mm=cal.marker_mm,
        detection_confidence=cal.detection_confidence,
        mm_per_pixel=cal.mm_per_pixel,
        homography_residual_px=cal.residual_px,
        verdict=CalibrationVerdict.CALIBRATED if cal.calibrated
        else CalibrationVerdict.REJECTED,
        reason=cal.reason,
    )


def _overall_disposition(declarations, font_items) -> Status:
    statuses = [d.status for d in declarations] + [i.status for i in font_items]
    if any(s == Status.POTENTIAL_NON_COMPLIANCE for s in statuses):
        return Status.POTENTIAL_NON_COMPLIANCE
    if any(s in (Status.NOT_DETECTED, Status.NOT_ASSESSABLE) for s in statuses):
        return Status.POTENTIAL_NON_COMPLIANCE
    return Status.COMPLIANT


def run_scan(
    images,
    ocrs,
    *,
    marker_mm: Optional[float] = None,
    dict_name: str = "DICT_4X4_50",
    marker_id: Optional[int] = None,
    product: Optional[Product] = None,
    inspection: Optional[Inspection] = None,
    panel_polygon_px: Optional[Sequence[Tuple[float, float]]] = None,
    panel_area_cm2: Optional[float] = None,
    molded: bool = False,
    image_file: str = "upload.jpg",
    captured_at: Optional[datetime] = None,
    catalog: Optional[RuleCatalog] = None,
    extract_backend: str = "regex",
) -> Report:
    """Run the full pipeline over one or more images (e.g. front + back).

    `images`/`ocrs` accept a single item or a list. The calibration card is
    optional: measurement (Rule 7) runs on whichever image contains a marker; if
    none do, Rule 7 is reported not_assessable and Rule 6 is still assessed.
    """
    settings = get_settings()
    marker_mm = marker_mm if marker_mm is not None else settings.marker_size_mm
    catalog = catalog or load_catalog()

    if not isinstance(images, (list, tuple)):
        images = [images]
    if not isinstance(ocrs, (list, tuple)):
        ocrs = [ocrs]

    # 1. Scale: use the first image that yields a valid calibration.
    cal = None
    cal_idx = 0
    for i, img in enumerate(images):
        c = detect_scale(img, marker_mm=marker_mm, dict_name=dict_name,
                         marker_id=marker_id,
                         max_residual_px=settings.max_homography_residual_px)
        if c.calibrated:
            cal, cal_idx = c, i
            break
        if cal is None:
            cal = c  # remember an uncalibrated result as the fallback
    marker_image = images[cal_idx]

    # 2. Extraction over ALL images (vision) or combined OCR text (regex).
    combined_text = "\n".join(o.text for o in ocrs if o and o.text)
    fields = extract_declarations(combined_text, catalog, backend=extract_backend,
                                  images=list(images))
    # Font measurement needs boxes from the marker image's OCR.
    if cal_idx < len(ocrs) and ocrs[cal_idx]:
        _attach_bboxes(fields, ocrs[cal_idx].tokens)

    # 3. Metric font inputs (only meaningful when calibrated).
    font_inputs = _build_font_inputs(cal, fields, panel_polygon_px, molded,
                                     panel_area_cm2_known=panel_area_cm2)

    # 4. Deterministic evaluation.
    declarations, font_analysis, summary = evaluate(
        catalog, fields, font_inputs, calibrated=cal.calibrated
    )

    # 5. Assemble report (evidence from the first image; note total count).
    primary = images[0]
    h, w = primary.shape[:2]
    report = Report(
        report_id=str(uuid.uuid4()),
        generated_at=datetime.now(timezone.utc),
        app_version=APP_VERSION,
        rule_catalog=RuleCatalogInfo(version=catalog.version, hash=catalog.hash),
        disposition=_overall_disposition(declarations, font_analysis.items),
        inspection=inspection or Inspection(),
        product=product or Product(),
        evidence=Evidence(original=OriginalImage(
            file=image_file, sha256=_sha256_of_image(primary),
            captured_at=captured_at, width=w, height=h)),
        calibration=_to_calibration_schema(cal),
        summary=summary,
        declarations=declarations,
        font_analysis=font_analysis,
        legal_basis={"statute": ", ".join(
            p for p in (catalog.statute.get("section"), catalog.statute.get("act"))
            if p) or "Legal Metrology Act, 2009"},
    )
    return report
