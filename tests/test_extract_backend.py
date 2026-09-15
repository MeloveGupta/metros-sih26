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
    outcome = extract_declarations(LABEL, catalog, backend="regex")
    fields = {f.id: f for f in outcome.fields}
    assert fields["mrp"].present and fields["mrp"].format_pass is True
    assert fields["net_quantity"].present
    assert outcome.used_llm is False


def test_auto_falls_back_to_regex_when_llm_unavailable(catalog):
    # No anthropic SDK / credentials in the test env -> auto must still work.
    outcome = extract_declarations(LABEL, catalog, backend="auto")
    fields = {f.id: f for f in outcome.fields}
    assert fields["mrp"].present  # regex path produced results
    assert outcome.used_llm is False


def test_auto_falls_back_to_ocr_regex_when_llm_call_fails(catalog, monkeypatch):
    """A configured-but-broken LLM (e.g. a rejected key) must not silently
    score an empty report -- it must fall back to OCR + regex and say so."""
    import backend.extract.llm as llm_mod

    monkeypatch.setattr(llm_mod, "llm_available", lambda: True)

    def _boom(*a, **kw):
        raise ExtractionError("simulated LLM auth failure")

    monkeypatch.setattr(llm_mod, "extract_fields_llm", _boom)
    monkeypatch.setattr(llm_mod, "extract_fields_from_images", _boom)

    outcome = extract_declarations(LABEL, catalog, backend="auto")
    assert outcome.used_llm is False
    assert outcome.llm_error and "simulated" in outcome.llm_error
    fields = {f.id: f for f in outcome.fields}
    assert fields["mrp"].present  # regex fallback still worked on the given text


def test_llm_backend_raises_when_unavailable(catalog):
    if llm_available():
        pytest.skip("LLM is configured in this environment")
    with pytest.raises(ExtractionError):
        extract_declarations(LABEL, catalog, backend="llm")


def test_unknown_backend_raises(catalog):
    with pytest.raises(ExtractionError):
        extract_declarations(LABEL, catalog, backend="nope")
