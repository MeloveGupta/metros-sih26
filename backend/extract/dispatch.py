"""Extract declaration fields from OCR/label text.

Deterministic only -- no model decides a declaration's value or its format
compliance. Whatever text an OCR engine (or a pasted listing/label) supplies
goes through the same regex parsers and validators every time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .fields import extract_fields, FieldExtraction


@dataclass
class ExtractionOutcome:
    """What extraction actually did, so the report can show how it read the label."""

    fields: List[FieldExtraction]
    # The text that was actually fed to the regex parsers.
    text_read: str = ""


def extract_declarations(
    text: str,
    catalog,
    common_name_hint: Optional[str] = None,
) -> ExtractionOutcome:
    """Extract every declaration in `catalog` from `text` via the
    deterministic regex parsers (each parser validates its own format --
    see e.g. `parse_mrp`/`validate_mrp`, `parse_net_quantity`/
    `analyze_quantity_declaration` in `fields.py`). `common_name_hint` is
    the officer-supplied generic name (e.g. "tomato ketchup"), if given.
    """
    ids = [d.id for d in catalog.declarations]
    text = text or ""
    qty_config = catalog.quantity_declaration or None
    fields = extract_fields(text, ids, common_name_hint=common_name_hint,
                            quantity_config=qty_config)
    return ExtractionOutcome(fields=fields, text_read=text)
