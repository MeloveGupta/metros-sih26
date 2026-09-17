"""Tests for the Gemini vision extraction fast-path (backend/extract/gemini_reader.py).

The SDK is mocked throughout -- no network calls, no GEMINI_API_KEY needed.
See test_gemini_live.py for the one real-API test.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

import backend.extract.gemini_reader as gr
from backend.core.errors import ExtractionError
from backend.rules.catalog import load_catalog


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _fake_interaction(fields_json: dict, in_tok=100, out_tok=20, thought_tok=5):
    return SimpleNamespace(
        output_text=json.dumps(fields_json),
        usage=SimpleNamespace(total_input_tokens=in_tok, total_output_tokens=out_tok,
                              total_thought_tokens=thought_tok),
    )


class _FakeAPIError(Exception):
    """Stands in for google.genai.errors.APIError without importing the real
    exception hierarchy (keeps these tests independent of SDK internals)."""

    def __init__(self, code, status="ERROR"):
        self.code = code
        self.status = status
        self.details = "fake"
        super().__init__(f"{code} {status}")


# --- _env (empty-but-set env vars must fall back to the default) ----------

def test_env_helper_falls_back_on_empty_string(monkeypatch):
    """Regression test: a template .env's unfilled "METROS_GEMINI_MODEL="
    line sets the var to "" (present, not unset) -- plain os.environ.get
    would silently resolve to "" instead of the default, which once sent an
    empty model name straight to the live API and got a 404."""
    monkeypatch.setenv("SOME_TEST_VAR", "")
    assert gr._env("SOME_TEST_VAR", "fallback") == "fallback"
    monkeypatch.delenv("SOME_TEST_VAR", raising=False)
    assert gr._env("SOME_TEST_VAR", "fallback") == "fallback"
    monkeypatch.setenv("SOME_TEST_VAR", "actual-value")
    assert gr._env("SOME_TEST_VAR", "fallback") == "actual-value"


# --- credentials ---------------------------------------------------------

def test_gemini_available_false_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gr.gemini_available() is False


def test_gemini_available_true_with_api_key(monkeypatch):
    pytest.importorskip("google.genai")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    assert gr.gemini_available() is True


def test_client_raises_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ExtractionError):
        gr._client()


# --- _parse_response -------------------------------------------------------

def test_parse_response_success():
    interaction = _fake_interaction({"fields": [
        {"id": "mrp", "present": True, "value": "MRP Rs. 10.00 (incl. of all taxes)",
         "format_pass": None, "applicable": True},
    ]})
    fields = gr._parse_response(interaction, ["mrp", "net_quantity"])
    by_id = {f.id: f for f in fields}
    assert by_id["mrp"].present is True
    assert by_id["mrp"].value == "MRP Rs. 10.00 (incl. of all taxes)"
    # A declaration id absent from the model's response defaults to not present,
    # never a guess.
    assert by_id["net_quantity"].present is False
    assert by_id["net_quantity"].value is None


def test_parse_response_malformed_json_raises():
    interaction = SimpleNamespace(output_text="not json at all", usage=None)
    with pytest.raises(ExtractionError):
        gr._parse_response(interaction, ["mrp"])


# --- quota detection / retry / fallback ------------------------------------

def test_is_quota_error_detects_429_code():
    assert gr._is_quota_error(_FakeAPIError(429, "RESOURCE_EXHAUSTED")) is True


def test_is_quota_error_ignores_403():
    assert gr._is_quota_error(_FakeAPIError(403, "PERMISSION_DENIED")) is False


def test_create_retries_once_on_quota_then_succeeds(monkeypatch):
    monkeypatch.setattr(gr.time, "sleep", lambda *_: None)
    calls = {"n": 0}
    success = _fake_interaction({"fields": []})

    def fake_create(client, model, content):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeAPIError(429, "RESOURCE_EXHAUSTED")
        return success

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    result = gr._create(client=object(), model="m", input_content="x", allow_retry=True)
    assert result is success
    assert calls["n"] == 2


def test_create_no_retry_when_disallowed(monkeypatch):
    def fake_create(client, model, content):
        raise _FakeAPIError(429, "RESOURCE_EXHAUSTED")

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    with pytest.raises(_FakeAPIError):
        gr._create(client=object(), model="m", input_content="x", allow_retry=False)


def test_create_does_not_retry_non_quota_error(monkeypatch):
    calls = {"n": 0}

    def fake_create(client, model, content):
        calls["n"] += 1
        raise _FakeAPIError(403, "PERMISSION_DENIED")

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    with pytest.raises(_FakeAPIError):
        gr._create(client=object(), model="m", input_content="x", allow_retry=True)
    assert calls["n"] == 1  # no retry for a non-quota (auth) failure


def test_model_fallback_used_when_default_exhausts_quota(monkeypatch):
    monkeypatch.setattr(gr.time, "sleep", lambda *_: None)
    fallback_result = _fake_interaction({"fields": []})
    calls = []

    def fake_create(client, model, content):
        calls.append(model)
        if model == gr.DEFAULT_MODEL:
            raise _FakeAPIError(429, "RESOURCE_EXHAUSTED")
        return fallback_result

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    interaction, model_used = gr._call_with_model_fallback(object(), "x")
    assert model_used == gr.FALLBACK_MODEL
    assert interaction is fallback_result
    # Default model attempted twice (initial + one jittered retry), then fallback once.
    assert calls == [gr.DEFAULT_MODEL, gr.DEFAULT_MODEL, gr.FALLBACK_MODEL]


def test_model_fallback_raises_extraction_error_when_both_exhaust_quota(monkeypatch):
    monkeypatch.setattr(gr.time, "sleep", lambda *_: None)

    def fake_create(client, model, content):
        raise _FakeAPIError(429, "RESOURCE_EXHAUSTED")

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    with pytest.raises(ExtractionError, match="quota reached"):
        gr._call_with_model_fallback(object(), "x")


def test_model_fallback_raises_immediately_on_auth_failure(monkeypatch):
    """A 403 (bad key) is not quota-shaped -- no point trying the fallback
    model with the same bad key, so this must not retry or fall back."""
    calls = []

    def fake_create(client, model, content):
        calls.append(model)
        raise _FakeAPIError(403, "PERMISSION_DENIED")

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    with pytest.raises(ExtractionError):
        gr._call_with_model_fallback(object(), "x")
    assert calls == [gr.DEFAULT_MODEL]


def test_model_fallback_wraps_timeout(monkeypatch):
    def fake_create(client, model, content):
        raise TimeoutError("upstream took too long")

    monkeypatch.setattr(gr, "_interactions_create", fake_create)
    with pytest.raises(ExtractionError):
        gr._call_with_model_fallback(object(), "x")


# --- image encoding ---------------------------------------------------------

def test_encode_jpeg_stripped_downscales_and_returns_base64():
    big = np.full((3000, 2000, 3), 200, dtype=np.uint8)
    encoded = gr._encode_jpeg_stripped(big, max_side=1600)
    import base64
    raw = base64.b64decode(encoded)
    assert raw[:2] == b"\xff\xd8"  # JPEG magic bytes -- confirms real encoding
    # A 3000px-long side downscaled to <=1600 makes for a much smaller buffer
    # than encoding the original at full resolution would.
    assert len(raw) < 3000 * 2000 * 3 // 10


def test_encode_jpeg_stripped_leaves_small_image_unscaled():
    small = np.full((100, 80, 3), 50, dtype=np.uint8)
    encoded = gr._encode_jpeg_stripped(small, max_side=1600)
    assert encoded  # just needs to succeed without resizing


# --- extract_fields_from_images / extract_fields_gemini (top-level) --------

def test_extract_fields_from_images_end_to_end(monkeypatch, catalog):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    fake_client = MagicMock()
    fake_client.interactions.create.return_value = _fake_interaction({"fields": [
        {"id": "mrp", "present": True, "value": "MRP Rs. 20.00 (incl. of all taxes)",
         "format_pass": None, "applicable": True},
    ]})
    monkeypatch.setattr(gr, "_client", lambda: fake_client)

    img = np.full((50, 50, 3), 255, dtype=np.uint8)
    result = gr.extract_fields_from_images([img], catalog, ["mrp"])
    assert result.model_used == gr.DEFAULT_MODEL
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.thought_tokens == 5
    assert result.fields[0].value == "MRP Rs. 20.00 (incl. of all taxes)"
    # One request for all images together.
    assert fake_client.interactions.create.call_count == 1


def test_extract_fields_from_images_raises_without_any_images(monkeypatch, catalog):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    with pytest.raises(ExtractionError):
        gr.extract_fields_from_images([], catalog, ["mrp"])


def test_extract_fields_gemini_text_only_path(monkeypatch, catalog):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    fake_client = MagicMock()
    fake_client.interactions.create.return_value = _fake_interaction({"fields": [
        {"id": "net_quantity", "present": True, "value": "200 g",
         "format_pass": None, "applicable": True},
    ]})
    monkeypatch.setattr(gr, "_client", lambda: fake_client)
    result = gr.extract_fields_gemini("Net Qty 200 g", catalog, ["net_quantity"])
    assert result.fields[0].value == "200 g"


# --- on-disk cache -----------------------------------------------------------

def test_cache_round_trip(tmp_path, monkeypatch, catalog):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")

    fake_settings = SimpleNamespace(repo_root=tmp_path)
    monkeypatch.setattr(gr, "get_settings", lambda: fake_settings)

    fake_client = MagicMock()
    fake_client.interactions.create.return_value = _fake_interaction({"fields": [
        {"id": "mrp", "present": True, "value": "MRP Rs. 5.00 (incl. of all taxes)",
         "format_pass": None, "applicable": True},
    ]})
    monkeypatch.setattr(gr, "_client", lambda: fake_client)

    img = np.full((50, 50, 3), 255, dtype=np.uint8)
    hashes = ["deadbeef"]
    first = gr.extract_fields_from_images([img], catalog, ["mrp"], image_hashes=hashes)
    assert fake_client.interactions.create.call_count == 1
    assert (tmp_path / "data" / "gemini_cache").exists()

    # Second call with the same image_hashes must hit the cache -- no new API call.
    second = gr.extract_fields_from_images([img], catalog, ["mrp"], image_hashes=hashes)
    assert fake_client.interactions.create.call_count == 1  # unchanged
    assert second.model_used == first.model_used
    assert second.fields[0].value == first.fields[0].value


def test_cache_miss_on_different_image_hashes(tmp_path, monkeypatch, catalog):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    fake_settings = SimpleNamespace(repo_root=tmp_path)
    monkeypatch.setattr(gr, "get_settings", lambda: fake_settings)

    fake_client = MagicMock()
    fake_client.interactions.create.return_value = _fake_interaction({"fields": []})
    monkeypatch.setattr(gr, "_client", lambda: fake_client)

    img = np.full((50, 50, 3), 255, dtype=np.uint8)
    gr.extract_fields_from_images([img], catalog, ["mrp"], image_hashes=["hash-a"])
    gr.extract_fields_from_images([img], catalog, ["mrp"], image_hashes=["hash-b"])
    assert fake_client.interactions.create.call_count == 2
