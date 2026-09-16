"""Integration test against the real PaddleOCR-VL-1.6 model.

Self-skips unless the model is actually installed (see
requirements-paddle.txt) -- same pattern this repo already uses for
Tesseract-gated tests (`if not tesseract_available(): pytest.skip(...)`).
Run explicitly with: pytest -m paddle

KNOWN ISSUE (verified on the RTX 4070 setup this branch was built against):
`pipeline.predict()` did not return within 5 minutes for a single small
(900x400px, two lines of text) synthetic label image -- model construction
itself was fast (~2s, cached weights), but the actual inference call hung
or was extremely slow, confirmed via an isolated diagnostic script (not
just this test) with a hard `timeout 300` that fired. This test has no
timeout of its own, so running it on hardware with the same issue will hang
indefinitely -- if you hit this, Ctrl-C and see the README's "Experimental
branch" section, known limitations.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from backend.vision.ocr import select_ocr_engine
from backend.vision.paddle_vl import paddleocr_vl_available

pytestmark = pytest.mark.paddle


def _label_image() -> np.ndarray:
    img = np.full((400, 900, 3), 255, np.uint8)
    cv2.putText(img, "MRP Rs. 45.00 incl. of all taxes", (20, 80),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Net Qty 200 g", (20, 160),
               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    return img


def test_paddleocr_vl_reads_a_real_label():
    if not paddleocr_vl_available():
        pytest.skip("PaddleOCR-VL is not installed (see requirements-paddle.txt)")

    t0 = time.monotonic()
    ocrs, backend_used, warning = select_ocr_engine(
        [_label_image()], None, engine="paddleocr_vl")
    elapsed = time.monotonic() - t0

    assert backend_used == "paddleocr_vl"
    assert warning is None
    assert ocrs[0].text.strip(), "PaddleOCR-VL returned no text at all"
    print(f"\nPaddleOCR-VL: read 1 image in {elapsed:.1f}s "
         f"(includes model load on first call)\ntext read:\n{ocrs[0].text}")
