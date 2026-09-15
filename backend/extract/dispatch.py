"""Choose the extraction backend: Claude (default when available) or offline regex.

`auto` uses Claude when `ANTHROPIC_API_KEY` is set and the SDK imports, and falls
back to Tesseract OCR + the deterministic regex parsers on any failure -- so a
missing key, a bad key, or a failed API call never produces a silently-empty
report. When an image is supplied, the LLM path reads the label directly via
vision (no OCR needed); otherwise it reads the provided OCR/label text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..core.errors import ExtractionError
from ..rules.catalog import RuleCatalog
from .fields import FieldExtraction, extract_fields


@dataclass
class ExtractionOutcome:
    """What extraction actually did, so the report can show how it read the label."""

    fields: List[FieldExtraction]
    used_llm: bool = False
    llm_error: Optional[str] = None
    # The text that was actually fed to the regex parsers (empty when the LLM
    # vision path succeeded, since no OCR text was needed for that).
    text_read: str = ""


def extract_declarations(
    text: str,
    catalog: RuleCatalog,
    backend: str = "regex",
    images=None,
) -> ExtractionOutcome:
    """Extract declarations using the requested backend.

    backend: "regex" (offline default), "llm" (require Claude), or "auto"
    (Claude if available, else OCR + regex). `images` (a list of BGR ndarrays,
    e.g. front + back) enables the Claude vision path.
    """
    ids = [d.id for d in catalog.declarations]
    text = text or ""

    if backend == "regex":
        return ExtractionOutcome(fields=extract_fields(text, ids), text_read=text)

    if backend in ("llm", "auto"):
        from .llm import (
            extract_fields_from_images,
            extract_fields_llm,
            llm_available,
        )
        if backend == "llm" or llm_available():
            try:
                if images:
                    fields = extract_fields_from_images(images, catalog, ids)
                else:
                    fields = extract_fields_llm(text, catalog, ids)
                return ExtractionOutcome(fields=fields, used_llm=True)
            except ExtractionError as exc:
                if backend == "llm":
                    raise
                # auto: fall back to OCR + regex rather than silently scoring an
                # empty report. If OCR was skipped upstream (the caller expected
                # the LLM vision path to read the images directly), run it now.
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
                    fields=extract_fields(fallback_text, ids),
                    used_llm=False,
                    llm_error=str(exc),
                    text_read=fallback_text,
                )
        return ExtractionOutcome(fields=extract_fields(text, ids), text_read=text)

    raise ExtractionError(f"unknown extraction backend {backend!r}")
