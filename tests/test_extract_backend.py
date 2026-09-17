"""Tests for the extraction dispatcher (backend/extract/dispatch.py)."""
from __future__ import annotations

import numpy as np
import pytest

from backend.extract.dispatch import extract_declarations
from backend.extract.gemini_reader import GeminiCallResult
from backend.rules.catalog import load_catalog
from backend.rules.engine import FieldExtraction

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


def test_extract_declarations_unknown_backend_raises(catalog):
    from backend.core.errors import ExtractionError
    with pytest.raises(ExtractionError):
        extract_declarations(LABEL, catalog, backend="not-a-real-backend")


def test_extract_declarations_gemini_backend_falls_back_when_unavailable(catalog, monkeypatch):
    """No GEMINI_API_KEY configured -> silently use regex, same as the
    default backend (never a silently-empty report over a missing key)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    outcome = extract_declarations(LABEL, catalog, backend="gemini")
    assert outcome.used_gemini is False
    fields = {f.id: f for f in outcome.fields}
    assert fields["mrp"].present is True


def test_extract_declarations_gemini_success_runs_deterministic_validators(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    fake_result = GeminiCallResult(
        fields=[FieldExtraction(id="mrp", present=True, value="not a real MRP format")],
        model_used="gemini-3.1-flash-lite", input_tokens=10, output_tokens=5,
    )
    import backend.extract.gemini_reader as gr
    monkeypatch.setattr(gr, "gemini_available", lambda: True)
    monkeypatch.setattr(gr, "extract_fields_from_images", lambda *a, **kw: fake_result)

    img = np.full((50, 50, 3), 255, dtype=np.uint8)
    outcome = extract_declarations("", catalog, backend="gemini", images=[img])
    assert outcome.used_gemini is True
    assert outcome.gemini_model_used == "gemini-3.1-flash-lite"
    assert outcome.gemini_input_tokens == 10
    mrp = next(f for f in outcome.fields if f.id == "mrp")
    # Gemini's own format claim is never trusted -- the deterministic
    # validator re-checked "not a real MRP format" and must fail it.
    assert mrp.format_pass is False


def test_extract_declarations_gemini_failure_falls_back_to_regex(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    import backend.extract.gemini_reader as gr
    monkeypatch.setattr(gr, "gemini_available", lambda: True)

    def boom(*a, **kw):
        raise Exception("both models exhausted their free-tier quota")

    monkeypatch.setattr(gr, "extract_fields_from_images", boom)
    img = np.full((50, 50, 3), 255, dtype=np.uint8)
    outcome = extract_declarations("", catalog, backend="gemini", images=[img])
    assert outcome.used_gemini is False
    assert "quota" in outcome.gemini_error
    # extract_fields() still ran (over empty text here) -- never an exception
    # propagating out of extract_declarations.
    assert outcome.fields
