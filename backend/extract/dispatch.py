"""Choose the extraction backend: offline regex (default) or the Claude LLM.

`auto` uses Claude only when it is actually usable (SDK present + an
`ant auth login` session or token), and always falls back to the deterministic
regex parsers on any failure -- so the tool never hard-depends on network or
credentials. When an image is supplied, the LLM path reads the label directly
via vision (no OCR needed); otherwise it reads the provided OCR/label text.
"""
from __future__ import annotations

from typing import List, Optional

from ..core.errors import ExtractionError
from ..rules.catalog import RuleCatalog
from .fields import FieldExtraction, extract_fields


def extract_declarations(
    text: str,
    catalog: RuleCatalog,
    backend: str = "regex",
    images=None,
) -> List[FieldExtraction]:
    """Extract declarations using the requested backend.

    backend: "regex" (offline default), "llm" (require Claude), or "auto"
    (Claude if available, else regex). `images` (a list of BGR ndarrays, e.g.
    front + back) enables the Claude vision path.
    """
    ids = [d.id for d in catalog.declarations]

    if backend == "regex":
        return extract_fields(text, ids)

    if backend in ("llm", "auto"):
        from .llm import (
            extract_fields_from_images,
            extract_fields_llm,
            llm_available,
        )
        if backend == "llm" or llm_available():
            try:
                if images:
                    return extract_fields_from_images(images, catalog, ids)
                return extract_fields_llm(text, catalog, ids)
            except ExtractionError:
                if backend == "llm":
                    raise
                # auto: fall back silently to the offline path
        return extract_fields(text, ids)

    raise ExtractionError(f"unknown extraction backend {backend!r}")
