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
from .rules.panel import compute_panel_area_cm2
from .extract.dispatch import extract_declarations
from .schemas.report import (
    Calibration,
    CalibrationVerdict,
    ClauseRef,
    DeclarationFinding,
    Evidence,
    Extraction,
    Inspection,
    OriginalImage,
    PanelInput,
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
from .vision.card import card_outline_mm
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
                              mean_side, cal.corner_jitter_px)
    elif panel_area_cm2_known is not None:
        # Officer-supplied panel area; carry a nominal 2% uncertainty.
        area = MmMeasurement(round(panel_area_cm2_known, 3),
                             round(panel_area_cm2_known * 0.02, 3), unit="cm^2")

    items: List[GlyphInput] = []
    for f in fields:
        if f.bbox is None:
            continue
        height = glyph_height_mm(cal.H_img_to_mm, f.bbox, cal.marker_mm,
                                 mean_side, cal.corner_jitter_px)
        ratio = glyph_width_ratio(cal.H_img_to_mm, f.bbox)
        items.append(GlyphInput(f.id, height=height, width_ratio=ratio, molded=molded))
    return FontInputs(panel_area_cm2=area, items=items)


def _card_polygon_px(cal: CalibrationResult, margin_mm: float = 3.0) -> Optional[np.ndarray]:
    """The calibration card's own outline in pixel space, expanded by a margin.

    Used to exclude the card's own printed text (dictionary name, marker size,
    print instructions) from Rule 7 measurement -- that text is ~1.2mm tall and
    would otherwise be picked up as the smallest (and wrongly flagged) item.
    """
    if not cal.calibrated or cal.marker_mm is None or cal.H_img_to_mm is None:
        return None
    outline = card_outline_mm(cal.marker_mm)
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    x0, x1 = min(xs) - margin_mm, max(xs) + margin_mm
    y0, y1 = min(ys) - margin_mm, max(ys) + margin_mm
    corners_mm = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
    try:
        H_inv = np.linalg.inv(cal.H_img_to_mm)
    except np.linalg.LinAlgError:
        return None
    homog = np.hstack([corners_mm, np.ones((4, 1))])
    mapped = (H_inv @ homog.T).T
    px = mapped[:, :2] / mapped[:, 2:3]
    return px.astype(np.float32)


def _token_intersects_card(bbox, card_poly_px: Optional[np.ndarray]) -> bool:
    """True if a token's pixel bbox falls on/near the calibration card.

    The card polygon can be an arbitrary (rotated/perspective) quadrilateral in
    pixel space, so this is a point-in-polygon test on the bbox's corners and
    centre -- exact for a bbox fully inside or outside the card, and a safe
    over-exclusion for one straddling the edge.
    """
    if card_poly_px is None:
        return False
    x, y, w, h = bbox
    contour = card_poly_px.reshape(-1, 1, 2)
    points = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + w / 2, y + h / 2)]
    return any(cv2.pointPolygonTest(contour, pt, False) >= 0 for pt in points)


