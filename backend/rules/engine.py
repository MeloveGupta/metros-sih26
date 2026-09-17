"""Deterministic compliance engine.

Consumes extracted declarations + metric measurements and produces clause-cited
findings. It NEVER makes a final legal finding: it emits `compliant`,
`potential_non_compliance`, `not_detected`, `not_assessable`, or `not_applicable`
for officer verification. The same inputs always yield the same output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..schemas.report import (
    ClauseRef,
    DeclarationFinding,
    FontAnalysis,
    FontItem,
    FormatCheck,
    Measurement,
    PanelInput,
    Status,
    Summary,
    TableIBand,
)
from ..vision.measure import MmMeasurement
from .catalog import DeclarationRule, RuleCatalog


@dataclass
class FieldExtraction:
    """What the extraction layer found for one declaration."""

    id: str
    present: bool
    value: Optional[str] = None
    bbox: Optional[Tuple[int, int, int, int]] = None
    panel: Optional[str] = None
    ocr_confidence: Optional[float] = None
    format_pass: Optional[bool] = None
    format_pattern: Optional[str] = None
    # Specific explanation for a format failure (e.g. which parts of a
    # multi-part declaration are missing). Falls back to a generic message
    # when unset.
    format_detail: Optional[str] = None
    applicable: bool = True
    # True when detection alone isn't enough to trust the value (e.g. an
    # unconfirmed generic name -- the regex parser can't tell a "generic
    # name" line apart from a brand name without an officer-supplied hint)
    # -- forces not_assessable regardless of `present`.
    needs_confirmation: bool = False
    confirmation_reason: Optional[str] = None


@dataclass
class GlyphInput:
    declaration_id: str
    height: Optional[MmMeasurement] = None
    width_ratio: Optional[float] = None
    width_ratio_char: Optional[str] = None
    molded: bool = False


@dataclass
class FontInputs:
    panel_area_cm2: Optional[MmMeasurement] = None
    panel_input: Optional[PanelInput] = None
    items: List[GlyphInput] = field(default_factory=list)


def _clause_ref(catalog: RuleCatalog, decl_id: str) -> ClauseRef:
    rule = catalog.declaration(decl_id)
    return ClauseRef(
        clause=rule.clause,
        source_url=rule.source_url,
        gazette=rule.gazette,
        effective_from=rule.effective_from,
    )


def _declaration_status(f: FieldExtraction) -> Tuple[Status, Optional[str]]:
    if not f.applicable:
        return Status.NOT_APPLICABLE, "rule does not apply to this commodity/category"
    if f.needs_confirmation:
        return Status.NOT_ASSESSABLE, f.confirmation_reason or "needs officer confirmation"
    if not f.present:
        return (
            Status.NOT_DETECTED,
            "not detected in the submitted image (not the same as legally absent)",
        )
    if f.format_pass is False:
        return (
            Status.POTENTIAL_NON_COMPLIANCE,
            f.format_detail or "detected but does not match the prescribed format; verify",
        )
    # format_detail may still carry a soft, non-hard-flag note (e.g. a
    # non-standard unit spelling) even when the declaration otherwise passes.
    return Status.COMPLIANT, f.format_detail


def _font_status(
    height: Optional[MmMeasurement],
    threshold_mm: float,
    width_ratio: Optional[float],
    min_width_ratio: float,
    calibrated: bool,
    max_extrapolation_sides: float = 4.0,
    width_ratio_char: Optional[str] = None,
) -> Tuple[Status, Optional[str]]:
    if not calibrated or height is None:
        return Status.NOT_ASSESSABLE, "no valid calibration; millimetre height not measurable"

    if height.extrapolation_d > max_extrapolation_sides:
        return (
            Status.NOT_ASSESSABLE,
            "text too far from calibration card for a reliable measurement",
        )

    lo, hi = height.value - height.uncertainty, height.value + height.uncertainty
    if hi < threshold_mm:
        status, reason = (
            Status.POTENTIAL_NON_COMPLIANCE,
            f"measured {height.value:.2f}±{height.uncertainty:.2f}mm is below the "
            f"{threshold_mm:.1f}mm minimum; verify",
        )
    elif lo >= threshold_mm:
        status, reason = Status.COMPLIANT, None
    else:
        status, reason = (
            Status.POTENTIAL_NON_COMPLIANCE,
            f"measured {height.value:.2f}±{height.uncertainty:.2f}mm straddles the "
            f"{threshold_mm:.1f}mm minimum; physical verification needed",
        )

    return _apply_width_ratio(status, reason, width_ratio, min_width_ratio, width_ratio_char)


def _apply_width_ratio(
    status: Status, reason: Optional[str], width_ratio: Optional[float], min_width_ratio: float,
    width_ratio_char: Optional[str] = None,
) -> Tuple[Status, Optional[str]]:
    if width_ratio is not None and width_ratio < min_width_ratio:
        who = f" for '{width_ratio_char}'" if width_ratio_char else ""
        extra = f"width/height ratio {width_ratio:.2f} < {min_width_ratio:.2f}{who} (Rule 7(3))"
        reason = f"{reason}; {extra}" if reason else extra
        if status == Status.COMPLIANT:
            status = Status.POTENTIAL_NON_COMPLIANCE
    return status, reason


def _category_exemption(rule: DeclarationRule, category: Optional[str]) -> Optional[Tuple[Status, str]]:
    """A (status, note) pair if `category` exempts this declaration under
    another law, else None. Unknown/None category exempts nothing."""
    if not category:
        return None
    exemption = rule.applicability.get(category)
    if not exemption or not exemption.get("not_applicable"):
        return None
    law = exemption.get("law", "another law")
    clause = exemption.get("clause", "")
    note = f"not assessed under LMPC Rules; governed by {law}" + (f" ({clause})" if clause else "")
    return Status.NOT_APPLICABLE, note


def evaluate(
    catalog: RuleCatalog,
    fields: List[FieldExtraction],
    font: FontInputs,
    calibrated: bool,
    max_extrapolation_sides: float = 4.0,
    category: Optional[str] = None,
) -> Tuple[List[DeclarationFinding], FontAnalysis, Summary]:
    """Run the full deterministic evaluation."""
    by_id = {f.id: f for f in fields}
    findings: List[DeclarationFinding] = []

    for rule in catalog.declarations:
        f = by_id.get(rule.id)
        if f is None:
            f = FieldExtraction(id=rule.id, present=False)
        exemption = _category_exemption(rule, category)
        status, note = exemption if exemption is not None else _declaration_status(f)
        fmt = (
            FormatCheck(passed=f.format_pass, pattern=f.format_pattern)
            if f.format_pass is not None
            else None
        )
        findings.append(
            DeclarationFinding(
                id=rule.id,
                label=rule.label,
                clause_ref=_clause_ref(catalog, rule.id),
                extracted=f.value,
                bbox=f.bbox,
                panel=f.panel,
                ocr_confidence=f.ocr_confidence,
                format_check=fmt,
                status=status,
                note=note,
            )
        )

    # --- Rule 7 font analysis ---
    fa = FontAnalysis()
    min_width_ratio = float(catalog.font_absolute.get("min_width_ratio", 1 / 3))
    # Table-I band 1 (smallest panel area) has the lowest minimum of any band,
    # so it is the only floor that can be cited when the panel area isn't
    # known at all -- Rule 7(3) no longer has a separate absolute floor of
    # its own (that language was moved into Table-I by GSR 629(E)).
    band0 = catalog.font_bands[0]

    band = None
    fa.panel_input = font.panel_input
    if calibrated and font.panel_area_cm2 is not None:
        band = catalog.select_band(font.panel_area_cm2.value)
        fa.panel_area_cm2 = Measurement(
            value=font.panel_area_cm2.value,
            uncertainty=font.panel_area_cm2.uncertainty,
            unit=font.panel_area_cm2.unit,
        )
        # Locate this band's label for display.
        idx = catalog.font_bands.index(band)
        prev = catalog.font_bands[idx - 1].area_cm2_lt if idx > 0 else 0
        top = band.area_cm2_lt
        label = f"{prev}<=A" + (f"<{top}" if top is not None else "")
        fa.table_i_band = TableIBand(
            area_band=label,
            min_height_mm=band.min_height_mm,
            min_height_mm_molded=band.min_height_mm_molded,
        )

    for g in font.items:
        if band is not None:
            threshold = band.min_height_mm_molded if g.molded else band.min_height_mm
            status, reason = _font_status(
                g.height, threshold, g.width_ratio, min_width_ratio, calibrated,
                max_extrapolation_sides, g.width_ratio_char,
            )
        else:
            # No panel area known: we can only rule OUT compliance against
            # the lowest Table-I band's own minimum (text under that fails
            # every band); we can never CONFIRM compliance without knowing
            # which band actually applies.
            floor = band0.min_height_mm_molded if g.molded else band0.min_height_mm
            threshold = floor
            if not calibrated or g.height is None:
                status, reason = Status.NOT_ASSESSABLE, \
                    "no valid calibration; millimetre height not measurable"
            elif g.height.extrapolation_d > max_extrapolation_sides:
                status, reason = Status.NOT_ASSESSABLE, \
                    "text too far from calibration card for a reliable measurement"
            elif (g.height.value + g.height.uncertainty) < floor:
                status = Status.POTENTIAL_NON_COMPLIANCE
                reason = (
                    f"measured {g.height.value:.2f}±{g.height.uncertainty:.2f}mm is below "
                    f"{floor:.1f}mm, the minimum of every Table-I band (Rule 7(2), Table-I)"
                )
            else:
                status = Status.NOT_ASSESSABLE
                reason = "panel size not captured, Table-I band not selected"
            status, reason = _apply_width_ratio(status, reason, g.width_ratio, min_width_ratio,
                                               g.width_ratio_char)
        fa.items.append(
            FontItem(
                declaration_id=g.declaration_id,
                height_mm=(
                    Measurement(value=g.height.value, uncertainty=g.height.uncertainty)
                    if g.height is not None
                    else None
                ),
                width_ratio=g.width_ratio,
                width_ratio_char=g.width_ratio_char,
                threshold_mm=threshold if calibrated else None,
                molded=g.molded,
                status=status,
                reason=reason,
            )
        )

    summary = summarize(findings, fa)
    return findings, fa, summary


def summarize(findings: List[DeclarationFinding], fa: FontAnalysis) -> Summary:
    s = Summary()
    all_status = [f.status for f in findings] + [i.status for i in fa.items]
    s.checked = len(all_status)
    for st in all_status:
        if st == Status.COMPLIANT:
            s.compliant += 1
        elif st == Status.POTENTIAL_NON_COMPLIANCE:
            s.potential_non_compliance += 1
        elif st == Status.NOT_DETECTED:
            s.not_detected += 1
        elif st == Status.NOT_ASSESSABLE:
            s.not_assessable += 1
        elif st == Status.NOT_APPLICABLE:
            s.not_applicable += 1

    for f in findings:
        if f.status in (Status.POTENTIAL_NON_COMPLIANCE, Status.NOT_DETECTED):
            s.required_actions.append(f"Verify {f.label} ({f.clause_ref.clause}): {f.status.value}")
    for i in fa.items:
        if i.status == Status.POTENTIAL_NON_COMPLIANCE:
            s.required_actions.append(
                f"Verify letter height for {i.declaration_id} (Rule 7): {i.reason}"
            )

    assessable = s.checked - s.not_assessable - s.not_applicable
    s.compliance_ratio = round(s.compliant / assessable, 3) if assessable else 0.0
    return s
