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

# Rule 6(1)(d): "or pre-packed or imported" was omitted vide GSR 779(E)/226(E)
# w.e.f. 01-10-2022, so only a manufacturing-date cue satisfies the clause now
# -- a packing-date cue is tracked separately and flagged, not accepted.
_MFR_DATE_CUE = re.compile(
    r"(mfg|manufactured|mfd|date\s+of\s+manufacture)\b.*?"
    rf"((?:{_MONTHS})[\s./-]*\d{{2,4}}|\d{{1,2}}[/\-.]\d{{2,4}})",
    re.IGNORECASE,
)
_PACK_DATE_CUE = re.compile(
    r"(packed|pkd|packing\s+date|date\s+of\s+packing|packaging)\b.*?"
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

# Unit sale price, e.g. "Rs. 5.00 per 100g" -> "Rs. 0.05 per g" isn't required;
# officers print the per-unit price directly, e.g. "Rs. 2.25/g" or "Rs. 225 per kg".
_USP_CUE = re.compile(
    r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:/|per)\s*"
    r"(kilograms?|kgs?|grams?|gms?|g|litres?|liters?|ltrs?|l|"
    r"millilitres?|milliliters?|ml|centimet(?:res?|ers?)|cm|met(?:res?|ers?)|m|"
    r"numbers?|units?|pieces?|pcs?)\b",
    re.IGNORECASE,
)
_USP_MASS_BASE = {"g": 1, "gm": 1, "gms": 1, "gram": 1, "grams": 1,
                  "kg": 1000, "kgs": 1000, "kilogram": 1000, "kilograms": 1000}
_USP_VOLUME_BASE = {"ml": 1, "millilitre": 1, "millilitres": 1, "milliliter": 1, "milliliters": 1,
                    "l": 1000, "litre": 1000, "litres": 1000, "liter": 1000, "liters": 1000,
                    "ltr": 1000, "ltrs": 1000}
_USP_NUMBER_UNITS = {"number", "numbers", "unit", "units", "piece", "pieces", "pc", "pcs"}


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


def _mrp_amount(mrp_field: FieldExtraction) -> Optional[float]:
    if not mrp_field.present or not mrp_field.value:
        return None
    m = _MRP_AMOUNT.search(mrp_field.value)
    return float(m.group(1).replace(",", "")) if m else None


def _net_qty_base(net_qty_field: FieldExtraction) -> tuple[Optional[float], Optional[str]]:
    """(value_in_grams_or_ml_or_count, 'mass'|'volume'|'number') from a
    parsed net-quantity value, or (None, None) if it can't be classified."""
    if not net_qty_field.present or not net_qty_field.value:
        return None, None
    m = _QTY_NUM.search(net_qty_field.value)
    if not m:
        return None, None
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    if unit in _USP_MASS_BASE:
        return num * _USP_MASS_BASE[unit], "mass"
    if unit in _USP_VOLUME_BASE:
        return num * _USP_VOLUME_BASE[unit], "volume"
    if unit in ("n", "no", "nos", "u", "unit", "units", "pc", "pcs", "piece", "pieces"):
        return num, "number"
    return None, None