def _font_from_tokens(cal: CalibrationResult, tokens, molded: bool,
                      panel_area_cm2_known: Optional[float] = None,
                      fields: Optional[List[FieldExtraction]] = None,
                      panel_polygon_px: Optional[Sequence[Tuple[float, float]]] = None,
                      category: Optional[str] = None,
                      catalog: Optional[RuleCatalog] = None) -> FontInputs:
    """Measure letter height directly from OCR text tokens (Rule 7).

    Rule 7 is about the MINIMUM letter height on the PRODUCT panel, so tokens
    that fall on the calibration card itself (its own printed dictionary name /
    marker size / print instructions) are excluded first. Among what remains,
    tokens already matched to an extracted declaration (MRP, net quantity,
    dates, consumer care -- definitely product text) are preferred; only when
    none match do we fall back to the smallest unmatched product-text tokens.

    On a food/cosmetic package, Rule 7(5) (as substituted) exempts every
    declaration EXCEPT net quantity/MRP/best-before/consumer-care from Rule 7
    sizing (those declarations' content is governed by another law and their
    size isn't Metros's business either); tokens matched to any other
    declaration are excluded from measurement in that case.
    """
    if not cal.calibrated or not tokens:
        return FontInputs()
    import re
    mean_side = _mean_side_px(cal)
    card_poly_px = _card_polygon_px(cal)

    carveout_ids = None
    if category in ("food", "cosmetic") and catalog is not None:
        carveout_ids = set(catalog.rule7_carveout.get("declaration_ids", []))

    # Panel area is computed up front so the Table-I band is still reported
    # even when no glyph token survives the filters below.
    area = None
    if panel_polygon_px is not None:
        area = panel_area_cm2(cal.H_img_to_mm, panel_polygon_px, cal.marker_mm,
                              mean_side, cal.corner_jitter_px)
    elif panel_area_cm2_known is not None:
        area = MmMeasurement(round(panel_area_cm2_known, 3),
                             round(panel_area_cm2_known * 0.02, 3), unit="cm^2")

    decl_bbox_ids = {}
    for f in (fields or []):
        if f.bbox is not None:
            decl_bbox_ids[tuple(f.bbox)] = f.id

    measured = []
    for t in tokens:
        if not t.bbox:
            continue
        if _token_intersects_card(t.bbox, card_poly_px):
            continue                              # calibration card's own text
        if carveout_ids is not None:
            matched = decl_bbox_ids.get(tuple(t.bbox))
            if matched is not None and matched not in carveout_ids:
                continue  # Rule 7 doesn't apply to this declaration's sizing here
        txt = t.text.strip()
        x, y, w, h = t.bbox
        # Reject OCR noise: low confidence, too-short, or not a real word/number.
        if getattr(t, "confidence", 1.0) < 0.55:
            continue
        if w < 6 or h < 6:                       # a few-pixel speck, not text
            continue
        alnum = re.sub(r"[^A-Za-z0-9]", "", txt)
        if len(alnum) < 3:                       # need >= 3 letters/digits
            continue
        if len(alnum) / max(len(txt), 1) < 0.6:  # mostly symbols => garbage
            continue
        try:
            hmm = glyph_height_mm(cal.H_img_to_mm, t.bbox, cal.marker_mm,
                                  mean_side, cal.corner_jitter_px)
            ratio = glyph_width_ratio(cal.H_img_to_mm, t.bbox)
        except Exception:
            continue
        has_digit = bool(re.search(r"\d", txt))
        decl_id = decl_bbox_ids.get(tuple(t.bbox))
        measured.append((hmm, ratio, txt, has_digit, decl_id))
    if not measured:
        return FontInputs(panel_area_cm2=area)
    # Prefer tokens already matched to an extracted declaration (definitely
    # product text) over other digit-bearing text, over marketing words.
    decl_tokens = [m for m in measured if m[4] is not None]
    digit_tokens = [m for m in measured if m[3]]
    ranked = decl_tokens or digit_tokens or measured
    ranked.sort(key=lambda m: m[0].value)
    items = [
        GlyphInput(decl_id if decl_id is not None else f'"{txt[:18]}"',
                  height=hmm, width_ratio=ratio, molded=molded)
        for hmm, ratio, txt, _, decl_id in ranked[:3]
    ]
    return FontInputs(panel_area_cm2=area, items=items)


