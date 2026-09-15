"""Deterministic regex extraction of Rule 6 declarations from OCR text.

No network. Each parser reports whether the declaration was detected, the
captured value, and (where the rule prescribes a format) whether the format
matches. This is the fallback path used when the Claude reader
(`extract.llm`) is unavailable or fails; it always runs, so the tool works
with no API key.

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

_NET_QTY_CUE = re.compile(
    r"\bnet\s*(?:qty|quantity|wt|weight|contents?|vol|volume)\b|\bn\.?w\.?\b|\bnet\b",
    re.IGNORECASE,
)
_NUTRITION_WORDS = re.compile(
    r"\b(protein|fat|carbohydrates?|energy|sugars?|sodium|per\s*serving|per\s*100)\b",
    re.IGNORECASE,
)
_QTY_NUM = re.compile(
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


def _window(text: str, start: int, max_chars: int = 200, max_lines: int = 5) -> str:
    """The declaration "block" following a cue: a few lines, capped in chars.

    Scoping format checks (PIN code, contact info, tax wording) to this window
    -- instead of the whole document -- stops an unrelated PIN/phone elsewhere
    on the label from making a different declaration look compliant.
    """
    chunk = text[start:start + max_chars]
    return "\n".join(chunk.splitlines()[:max_lines])


# --- shared, pure format validators (used by both the regex parsers below and
# the LLM-value validation path) ---

def validate_manufacturer(window: str) -> tuple[bool, str]:
    if _PIN.search(window):
        return True, "PIN code present"
    return False, "no PIN code found near the manufacturer/packer name"


def validate_mrp(window: str) -> tuple[bool, str]:
    has_amount = bool(_MRP_AMOUNT.search(window))
    has_incl = bool(_MRP_INCL.search(window))
    if has_amount and has_incl:
        return True, "amount + 'inclusive of all taxes' present"
    missing = [n for ok, n in ((has_amount, "an amount"), (has_incl, "'inclusive of all taxes'")) if not ok]
    return False, "missing near the MRP: " + " and ".join(missing)


def validate_net_quantity(window: str) -> tuple[bool, str]:
    if _NUTRITION_WORDS.search(window):
        return False, "looks like a nutrition-facts figure, not the net-quantity declaration"
    if not _QTY_NUM.search(window):
        return False, "no number + standard unit found"
    if not _NET_QTY_CUE.search(window):
        return False, "number + unit found, but no net-quantity cue (Net Qty/Net Wt/...) nearby; verify"
    return True, "net-qty cue + number + standard unit present"


def validate_consumer_care(window: str) -> tuple[bool, str]:
    has_email = bool(_EMAIL.search(window))
    has_phone = bool(_PHONE.search(window))
    residual = _CARE_CUE.sub(" ", _PHONE.sub(" ", _EMAIL.sub(" ", window)))
    has_name_address = len(re.findall(r"[A-Za-z]", residual)) >= 10
    missing = [n for ok, n in ((has_name_address, "name/address"), (has_phone, "telephone"),
                               (has_email, "e-mail")) if not ok]
    if missing:
        return False, "consumer-care block is missing: " + ", ".join(missing)
    return True, "name/address + telephone + e-mail present"


def parse_manufacturer(text: str) -> FieldExtraction:
    m = _MFR_CUE.search(text)
    if not m:
        return FieldExtraction(id="manufacturer", present=False)
    ok, detail = validate_manufacturer(_window(text, m.start()))
    return FieldExtraction(
        id="manufacturer", present=True, value=_first_line(text, m.start()),
        format_pass=ok, format_detail=None if ok else detail,
        format_pattern="name + address (PIN code expected)",
    )


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_common_name(text: str, hint: Optional[str] = None) -> FieldExtraction:
    """Look for the officer-supplied generic name in the label text.

    Without a hint, the regex backend can't reliably tell a "generic name"
    line apart from a brand name or marketing copy, so it asks for officer
    confirmation instead of guessing.
    """
    if hint:
        found = normalize_ws(hint).lower() in normalize_ws(text).lower()
        return FieldExtraction(id="common_name", present=found,
                               value=hint if found else None)
    return FieldExtraction(
        id="common_name", present=False, value=None,
        needs_confirmation=True,
        confirmation_reason="generic name needs officer confirmation",
    )


def parse_net_quantity(text: str) -> FieldExtraction:
    """Require a net-quantity cue near the number; nutrition-facts lines never
    count (e.g. "Protein 12 g per serving" is not a net-quantity declaration).
    An uncued number+unit is recorded only as a low-confidence, format-failed
    candidate, never a pass."""
    candidate: Optional[FieldExtraction] = None
    for line in text.splitlines():
        if _NUTRITION_WORDS.search(line):
            continue
        m = _QTY_NUM.search(line)
        if not m:
            continue
        if _NET_QTY_CUE.search(line):
            return FieldExtraction(
                id="net_quantity", present=True, value=m.group(0).strip(),
                format_pass=True, format_pattern="net-qty cue + number + standard unit",
            )
        if candidate is None:
            candidate = FieldExtraction(
                id="net_quantity", present=True, value=m.group(0).strip(),
                format_pass=False,
                format_detail="number + unit found, but no net-quantity cue nearby; verify",
                format_pattern="net-qty cue + number + standard unit",
            )
    if candidate is not None:
        return candidate
    return FieldExtraction(id="net_quantity", present=False)


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
    anchor = (cue or amount).start()
    value = _first_line(text, anchor)
    # Format per Rule 6(1)(e): MRP + amount + "inclusive of all taxes", scoped
    # to the MRP's own line/window (not the whole document).
    ok, detail = validate_mrp(_window(text, anchor, max_chars=150))
    fmt_ok = bool(cue and amount) and ok
    return FieldExtraction(
        id="mrp", present=True, value=value, format_pass=fmt_ok,
        format_detail=None if fmt_ok else detail,
        format_pattern="MRP ₹ x.xx (incl. of all taxes)",
    )


def parse_consumer_care(text: str) -> FieldExtraction:
    m = _CARE_CUE.search(text)
    if not m:
        return FieldExtraction(id="consumer_care", present=False)
    ok, detail = validate_consumer_care(_window(text, m.start(), max_chars=300, max_lines=5))
    return FieldExtraction(
        id="consumer_care", present=True, value=_first_line(text, m.start()),
        format_pass=ok, format_detail=None if ok else detail,
        format_pattern="name, address, telephone and e-mail (Rule 6(2))",
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
    "net_quantity": parse_net_quantity,
    "mfg_date": parse_mfg_date,
    "best_before": parse_best_before,
    "mrp": parse_mrp,
    "consumer_care": parse_consumer_care,
    "country_of_origin": parse_country_of_origin,
}


def extract_fields(text: str, declaration_ids: List[str],
                   common_name_hint: Optional[str] = None) -> List[FieldExtraction]:
    """Extract each requested declaration from `text`.

    Unknown ids (no parser) yield a present=False extraction so the engine reports
    them as not_detected rather than crashing.
    """
    normalized = text or ""
    out: List[FieldExtraction] = []
    for decl_id in declaration_ids:
        if decl_id == "common_name":
            out.append(parse_common_name(normalized, hint=common_name_hint))
            continue
        parser: Optional[Callable[[str], FieldExtraction]] = _PARSERS.get(decl_id)
        out.append(parser(normalized) if parser else FieldExtraction(id=decl_id, present=False))
    return out