def validate_unit_sale_price(
    usp_value: float, usp_unit: str, mrp_amount: Optional[float],
    qty_base: Optional[float], qty_kind: Optional[str],
) -> tuple[bool, str]:
    """Rule 6(11): unit basis (per gram/kg, per ml/litre, per number) must
    match the net quantity's magnitude, and the value must be MRP / net
    quantity (converted to the declared unit), within rounding tolerance."""
    usp_unit = usp_unit.lower()
    if qty_kind == "mass":
        base_unit_size = _USP_MASS_BASE.get(usp_unit)
        if base_unit_size is None:
            return False, "unit sale price must be per gram or per kilogram for a solid"
        if qty_base < 1000 and base_unit_size != 1:
            return False, "net quantity is under 1 kg; unit sale price should be declared per gram"
        if qty_base >= 1000 and base_unit_size != 1000:
            return False, "net quantity is 1 kg or more; unit sale price should be declared per kilogram"
    elif qty_kind == "volume":
        base_unit_size = _USP_VOLUME_BASE.get(usp_unit)
        if base_unit_size is None:
            return False, "unit sale price must be per ml or per litre for a liquid"
        if qty_base < 1000 and base_unit_size != 1:
            return False, "net volume is under 1 litre; unit sale price should be declared per ml"
        if qty_base >= 1000 and base_unit_size != 1000:
            return False, "net volume is 1 litre or more; unit sale price should be declared per litre"
    elif qty_kind == "number":
        if usp_unit not in _USP_NUMBER_UNITS:
            return False, "sold by number; unit sale price should be declared per number/unit"
        base_unit_size = 1
    else:
        return True, "net quantity unknown; unit basis not checked"

    if mrp_amount is None or not qty_base:
        return True, "MRP or net quantity not available for an arithmetic cross-check"

    expected = mrp_amount / (qty_base / base_unit_size)
    if abs(expected - usp_value) > max(0.01, expected * 0.02):
        return False, f"declared Rs. {usp_value:.2f} but MRP ÷ net quantity = Rs. {expected:.2f}"
    return True, "unit basis and arithmetic check out"


def parse_unit_sale_price(text: str) -> FieldExtraction:
    net_qty = parse_net_quantity(text)
    mrp = parse_mrp(text)
    qty_base, qty_kind = _net_qty_base(net_qty)

    # Not applicable: a single item sold by number (the price already is the
    # unit price) -- an officer-review nuance, not auto-detectable beyond this.
    if qty_kind == "number" and qty_base == 1:
        return FieldExtraction(id="unit_sale_price", present=False, applicable=False)

    m = _USP_CUE.search(text)
    if not m:
        return FieldExtraction(id="unit_sale_price", present=False)

    usp_value = float(m.group(1).replace(",", ""))
    usp_unit = m.group(2).lower()
    mrp_amount = _mrp_amount(mrp)

    # Proviso: not required where the retail sale price equals the unit sale price.
    if mrp_amount is not None and abs(mrp_amount - usp_value) < 0.01:
        return FieldExtraction(id="unit_sale_price", present=True, value=m.group(0),
                               applicable=False)

    ok, detail = validate_unit_sale_price(usp_value, usp_unit, mrp_amount, qty_base, qty_kind)
    return FieldExtraction(
        id="unit_sale_price", present=True, value=m.group(0),
        format_pass=ok, format_detail=None if ok else detail,
        format_pattern="Rs. x.xx per <standard unit> (Rule 6(11))",
    )


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
    m = _MFR_DATE_CUE.search(text)
    if m:
        return FieldExtraction(id="mfg_date", present=True, value=m.group(0).strip(),
                               format_pass=True, format_pattern="month & year of manufacture")
    p = _PACK_DATE_CUE.search(text)
    if p:
        # Since 01-10-2022 (GSR 779(E)/226(E)) a packing date alone no longer
        # satisfies Rule 6(1)(d) -- it needs month & year of MANUFACTURE.
        return FieldExtraction(
            id="mfg_date", present=True, value=p.group(0).strip(), format_pass=False,
            format_detail=(
                "only a packing date found; since 01-10-2022 the rule requires month "
                "and year of manufacture; verify"
            ),
            format_pattern="month & year of manufacture",
        )
    d = _DATE_ANY.search(text)
    if d:
        # Date present but not clearly tied to a manufacture/pack cue -> flag.
        return FieldExtraction(id="mfg_date", present=True, value=d.group(0).strip(),
                               format_pass=False,
                               format_detail="date found but not clearly tied to a manufacture cue; verify",
                               format_pattern="month & year of manufacture")
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
    "unit_sale_price": parse_unit_sale_price,
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
