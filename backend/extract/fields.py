"""Offline extraction of Rule 6 declarations from OCR text.

Deterministic regex/keyword parsers, no network. Each parser reports whether the
declaration was detected, the captured value, and (where the rule prescribes a
format) whether the format matches. An optional Gemini fast-path can be layered
on top later; the offline path here is always the default so the tool runs with
no API key.

Extraction never decides compliance — it only reports what was detected and
whether the format matches. The rule engine turns that into a status.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

from ..rules.engine import FieldExtraction

# --- shared patterns ---
_MONTHS = (
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
)
_PIN = re.compile(r"\b\d{6}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?91[\-\s]?)?(?:\d[\-\s]?){10,13}")

# MRP amount, e.g. "₹ 45.00", "Rs. 45", "Rs 1,299.00"
_MRP_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE
)
_MRP_CUE = re.compile(r"\b(m\.?r\.?p\.?|maximum\s+retail\s+price|max\.?\s+retail\s+price)\b",
                      re.IGNORECASE)
_MRP_INCL = re.compile(r"incl(?:usive|\.)?\s+of\s+all\s+taxes", re.IGNORECASE)

_NET_QTY = re.compile(
    r"\b(?:net\s*(?:qty|quantity|wt|weight)\s*[:\-]?\s*)?"
    r"([0-9][0-9.,]*)\s*(kg|g|gm|gms|grams?|mg|l|ltr|litres?|ml|nos?|n|pcs?|pieces?|units?|u)\b",
    re.IGNORECASE,
)

_MFG_CUE = re.compile(
    r"(mfg|manufactured|mfd|packed|pkd|packaging)\b.*?"
    rf"((?:{_MONTHS})[\s./-]*\d{{2,4}}|\d{{1,2}}[/\-.]\d{{2,4}})",
    re.IGNORECASE,
)
_DATE_ANY = re.compile(
    rf"((?:{_MONTHS})[\s./-]*\d{{2,4}}|\b\d{{1,2}}[/\-.]\d{{2,4}}\b)", re.IGNORECASE
)
_BEST_BEFORE = re.compile(r"\b(best\s+before|use\s+by|expiry|exp\.?)\b", re.IGNORECASE)
_MFR_CUE = re.compile(r"\b(manufactured\s+by|marketed\s+by|packed\s+by|mfd\.?\s+by|imported\s+by)\b",
                      re.IGNORECASE)
_CARE_CUE = re.compile(r"\b(consumer\s+care|customer\s+care|customer\s+service|helpline|for\s+complaints?)\b",
                       re.IGNORECASE)
_ORIGIN_CUE = re.compile(r"\b(country\s+of\s+origin|made\s+in|imported)\b", re.IGNORECASE)


def _first_line(text: str, start: int) -> str:
    """Return the text from `start` up to the next newline, trimmed."""
    end = text.find("\n", start)
    return text[start:(end if end != -1 else len(text))].strip()


def parse_manufacturer(text: str) -> FieldExtraction:
    m = _MFR_CUE.search(text)
    present = m is not None
    value = _first_line(text, m.start()) if m else None
    has_address = bool(_PIN.search(text))  # a PIN code strongly implies an address
    return FieldExtraction(
        id="manufacturer", present=present, value=value,
        format_pass=(has_address if present else None),
        format_pattern="name + address (PIN code expected)",
    )


def parse_common_name(text: str) -> FieldExtraction:
    # Generic name is context-dependent; we only assert detection, never absence
    # with confidence. Presence is heuristic: a non-empty first descriptive line.
    return FieldExtraction(id="common_name", present=False,
                           value=None)  # requires product context; engine -> not_detected


def parse_net_quantity(text: str) -> FieldExtraction:
    m = _NET_QTY.search(text)
    return FieldExtraction(
        id="net_quantity", present=m is not None,
        value=m.group(0).strip() if m else None,
        format_pass=(m is not None),
        format_pattern="number + standard unit (g/kg/ml/l/N)",
    )


def parse_mfg_date(text: str) -> FieldExtraction:
    m = _MFG_CUE.search(text) or None
    if m:
        return FieldExtraction(id="mfg_date", present=True, value=m.group(0).strip(),
                               format_pass=True, format_pattern="month & year")
    d = _DATE_ANY.search(text)
    if d:
        # Date present but not clearly tied to a mfg/pack cue -> flag for check.
        return FieldExtraction(id="mfg_date", present=True, value=d.group(0).strip(),
                               format_pass=False,
                               format_pattern="month & year with mfg/pack cue")
    return FieldExtraction(id="mfg_date", present=False)


def parse_best_before(text: str) -> FieldExtraction:
    m = _BEST_BEFORE.search(text)
    if not m:
        # Perishability is category-dependent; if no cue, treat as not applicable
        # rather than asserting a missing declaration.
        return FieldExtraction(id="best_before", present=False, applicable=False)
    line = _first_line(text, m.start())
    has_date = bool(_DATE_ANY.search(line))
    return FieldExtraction(id="best_before", present=True, value=line,
                           format_pass=has_date, format_pattern="best before/use by + date")


def parse_mrp(text: str) -> FieldExtraction:
    cue = _MRP_CUE.search(text)
    amount = _MRP_AMOUNT.search(text)
    present = bool(cue or amount)
    if not present:
        return FieldExtraction(id="mrp", present=False)
    value = _first_line(text, (cue or amount).start())
    # Format per Rule 6(1)(e): MRP + amount + "inclusive of all taxes".
    fmt_ok = bool(cue and amount and _MRP_INCL.search(text))
    return FieldExtraction(
        id="mrp", present=True, value=value, format_pass=fmt_ok,
        format_pattern="MRP ₹ x.xx (incl. of all taxes)",
    )


def parse_consumer_care(text: str) -> FieldExtraction:
    m = _CARE_CUE.search(text)
    if not m:
        return FieldExtraction(id="consumer_care", present=False)
    window = text[m.start(): m.start() + 200]
    has_contact = bool(_EMAIL.search(window) or _PHONE.search(window))
    return FieldExtraction(
        id="consumer_care", present=True, value=_first_line(text, m.start()),
        format_pass=has_contact, format_pattern="name/address + phone or email",
    )


def parse_country_of_origin(text: str) -> FieldExtraction:
    m = _ORIGIN_CUE.search(text)
    if not m:
        # Only mandatory for imported products; if no import cue, not applicable.
        return FieldExtraction(id="country_of_origin", present=False, applicable=False)
    return FieldExtraction(id="country_of_origin", present=True,
                           value=_first_line(text, m.start()))


_PARSERS: Dict[str, Callable[[str], FieldExtraction]] = {
    "manufacturer": parse_manufacturer,
    "common_name": parse_common_name,
    "net_quantity": parse_net_quantity,
    "mfg_date": parse_mfg_date,
    "best_before": parse_best_before,
    "mrp": parse_mrp,
    "consumer_care": parse_consumer_care,
    "country_of_origin": parse_country_of_origin,
}


def extract_fields(text: str, declaration_ids: List[str]) -> List[FieldExtraction]:
    """Extract each requested declaration from `text`.

    Unknown ids (no parser) yield a present=False extraction so the engine reports
    them as not_detected rather than crashing.
    """
    normalized = text or ""
    out: List[FieldExtraction] = []
    for decl_id in declaration_ids:
        parser: Optional[Callable[[str], FieldExtraction]] = _PARSERS.get(decl_id)
        out.append(parser(normalized) if parser else FieldExtraction(id=decl_id, present=False))
    return out
