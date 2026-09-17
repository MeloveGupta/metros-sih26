"""End-to-end scan pipeline: image + OCR -> compliance Report.

Wires the moat together: scale recovery -> field extraction -> metric font
measurement -> deterministic rule engine -> canonical report. Every mm figure is
gated on a valid calibration; without a marker, font items are `not_assessable`
and no millimetre verdict is emitted.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .core.config import get_settings
from .rules.catalog import RuleCatalog, load_catalog
from .rules.engine import FieldExtraction, FontInputs, GlyphInput, evaluate, summarize
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
    glyph_height_mm_boxes,
    glyph_width_ratio,
    glyph_width_ratio_boxes,
    panel_area_cm2,
)
from .vision.card import card_outline_mm
from .vision.glyphs import extract_glyph_boxes
from .vision.ocr import Token
from .vision.quality import blur_score, glare_fraction, measure_contrast
from .vision.scale import CalibrationResult, detect_scale

# Only digits and uppercase letters have a reliable, unambiguous cap height
# for glyph-box measurement; lowercase x-height/ascenders/descenders are not
# used (see LIMITATIONS_TEXT).
_MEASURABLE_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")

APP_VERSION = "0.1.0"


def _sha256_of_image(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        return "sha256:unavailable"
    return "sha256:" + hashlib.sha256(buf.tobytes()).hexdigest()


@dataclass
class EvidenceImageInput:
    """One already-persisted upload: the real file on disk and the hash of its
    actual bytes (not a re-encoding of the decoded pixels)."""

    path: str                    # relative to settings.uploads_dir
    sha256: str
    role: str = "other"


def _save_crops(declarations: List[DeclarationFinding], image: np.ndarray,
                uploads_dir: Path, report_id: str) -> None:
    """Crop + save evidence for every finding with a bbox, in-place.

    `evidence_crop` is stored relative to `settings.uploads_dir`, matching how
    original image paths are stored, so both resolve the same way when served.
    """
    crops_dir = uploads_dir / report_id / "crops"
    h, w = image.shape[:2]
    made_any = False
    for d in declarations:
        if d.bbox is None:
            continue
        x, y, bw, bh = d.bbox
        pad = max(4, int(0.15 * max(bw, bh)))
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = image[y0:y1, x0:x1]
        ok, buf = cv2.imencode(".png", crop)
        if not ok:
            continue
        if not made_any:
            crops_dir.mkdir(parents=True, exist_ok=True)
            made_any = True
        (crops_dir / f"{d.id}.png").write_bytes(buf.tobytes())
        d.evidence_crop = f"{report_id}/crops/{d.id}.png"


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
                      catalog: Optional[RuleCatalog] = None,
                      image: Optional[np.ndarray] = None) -> FontInputs:
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

    exempt_chars = set(catalog.font_absolute.get("width_ratio_exceptions", ["1", "i", "I", "l"])) \
        if catalog is not None else {"1", "i", "I", "l"}

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
            hmm, ratio, ratio_char = _measure_token(cal, t.bbox, txt, image, mean_side,
                                                    exempt_chars)
        except Exception:
            continue
        has_digit = bool(re.search(r"\d", txt))
        decl_id = decl_bbox_ids.get(tuple(t.bbox))
        measured.append((hmm, ratio, txt, has_digit, decl_id, ratio_char))
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
                  height=hmm, width_ratio=ratio, width_ratio_char=ratio_char, molded=molded)
        for hmm, ratio, txt, _, decl_id, ratio_char in ranked[:3]
    ]
    return FontInputs(panel_area_cm2=area, items=items)


def _measure_token(cal: CalibrationResult, bbox, text: str, image: Optional[np.ndarray],
                   mean_side_px: float, exempt_chars: set):
    """Measure one token's height and width ratio, preferring individual
    character-glyph boxes (Rule 7(3) needs per-glyph width, and height should
    come from digit/uppercase glyphs, not a whole multi-character word box).
    Falls back to the whole-token bbox when no image is available or no
    glyph box survives extraction (e.g. a very small/blurry crop)."""
    glyph_boxes = extract_glyph_boxes(image, bbox, text) if image is not None else []
    hmm = None
    ratio_result = None
    if glyph_boxes:
        hmm = glyph_height_mm_boxes(cal.H_img_to_mm, glyph_boxes, cal.marker_mm,
                                    mean_side_px, cal.corner_jitter_px,
                                    measurable_chars=_MEASURABLE_CHARS)
        ratio_result = glyph_width_ratio_boxes(cal.H_img_to_mm, glyph_boxes,
                                               exempt_chars=exempt_chars)
    if hmm is None:
        hmm = glyph_height_mm(cal.H_img_to_mm, bbox, cal.marker_mm, mean_side_px,
                              cal.corner_jitter_px)
    if ratio_result is None:
        ratio, ratio_char = glyph_width_ratio(cal.H_img_to_mm, bbox), None
    else:
        ratio, ratio_char = ratio_result
    return hmm, ratio, ratio_char


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


def _apply_contrast_checks(declarations: List[DeclarationFinding], image, molded: bool,
                           catalog: RuleCatalog) -> None:
    """Rule 9(1)(b): MRP and net-quantity numerals must contrast conspicuously
    with the background. Exempt when blown/formed/moulded on glass or plastic."""
    if image is None or molded:
        return
    ccfg = catalog.readability.get("contrast", {})
    threshold = float(ccfg.get("min_contrast_ratio", 4.5))
    clause = ccfg.get("clause", "Rule 9(1)(b)")
    for d in declarations:
        if d.id not in ("mrp", "net_quantity") or d.bbox is None:
            continue
        try:
            ratio = measure_contrast(image, d.bbox)
        except Exception:
            continue
        if ratio is None or ratio >= threshold:
            continue
        note = f"low contrast ({ratio:.1f}:1, heuristic minimum {threshold:.1f}:1; {clause})"
        d.note = f"{d.note}; {note}" if d.note else note
        if d.status == Status.COMPLIANT:
            d.status = Status.POTENTIAL_NON_COMPLIANCE


def _apply_image_quality(declarations: List[DeclarationFinding], image,
                         catalog: RuleCatalog) -> List[str]:
    """Blur/glare gate: below/above threshold, warn and treat any not_detected
    declaration as not_assessable instead (the photo may simply be unreadable,
    not evidence the declaration is actually missing)."""
    if image is None:
        return []
    qcfg = catalog.readability.get("image_quality", {})
    min_blur = float(qcfg.get("min_blur_variance", 50.0))
    max_glare = float(qcfg.get("max_glare_fraction", 0.15))
    warnings: List[str] = []
    try:
        blur = blur_score(image)
        glare = glare_fraction(image)
    except Exception:
        return warnings
    if blur < min_blur:
        warnings.append(f"image_quality: retake photo (blurry -- sharpness {blur:.0f} "
                        f"below the {min_blur:.0f} heuristic threshold)")
    if glare > max_glare:
        warnings.append(f"image_quality: retake photo (glare -- {glare * 100:.0f}% of pixels "
                        f"saturated, above the {max_glare * 100:.0f}% heuristic threshold)")
    if warnings:
        for d in declarations:
            if d.status == Status.NOT_DETECTED:
                d.status = Status.NOT_ASSESSABLE
                extra = "image quality too poor to reliably read (blurry/glare)"
                d.note = f"{extra}; {d.note}" if d.note else extra
    return warnings


def _readability_findings(catalog: RuleCatalog, combined_text: str) -> List[DeclarationFinding]:
    """Rule 9(4) language, and a standing Rule 6(3) sticker checklist item
    (never auto-detected, always routed to officer review)."""
    rcfg = catalog.readability
    findings: List[DeclarationFinding] = []

    sticker_cfg = rcfg.get("stickers", {})
    findings.append(DeclarationFinding(
        id="readability_stickers", label="Check for stickers altering mandatory declarations",
        clause_ref=ClauseRef(clause=sticker_cfg.get("clause", "Rule 6(3)"),
                             gazette=sticker_cfg.get("gazette"),
                             effective_from=sticker_cfg.get("effective_from")),
        status=Status.NOT_ASSESSABLE,
        note="not auto-detected; officer must check for stickers over declarations",
    ))

    lang_cfg = rcfg.get("language", {})
    text = combined_text or ""
    if not text.strip():
        status, note = Status.NOT_ASSESSABLE, "no text read to check language"
    elif _DEVANAGARI_RE.search(text) or _LATIN_RE.search(text):
        status, note = Status.COMPLIANT, None
    else:
        status, note = (
            Status.NOT_ASSESSABLE,
            "neither Hindi (Devanagari) nor English detected in the read text; verify",
        )
    findings.append(DeclarationFinding(
        id="readability_language", label="Declarations in Hindi (Devanagari) or English",
        clause_ref=ClauseRef(clause=lang_cfg.get("clause", "Rule 9(4)"),
                             gazette=lang_cfg.get("gazette"), effective_from=lang_cfg.get("effective_from")),
        status=status, note=note,
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
    ocr_backend_used: str = "tesseract",
    ocr_warning: Optional[str] = None,
    extract_backend: str = "regex",
    label_text_provided: bool = False,
    common_name: Optional[str] = None,
    category: Optional[str] = None,
    report_id: Optional[str] = None,
    evidence_images: Optional[Sequence[EvidenceImageInput]] = None,
    save_crops: bool = False,
    skip_physical_measurement: bool = False,
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

    # 1. Scale: use the first image that yields a valid calibration. An
    #    e-commerce listing screenshot has no physical calibration card, so
    #    don't waste time looking for one (and don't risk a false positive
    #    match against something in the screenshot).
    cal_idx = 0
    if skip_physical_measurement:
        cal = CalibrationResult(
            found=False, dict_name=dict_name,
            reason="not applicable: e-commerce listing scan (no physical pack to calibrate)",
        )
    else:
        cal = None
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

    # 2. Extraction over the combined OCR/label text (regex by default; Gemini
    #    vision when requested). Known-bug fix: pasted label text is always
    #    authoritative and complete, so images are only ever handed to the
    #    vision path when there is NO label text -- a blank placeholder image
    #    (the API layer's text-only e-commerce-listing stand-in) never reaches
    #    the model, regardless of what's in `images`.
    combined_text = "\n".join(o.text for o in ocrs if o and o.text)
    image_hashes = (
        [_sha256_of_image(img) for img in images]
        if extract_backend == "gemini" and not label_text_provided else None
    )
    outcome = extract_declarations(
        combined_text, catalog, backend=extract_backend,
        images=(images if not label_text_provided else None),
        image_hashes=image_hashes,
        common_name_hint=common_name,
    )
    fields = outcome.fields
    unreadable = not outcome.used_gemini and not (outcome.text_read or "").strip()
    if label_text_provided:
        extraction_backend_used = "label_text"
    elif outcome.used_gemini:
        extraction_backend_used = "gemini"
    else:
        extraction_backend_used = ocr_backend_used

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

    # Rule 8 placement is a physical-panel layout check; it has nothing to
    # measure on an e-commerce listing screenshot.
    placement = [] if skip_physical_measurement else _placement_findings(catalog, fields, marker_tokens)

    # 3. Metric font inputs (Rule 7). Prefer measuring the actual label text
    #    tokens on the calibrated image; fall back to field-bbox measurement.
    if cal.calibrated and marker_tokens:
        font_inputs = _font_from_tokens(cal, marker_tokens, molded,
                                        panel_area_cm2_known=panel_area_cm2,
                                        fields=fields, panel_polygon_px=panel_polygon_px,
                                        category=category, catalog=catalog, image=marker_image)
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

    if skip_physical_measurement:
        # Rule 6(10): an e-commerce entity need not display the month/year of
        # manufacture on the digital listing (the physical pack itself must
        # still carry it when delivered -- see the rule's own Explanation).
        mfg = next((d for d in declarations if d.id == "mfg_date"), None)
        if mfg is not None:
            mfg.status = Status.NOT_APPLICABLE
            mfg.note = ("not required on an e-commerce listing (Rule 6(10)); the "
                        "physical pack delivered to the consumer must still carry it")
        # Rule 7 letter height has no physical panel to measure here.
        font_analysis.items = []
        # Recompute the summary/counts against the overridden findings above --
        # evaluate()'s own summary was built before these listing-mode changes.
        summary = summarize(declarations, font_analysis)

    # Rule 9 readability: contrast (mrp/net_quantity only), image quality
    # (blur/glare -- gates not_detected -> not_assessable), language, stickers.
    # Contrast needs the SAME image the bboxes were measured on (marker_image);
    # image quality (blur/glare) is judged on the primary evidence image.
    _apply_contrast_checks(declarations, marker_image, molded, catalog)
    # Blur/glare are physical-photo artifacts; skip them for a listing, where
    # the "image" may be a placeholder (text-only input) or a lossless
    # screenshot that was never subject to camera focus/glare in the first place.
    extraction_warnings: List[str] = (
        [] if skip_physical_measurement else _apply_image_quality(declarations, images[0], catalog)
    )
    readability = _readability_findings(catalog, combined_text)

    if ocr_warning:
        extraction_warnings.append(ocr_warning)
    if outcome.gemini_error:
        # Carries "Gemini free-tier quota reached..." verbatim when that's the
        # actual cause (see gemini_reader._call_with_model_fallback); any other
        # Gemini failure still surfaces here so a fallback to OCR is never silent.
        extraction_warnings.append(
            f"Gemini extraction unavailable, fell back to Tesseract OCR + regex: "
            f"{outcome.gemini_error}"
        )
    if skip_physical_measurement:
        extraction_warnings.append(
            "letter height (Rule 7) and placement (Rule 8) are not assessed for an "
            "e-commerce listing scan -- there is no physical pack to measure"
        )
    if category is None or category == "unknown":
        extraction_warnings.append(
            "product category not specified; no food/cosmetic exemptions applied"
        )
    if unreadable:
        # No text could be read from any source (no pasted label text AND
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
        warnings=extraction_warnings,
        gemini_model_used=outcome.gemini_model_used,
        gemini_input_tokens=outcome.gemini_input_tokens,
        gemini_output_tokens=outcome.gemini_output_tokens,
        gemini_thought_tokens=outcome.gemini_thought_tokens,
    )

    # 5. Assemble report. Evidence images are the real uploaded files when the
    #    caller persisted them (API layer); otherwise fall back to hashing the
    #    decoded pixels, which is all a bare pipeline/CLI call has.
    report_id = report_id or str(uuid.uuid4())
    if evidence_images:
        default_roles = ["front", "back"]
        original_images = []
        for idx, img in enumerate(images):
            ih, iw = img.shape[:2]
            src = evidence_images[idx] if idx < len(evidence_images) else None
            role = src.role if src else (
                default_roles[idx] if idx < len(default_roles) else "other")
            original_images.append(OriginalImage(
                file=src.path if src else image_file,
                sha256=src.sha256 if src else _sha256_of_image(img),
                role=role, captured_at=captured_at, width=iw, height=ih,
            ))
    else:
        primary = images[0]
        h, w = primary.shape[:2]
        original_images = [OriginalImage(
            file=image_file, sha256=_sha256_of_image(primary),
            role="front", captured_at=captured_at, width=w, height=h)]

    if save_crops:
        _save_crops(declarations, marker_image, settings.uploads_dir, report_id)

    report = Report(
        report_id=report_id,
        generated_at=datetime.now(timezone.utc),
        app_version=APP_VERSION,
        rule_catalog=RuleCatalogInfo(version=catalog.version, hash=catalog.hash),
        disposition=disposition,
        inspection=inspection or Inspection(),
        product=product or Product(),
        evidence=Evidence(images=original_images),
        calibration=_to_calibration_schema(cal),
        extraction=extraction,
        summary=summary,
        declarations=declarations,
        placement=placement,
        readability=readability,
        font_analysis=font_analysis,
        legal_basis={"statute": ", ".join(
            p for p in (catalog.statute.get("section"), catalog.statute.get("act"))
            if p) or "Legal Metrology Act, 2009"},
    )
    return report
