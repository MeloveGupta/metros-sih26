"""Tests for OCR engine selection (backend/vision/ocr.py) and the
PaddleOCR-VL adapter (backend/vision/paddle_vl.py). Mocked -- no model
required. See test_paddle_integration.py for the one test that needs the
real model installed."""
from __future__ import annotations

import sys

import numpy as np
import pytest

import backend.vision.paddle_vl as paddle_vl
from backend.core.errors import OcrError
from backend.vision.ocr import OcrResult, select_ocr_engine


def _blank_image() -> np.ndarray:
    return np.full((10, 10, 3), 255, np.uint8)


def test_paddleocr_vl_available_false_when_paddle_not_importable(monkeypatch):
    # Setting a module to None in sys.modules makes `import paddle` raise
    # ImportError -- this forces the "not installed" path deterministically,
    # regardless of whether paddle actually happens to be installed in the
    # environment running this test.
    monkeypatch.setitem(sys.modules, "paddle", None)
    assert paddle_vl.paddleocr_vl_available() is False


def test_read_label_raises_ocr_error_when_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddle", None)
    with pytest.raises(OcrError):
        paddle_vl.read_label([_blank_image()])


def test_select_ocr_engine_prefers_label_text_over_any_engine():
    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image()], "MRP Rs. 10", engine="paddleocr_vl")
    assert backend_used == "label_text"
    assert warning is None
    assert ocrs[0].text == "MRP Rs. 10"


def test_select_ocr_engine_falls_back_to_tesseract_on_paddle_failure(monkeypatch):
    def _boom(images):
        raise OcrError("simulated failure")

    monkeypatch.setattr(paddle_vl, "read_label", _boom)
    monkeypatch.setattr("backend.vision.ocr.tesseract_available", lambda: True)
    monkeypatch.setattr("backend.vision.ocr.tesseract_ocr",
                        lambda img, lang="eng": OcrResult(text="fallback text"))

    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image()], None, engine="paddleocr_vl")
    assert backend_used == "tesseract"
    assert warning and "PaddleOCR-VL" in warning
    assert ocrs[0].text == "fallback text"


def test_select_ocr_engine_uses_paddle_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(paddle_vl, "read_label",
                        lambda images: OcrResult(text="paddle text"))

    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image(), _blank_image()], None, engine="paddleocr_vl")
    assert backend_used == "paddleocr_vl"
    assert warning is None
    assert ocrs[0].text == "paddle text"
    assert ocrs[1].text == ""  # only the combined result's slot carries text


def test_select_ocr_engine_tesseract_explicit_no_warning(monkeypatch):
    monkeypatch.setattr("backend.vision.ocr.tesseract_available", lambda: True)
    monkeypatch.setattr("backend.vision.ocr.tesseract_ocr",
                        lambda img, lang="eng": OcrResult(text="t"))

    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image()], None, engine="tesseract")
    assert backend_used == "tesseract"
    assert warning is None
    assert ocrs[0].text == "t"


def test_select_ocr_engine_no_engine_available_is_never_silent(monkeypatch):
    monkeypatch.setattr("backend.vision.ocr.tesseract_available", lambda: False)

    ocrs, backend_used, warning = select_ocr_engine(
        [_blank_image()], None, engine="tesseract")
    assert warning
    assert ocrs[0].text == ""
