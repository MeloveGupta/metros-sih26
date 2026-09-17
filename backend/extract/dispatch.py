"""Choose the extraction backend: Gemini vision (when requested) or offline regex.

`"gemini"` tries Gemini when `GEMINI_API_KEY` is set and the SDK imports, and
falls back to Tesseract OCR + the deterministic regex parsers on any failure --
so a missing key, a bad key, or a failed API call never produces a silently-
empty report. When images are supplied, the Gemini path reads the label
directly via vision (no OCR needed); otherwise it reads the provided OCR/label
text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..core.errors import ExtractionError
from .fields import (
    FieldExtraction,
    _ORIGIN_CUE,
    analyze_quantity_declaration,
    extract_fields,
    normalize_ws,
    validate_consumer_care,
    validate_manufacturer,
    validate_mrp,
    validate_net_quantity,
)

# Gemini only extracts; format compliance is always decided by the same
# deterministic validators the regex backend uses, run against Gemini's own
# extracted value text -- Gemini's own format_pass claim is never trusted.
_DETERMINISTIC_VALIDATORS = {
    "manufacturer": validate_manufacturer,
    "mrp": validate_mrp,
    "net_quantity": validate_net_quantity,
    "consumer_care": validate_consumer_care,
}


@dataclass
class ExtractionOutcome:
    """What extraction actually did, so the report can show how it read the label."""

    fields: List[FieldExtraction]
    used_gemini: bool = False
    gemini_model_used: Optional[str] = None
    gemini_error: Optional[str] = None
    gemini_input_tokens: Optional[int] = None
    gemini_output_tokens: Optional[int] = None
    gemini_thought_tokens: Optional[int] = None
    # The text that was actually fed to the regex parsers (empty when the
    # Gemini vision path succeeded, since no OCR text was needed for that).
    text_read: str = ""


def _reconcile_common_name(fields, text: str, hint: Optional[str]) -> None:
    """Gemini never decides compliance: its common_name value is only trusted
    if it also appears in the OCR/label text, or the officer supplied it
    directly. Otherwise it needs officer confirmation, same as the regex path.
    """
    for f in fields:
        if f.id != "common_name":
            continue
        if hint:
            found = normalize_ws(hint).lower() in normalize_ws(text).lower()
            f.present, f.value = found, (hint if found else None)
            f.needs_confirmation = False
            continue
        if f.present and f.value and normalize_ws(f.value).lower() in normalize_ws(text).lower():
            continue  # Gemini value corroborated by the OCR/label text
        f.present = False
        f.value = None
        f.needs_confirmation = True
        f.confirmation_reason = "generic name needs officer confirmation"


def _reconcile_country_of_origin(fields, text: str) -> None:
    """Rule 6(1)(aa) applies only to imported products. Code decides
    applicability and presence from the OCR/label text -- Gemini's own
    assessment is never trusted, even though the prompt also tells it not to
    infer/guess. When there's no independent text to check against (the
    vision-only path with no OCR run), a value is only trusted if one was
    actually given, never invented."""
    for f in fields:
        if f.id != "country_of_origin":
            continue
        if not text.strip():
            if not f.value:
                f.present, f.applicable = False, False
            continue
        m = _ORIGIN_CUE.search(text)
        if not m:
            f.present, f.applicable, f.value = False, False, None
            continue
        f.applicable = True
        if f.value and normalize_ws(f.value).lower() in normalize_ws(text).lower():
            f.present = True
        else:
            f.present, f.value = False, None


def _apply_deterministic_validators(fields, quantity_config: Optional[dict] = None) -> None:
    for f in fields:
        validator = _DETERMINISTIC_VALIDATORS.get(f.id)
        if validator is not None and f.present and f.value:
            ok, detail = validator(f.value)
            f.format_pass = ok
            f.format_detail = None if ok else detail
        if f.id == "net_quantity" and f.present and f.value:
            # Rules 11-13 (misleading qualifiers, "when packed", banned
            # counting words, non-SI units, unit-magnitude mismatches) apply
            # to Gemini's own value too -- it only extracts, never decides.
            notes = analyze_quantity_declaration(f.value, quantity_config)
            flags = [n for sev, n in notes if sev == "flag"]
            reviews = [n for sev, n in notes if sev == "review"]
            softs = [n for sev, n in notes if sev == "note"]
            if flags:
                f.format_pass = False
                f.format_detail = "; ".join(flags + reviews + softs)
            elif reviews:
                f.needs_confirmation = True
                f.confirmation_reason = "; ".join(reviews + softs)
            elif softs:
                f.format_detail = "; ".join(softs)


def extract_declarations(
    text: str,
    catalog,
    backend: str = "regex",
    images=None,
    image_hashes: Optional[Sequence[str]] = None,
    common_name_hint: Optional[str] = None,
) -> ExtractionOutcome:
    """Extract every declaration in `catalog`.

    backend: "regex" (offline default) or "gemini" (Gemini if available, else
    OCR + regex). `images` (a list of BGR ndarrays, e.g. front + back) enables
    the Gemini vision path; `image_hashes` lets Gemini's on-disk cache key off
    the actual photos. `common_name_hint` is the officer-supplied generic name
    (e.g. "tomato ketchup"), if given.
    """
    ids = [d.id for d in catalog.declarations]
    text = text or ""
    qty_config = catalog.quantity_declaration or None

    if backend == "regex":
        return ExtractionOutcome(
            fields=extract_fields(text, ids, common_name_hint=common_name_hint,
                                  quantity_config=qty_config),
            text_read=text,
        )

    if backend != "gemini":
        raise ExtractionError(f"unknown extraction backend {backend!r}")

    from .gemini_reader import extract_fields_from_images, extract_fields_gemini, gemini_available
    if not gemini_available():
        return ExtractionOutcome(
            fields=extract_fields(text, ids, common_name_hint=common_name_hint,
                                  quantity_config=qty_config),
            text_read=text,
        )
    try:
        if images:
            result = extract_fields_from_images(images, catalog, ids, image_hashes=image_hashes)
        else:
            result = extract_fields_gemini(text, catalog, ids)
        fields = result.fields
        _reconcile_common_name(fields, text, common_name_hint)
        _reconcile_country_of_origin(fields, text)
        _apply_deterministic_validators(fields, qty_config)
        return ExtractionOutcome(
            fields=fields, used_gemini=True, gemini_model_used=result.model_used,
            gemini_input_tokens=result.input_tokens,
            gemini_output_tokens=result.output_tokens,
            gemini_thought_tokens=result.thought_tokens,
        )
    except Exception as exc:
        # Fall back to OCR + regex rather than silently scoring an empty
        # report -- catches any failure in the Gemini call itself (normally
        # wrapped as ExtractionError) or in the reconciliation/validator
        # steps just above, not only the documented error type. If OCR was
        # skipped upstream (the caller expected the Gemini vision path to
        # read the images directly), run it now.
        fallback_text = text
        if not fallback_text.strip() and images:
            from ..vision.ocr import tesseract_available, tesseract_ocr
            if tesseract_available():
                parts = []
                for img in images:
                    try:
                        parts.append(tesseract_ocr(img).text)
                    except Exception:
                        continue
                fallback_text = "\n".join(p for p in parts if p)
        return ExtractionOutcome(
            fields=extract_fields(fallback_text, ids, common_name_hint=common_name_hint,
                                  quantity_config=qty_config),
            used_gemini=False,
            gemini_error=str(exc),
            text_read=fallback_text,
        )
