"""Integration test against the real, hosted PaddleOCR API.

Self-skips unless PADDLEOCR_ACCESS_TOKEN is set (get one at
https://aistudio.baidu.com/account/accessToken) -- same self-skip pattern
this repo already uses for Tesseract-gated tests. Run explicitly with:

    PADDLEOCR_ACCESS_TOKEN=... pytest -m paddle_api
"""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from backend.vision.ocr import select_ocr_engine
from backend.vision.paddle_api import paddleocr_api_configured

pytestmark = pytest.mark.paddle_api


def _label_image() -> np.ndarray:
    img = np.full((400, 900, 3), 255, np.uint8)
    cv2.putText(img, "MRP Rs. 45.00 incl. of all taxes", (20, 80),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Net Qty 200 g", (20, 160),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    return img


def test_paddleocr_api_reads_a_real_label():
    if not paddleocr_api_configured():
        pytest.skip("PADDLEOCR_ACCESS_TOKEN is not set")

    t0 = time.monotonic()
    ocrs, backend_used, warning = select_ocr_engine(
        [_label_image()], None, engine="paddleocr_api")
    elapsed = time.monotonic() - t0

    assert backend_used == "paddleocr_api"
    assert warning is None
    assert ocrs[0].text.strip(), "PaddleOCR API returned no text at all"
    print(f"\nPaddleOCR API: parse_document() for 1 image took {elapsed:.1f}s"
         f"\ntext read:\n{ocrs[0].text}")
