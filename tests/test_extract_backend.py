"""Tests for the extraction dispatcher (backend/extract/dispatch.py)."""
from __future__ import annotations

import pytest

from backend.extract.dispatch import extract_declarations
from backend.rules.catalog import load_catalog

LABEL = "MRP Rs. 45.00 (incl. of all taxes)\nNet Qty 90 g"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_extract_declarations_uses_regex_parsers(catalog):
    outcome = extract_declarations(LABEL, catalog)
    fields = {f.id: f for f in outcome.fields}
    assert fields["mrp"].present and fields["mrp"].format_pass is True
    assert fields["net_quantity"].present
    assert outcome.text_read == LABEL


def test_extract_declarations_passes_common_name_hint(catalog):
    outcome = extract_declarations("Tasty Chips\n" + LABEL, catalog,
                                   common_name_hint="Tasty Chips")
    common_name = next(f for f in outcome.fields if f.id == "common_name")
    assert common_name.present is True
    assert common_name.value == "Tasty Chips"
    assert common_name.needs_confirmation is False