def _rects_intersect(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + ah and ay + ah > by


def _placement_findings(catalog: RuleCatalog, fields: List[FieldExtraction],
                        tokens) -> List[DeclarationFinding]:
    """Rule 8 placement checks: clear space around net quantity (auto-checked,
    relative pixel-space only -- no calibration needed), and a standing
    officer-review item for whether declarations are properly grouped on the
    principal display panel (Rule 2(h)), which is never auto-judged."""
    findings: List[DeclarationFinding] = []

    def _clause_ref_for(rule_id: str) -> ClauseRef:
        rule = catalog.placement_rule(rule_id)
        return ClauseRef(clause=rule.clause, source_url=rule.source_url,
                         gazette=rule.gazette, effective_from=rule.effective_from)

    clear_space_rule = catalog.placement_rule("placement_clear_space")
    net_qty = next((f for f in fields if f.id == "net_quantity" and f.bbox is not None), None)
    if net_qty is None:
        findings.append(DeclarationFinding(
            id="placement_clear_space", label=clear_space_rule.label,
            clause_ref=_clause_ref_for("placement_clear_space"),
            status=Status.NOT_ASSESSABLE,
            note="no net-quantity bounding box available to check clear space",
        ))
    else:
        x, y, w, h = net_qty.bbox
        zone = (x - 2 * h, y - h, w + 4 * h, h + 2 * h)
        intruders = [
            t.text for t in tokens
            if t.bbox and tuple(t.bbox) != tuple(net_qty.bbox) and _rects_intersect(zone, t.bbox)
        ]
        if intruders:
            findings.append(DeclarationFinding(
                id="placement_clear_space", label=clear_space_rule.label,
                clause_ref=_clause_ref_for("placement_clear_space"),
                status=Status.POTENTIAL_NON_COMPLIANCE,
                note="text intrudes on the required clear space: " + ", ".join(intruders[:5]),
            ))
        else:
            findings.append(DeclarationFinding(
                id="placement_clear_space", label=clear_space_rule.label,
                clause_ref=_clause_ref_for("placement_clear_space"),
                status=Status.COMPLIANT,
            ))

    grouping_rule = catalog.placement_rule("placement_grouping")
    findings.append(DeclarationFinding(
        id="placement_grouping", label=grouping_rule.label,
        clause_ref=_clause_ref_for("placement_grouping"),
        status=Status.NOT_ASSESSABLE,
        note="grouping of declarations on the principal display panel needs officer review",
    ))
    return findings


def _to_calibration_schema(cal: CalibrationResult) -> Calibration:
    return Calibration(
        reference="aruco_card",
        aruco_dict=cal.dict_name,
        marker_id=cal.marker_id,
        marker_mm=cal.marker_mm,
        detection_confidence=cal.detection_confidence,
        mm_per_pixel=cal.mm_per_pixel,
        corner_jitter_px=cal.corner_jitter_px,
        verdict=CalibrationVerdict.CALIBRATED if cal.calibrated
        else CalibrationVerdict.REJECTED,
        reason=cal.reason,
    )


def _overall_disposition(declarations, font_items) -> Status:
    statuses = [d.status for d in declarations] + [i.status for i in font_items]
    if any(s in (Status.POTENTIAL_NON_COMPLIANCE, Status.NOT_DETECTED) for s in statuses):
        return Status.POTENTIAL_NON_COMPLIANCE
    if any(s == Status.NOT_ASSESSABLE for s in statuses):
        return Status.NEEDS_OFFICER_REVIEW
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
    panel_shape: Optional[str] = None,
    panel_height_cm: Optional[float] = None,
    panel_width_cm: Optional[float] = None,
    panel_circumference_cm: Optional[float] = None,
    panel_area_cm2_other: Optional[float] = None,
    molded: bool = False,
    image_file: str = "upload.jpg",
    captured_at: Optional[datetime] = None,
    catalog: Optional[RuleCatalog] = None,
    extract_backend: str = "regex",
    label_text_provided: bool = False,
    common_name: Optional[str] = None,
    category: Optional[str] = None,
) -> Report:
    """Run the full pipeline over one or more images (e.g. front + back).

    `images`/`ocrs` accept a single item or a list. The calibration card is
    optional: measurement (Rule 7) runs on whichever image contains a marker; if
    none do, Rule 7 is reported not_assessable and Rule 6 is still assessed.

    The principal-display-panel area for the Rule 7 Table-I band comes from
    either a pre-computed `panel_area_cm2`, a pixel `panel_polygon_px`
    (measured through the calibration), or officer-entered dimensions
    (`panel_shape` + the matching `panel_*_cm` fields, Rule 7(4)).
    """
    settings = get_settings()
    marker_mm = marker_mm if marker_mm is not None else settings.marker_size_mm
    catalog = catalog or load_catalog()

    panel_input = None
    if panel_shape is not None:
        panel_input = PanelInput(
            shape=panel_shape, height_cm=panel_height_cm, width_cm=panel_width_cm,
            circumference_cm=panel_circumference_cm, area_cm2_other=panel_area_cm2_other,
        )
        if panel_area_cm2 is None and panel_polygon_px is None:
            panel_area_cm2 = compute_panel_area_cm2(
                panel_shape, height_cm=panel_height_cm, width_cm=panel_width_cm,
                circumference_cm=panel_circumference_cm, area_cm2_other=panel_area_cm2_other,
            )

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
                         max_corner_jitter_px=settings.max_corner_jitter_px)
        if c.calibrated:
            cal, cal_idx = c, i
            break
        if cal is None:
            cal = c  # remember an uncalibrated result as the fallback
    marker_image = images[cal_idx]

    # 2. Extraction over ALL images (vision) or combined OCR text (regex).
    combined_text = "\n".join(o.text for o in ocrs if o and o.text)
    outcome = extract_declarations(combined_text, catalog, backend=extract_backend,
                                   images=list(images), common_name_hint=common_name)
    fields = outcome.fields
    unreadable = not outcome.used_llm and not (outcome.text_read or "").strip()
    if outcome.used_llm:
        extraction_backend_used = "llm"
    elif label_text_provided:
        extraction_backend_used = "label_text"
    else:
        extraction_backend_used = "ocr_regex"

    # Font measurement (Rule 7) needs glyph boxes from the MARKER image's OCR.
    # In the vision path OCR was skipped for speed, so if a card was found but we
    # have no tokens for that image, run OCR now on just that one image.
    marker_tokens = ocrs[cal_idx].tokens if cal_idx < len(ocrs) and ocrs[cal_idx] else []
    if cal.calibrated and not marker_tokens:
        try:
            from .vision.ocr import tesseract_available, tesseract_ocr
            if tesseract_available():
                marker_tokens = tesseract_ocr(marker_image).tokens
        except Exception:
            marker_tokens = []
    if marker_tokens:
        _attach_bboxes(fields, marker_tokens)

    placement = _placement_findings(catalog, fields, marker_tokens)

    # 3. Metric font inputs (Rule 7). Prefer measuring the actual label text
    #    tokens on the calibrated image; fall back to field-bbox measurement.
    if cal.calibrated and marker_tokens:
        font_inputs = _font_from_tokens(cal, marker_tokens, molded,
                                        panel_area_cm2_known=panel_area_cm2,
                                        fields=fields, panel_polygon_px=panel_polygon_px,
                                        category=category, catalog=catalog)
    else:
        font_inputs = _build_font_inputs(cal, fields, panel_polygon_px, molded,
                                         panel_area_cm2_known=panel_area_cm2)
    font_inputs.panel_input = panel_input

    # 4. Deterministic evaluation.
    declarations, font_analysis, summary = evaluate(
        catalog, fields, font_inputs, calibrated=cal.calibrated,
        max_extrapolation_sides=settings.max_extrapolation_sides,
        category=category,
    )

    extraction_warnings: List[str] = []
    if category is None or category == "unknown":
        extraction_warnings.append(
            "product category not specified; no food/cosmetic exemptions applied"
        )
    if unreadable:
        # No text could be read from any source (LLM failed/unavailable AND
        # OCR found nothing): declarations are unknown, not "absent".
        for d in declarations:
            d.status = Status.NOT_ASSESSABLE
            d.note = "label text could not be read"
        disposition = Status.NEEDS_OFFICER_REVIEW
        extraction_warnings.append("label text could not be read from any image")
    else:
        disposition = _overall_disposition(declarations, font_analysis.items)

    extraction = Extraction(
        backend_used=extraction_backend_used,
        llm_error=outcome.llm_error,
        warnings=extraction_warnings,
    )

    # 5. Assemble report (evidence from the first image; note total count).
    primary = images[0]
    h, w = primary.shape[:2]
    report = Report(
        report_id=str(uuid.uuid4()),
        generated_at=datetime.now(timezone.utc),
        app_version=APP_VERSION,
        rule_catalog=RuleCatalogInfo(version=catalog.version, hash=catalog.hash),
        disposition=disposition,
        inspection=inspection or Inspection(),
        product=product or Product(),
        evidence=Evidence(original=OriginalImage(
            file=image_file, sha256=_sha256_of_image(primary),
            captured_at=captured_at, width=w, height=h)),
        calibration=_to_calibration_schema(cal),
        extraction=extraction,
        summary=summary,
        declarations=declarations,
        placement=placement,
        font_analysis=font_analysis,
        legal_basis={"statute": ", ".join(
            p for p in (catalog.statute.get("section"), catalog.statute.get("act"))
            if p) or "Legal Metrology Act, 2009"},
    )
    return report
