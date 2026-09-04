"""Choose the extraction backend: offline regex (default) or the LLM fast-path.

`auto` uses the LLM only when it is actually usable (SDK present + an
`ant auth login` session or token), and always falls back to the deterministic
regex parsers on any failure — so the tool never hard-depends on network or
credentials.
"""
from __future__ import annotations

from typing import List

from ..core.errors import ExtractionError
from ..rules.catalog import RuleCatalog
from .fields import FieldExtraction, extract_fields


def extract_declarations(
    text: str,
    catalog: RuleCatalog,
    backend: str = "regex",
) -> List[FieldExtraction]:
    """Extract declarations using the requested backend.

    backend: "regex" (offline default), "llm" (require Claude), or "auto"
    (LLM if available, else regex).
    """
    ids = [d.id for d in catalog.declarations]

    if backend == "regex":
        return extract_fields(text, ids)

    if backend in ("llm", "auto"):
        from .llm import extract_fields_llm, llm_available
        if backend == "llm" or llm_available():
            try:
                return extract_fields_llm(text, catalog, ids)
            except ExtractionError:
                if backend == "llm":
                    raise
                # auto: fall back silently to the offline path
        return extract_fields(text, ids)

    raise ExtractionError(f"unknown extraction backend {backend!r}")
