"""Tests for the extraction backend dispatcher (regex / llm / auto)."""
from __future__ import annotations

import pytest

from backend.core.errors import ExtractionError
from backend.extract.dispatch import extract_declarations
from backend.extract.llm import llm_available
from backend.rules.catalog import load_catalog

LABEL = "MRP Rs. 45.00 (incl. of all taxes)\nNet Qty 90 g"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_regex_backend(catalog):
    fields = {f.id: f for f in extract_declarations(LABEL, catalog, backend="regex")}
    assert fields["mrp"].present and fields["mrp"].format_pass is True
    assert fields["net_quantity"].present


def test_auto_falls_back_to_regex_when_llm_unavailable(catalog):
    # No anthropic SDK / credentials in the test env -> auto must still work.
    fields = {f.id: f for f in extract_declarations(LABEL, catalog, backend="auto")}
    assert fields["mrp"].present  # regex path produced results


def test_llm_backend_raises_when_unavailable(catalog):
    if llm_available():
        pytest.skip("LLM is configured in this environment")
    with pytest.raises(ExtractionError):
        extract_declarations(LABEL, catalog, backend="llm")


def test_unknown_backend_raises(catalog):
    with pytest.raises(ExtractionError):
        extract_declarations(LABEL, catalog, backend="nope")
