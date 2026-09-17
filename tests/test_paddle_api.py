"""Tests for the hosted PaddleOCR API reader (backend/vision/paddle_api.py)
and its wiring into select_ocr_engine(). Mocked throughout -- no access
token or network call needed. See test_paddle_api_integration.py for the
one test that needs a real PADDLEOCR_ACCESS_TOKEN."""
from __future__ import annotations

import io

import numpy as np
import pytest

import backend.vision.paddle_api as paddle_api
from backend.core.errors import OcrError
from backend.vision.ocr import OcrResult, select_ocr_engine


def _blank_image(h=400, w=900) -> np.ndarray:
    return np.full((h, w, 3), 255, np.uint8)


# --- helpers -----------------------------------------------------------

def test_encode_jpeg_stripped_respects_max_side_and_strips_exif():
    from PIL import Image
    out = paddle_api._encode_jpeg_stripped(_blank_image(3000, 4000), max_side=2000)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) == 2000
    assert not img.getexif()  # no EXIF from the source photo carries over


def test_markdown_to_text_strips_formatting():
    md = (
        "# Product Label\n\n**MRP Rs. 45.00** (incl. of all taxes)\n\n"
        "| Field | Value |\n|---|---|\n| Net Qty | 90 g |\n\n"
        "Consumer care: [FoodCo](https://foodco.in)\n"
    )
    text = paddle_api._markdown_to_text(md)
    assert "#" not in text and "**" not in text and "|" not in text
    assert "MRP Rs. 45.00" in text
    assert "90 g" in text
    assert "FoodCo" in text and "https://foodco.in" not in text


# --- paddleocr_api_configured() -----------------------------------------

def test_paddleocr_api_configured_reflects_env_var(monkeypatch):
    monkeypatch.delenv("PADDLEOCR_ACCESS_TOKEN", raising=False)
    assert paddle_api.paddleocr_api_configured() is False
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "tok-123")
    assert paddle_api.paddleocr_api_configured() is True


def test_read_label_raises_when_token_missing(monkeypatch):
    monkeypatch.delenv("PADDLEOCR_ACCESS_TOKEN", raising=False)
    with pytest.raises(OcrError):
        paddle_api.read_label([_blank_image()])


# --- read_label() against a mocked client -------------------------------

class _Page:
    def __init__(self, markdown_text):
        self.markdown_text = markdown_text


class _Result:
    def __init__(self, pages):
        self.pages = pages


class _FakeClient:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)  # one per call to parse_document

    def parse_document(self, **kwargs):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "tok-123")


@pytest.fixture(autouse=True)
def _reset_singleton():
    paddle_api._client_singleton = None
    yield
    paddle_api._client_singleton = None


def test_read_label_success(monkeypatch):
    monkeypatch.setattr(paddle_api, "_get_client",
                        lambda: _FakeClient([_Result([_Page("**MRP Rs. 45.00**")])]))
    result = paddle_api.read_label([_blank_image()])
    assert isinstance(result, OcrResult)
    assert "MRP Rs. 45.00" in result.text
    assert result.tokens == []  # text only, see module docstring


def test_read_label_auth_error_raises_ocr_error(monkeypatch):
    from paddleocr import AuthError
    monkeypatch.setattr(paddle_api, "_get_client",
                        lambda: _FakeClient([AuthError("bad token")]))
    with pytest.raises(OcrError):
        paddle_api.read_label([_blank_image()])
    assert "AuthError" in paddle_api.last_api_error()


def test_read_label_rate_limit_error_raises_ocr_error(monkeypatch):
    from paddleocr import RateLimitError
    monkeypatch.setattr(paddle_api, "_get_client",
                        lambda: _FakeClient([RateLimitError("quota exceeded")]))
    with pytest.raises(OcrError):
        paddle_api.read_label([_blank_image()])
    assert "RateLimitError" in paddle_api.last_api_error()


def test_read_label_request_timeout_error_raises_ocr_error(monkeypatch):
    from paddleocr import RequestTimeoutError
    monkeypatch.setattr(paddle_api, "_get_client",
                        lambda: _FakeClient([RequestTimeoutError("too slow")]))
    with pytest.raises(OcrError):
        paddle_api.read_label([_blank_image()])


def test_read_label_retries_once_on_network_error_then_succeeds(monkeypatch):
    from paddleocr import NetworkError
    monkeypatch.setattr(paddle_api, "_get_client", lambda: _FakeClient([
        NetworkError("connection reset"), _Result([_Page("Net Qty 90 g")]),
    ]))
    result = paddle_api.read_label([_blank_image()])
    assert "90 g" in result.text


def test_read_label_gives_up_after_one_retry_on_repeated_network_error(monkeypatch):
    from paddleocr import NetworkError
    monkeypatch.setattr(paddle_api, "_get_client", lambda: _FakeClient([
        NetworkError("connection reset"), NetworkError("connection reset"),
    ]))
    with pytest.raises(OcrError):
        paddle_api.read_label([_blank_image()])


def test_read_label_malformed_result_raises_ocr_error_not_silently(monkeypatch):
    class _BadPage:
        pass  # no markdown_text attribute

    monkeypatch.setattr(paddle_api, "_get_client",
                        lambda: _FakeClient([_Result([_BadPage()])]))
    with pytest.raises(OcrError):
        paddle_api.read_label([_blank_image()])


def test_read_label_uses_cache_and_skips_the_api_on_a_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(paddle_api, "get_settings",
                        lambda: type("S", (), {"uploads_dir": tmp_path / "uploads"})())
    calls = []

    def _fake_client():
        calls.append(1)
        return _FakeClient([_Result([_Page("MRP Rs. 10")])])

    monkeypatch.setattr(paddle_api, "_get_client", _fake_client)
    h = "deadbeef" * 8
    r1 = paddle_api.read_label([_blank_image()], image_hashes=[h])
    r2 = paddle_api.read_label([_blank_image()], image_hashes=[h])
    assert "MRP Rs. 10" in r1.text
    assert r2.text == r1.text
    assert len(calls) == 1  # second call served from cache, no client call


# --- select_ocr_engine() wiring ------------------------------------------

def test_select_ocr_engine_falls_back_when_token_missing(monkeypatch):
    monkeypatch.delenv("PADDLEOCR_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("backend.vision.ocr.tesseract_available", lambda: True)
    monkeypatch.setattr("backend.vision.ocr.tesseract_ocr",
                        lambda img, lang="eng": OcrResult(text="fallback"))

    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image()], None, engine="paddleocr_api")
    assert backend_used == "tesseract"
    assert warning and "PaddleOCR API" in warning
    assert ocrs[0].text == "fallback"


def test_select_ocr_engine_uses_paddleocr_api_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(paddle_api, "read_label",
                        lambda images, image_hashes=None: OcrResult(text="MRP Rs. 5"))
    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image(), _blank_image()], None, engine="paddleocr_api")
    assert backend_used == "paddleocr_api"
    assert warning is None
    assert ocrs[0].text == "MRP Rs. 5"
    assert ocrs[1].text == ""  # only the combined result's slot carries text
