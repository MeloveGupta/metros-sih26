"""Integration test against the real Gemini API (Google AI Studio, free tier).

Self-skips unless GEMINI_API_KEY is set (get one at
https://aistudio.google.com/apikey) -- same self-skip pattern this repo
already used for the (now removed) hosted PaddleOCR API test. Run explicitly
with:

    GEMINI_API_KEY=... pytest -m gemini_live
"""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from backend.extract.gemini_reader import gemini_available
from backend.pipeline import run_scan
from backend.vision.ocr import ocr_from_text

pytestmark = pytest.mark.gemini_live


def _front_label() -> np.ndarray:
    img = np.full((500, 1000, 3), 255, np.uint8)
    cv2.putText(img, "Tasty Chips", (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.6,
               (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "MRP Rs. 45.00 (incl. of all taxes)", (30, 200),
               cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Net Qty 90 g", (30, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
               (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Best Before: 08/2026", (30, 360), cv2.FONT_HERSHEY_SIMPLEX,
               1.0, (0, 0, 0), 3, cv2.LINE_AA)
    return img


def _back_label() -> np.ndarray:
    img = np.full((500, 1000, 3), 255, np.uint8)
    cv2.putText(img, "Manufactured by: Acme Foods Pvt Ltd,", (30, 90),
               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "123 MG Road, Pune 411001", (30, 150),
               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "Consumer Care: 1800-123-4567", (30, 250),
               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "care@acmefoods.example.com", (30, 310),
               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3, cv2.LINE_AA)
    return img


def test_gemini_reads_a_real_two_photo_scan():
    if not gemini_available():
        pytest.skip("GEMINI_API_KEY is not set")

    front, back = _front_label(), _back_label()
    t0 = time.monotonic()
    report = run_scan(
        [front, back], [ocr_from_text(""), ocr_from_text("")],
        extract_backend="gemini", label_text_provided=False,
    )
    elapsed = time.monotonic() - t0

    assert report.extraction.backend_used == "gemini", (
        f"expected the Gemini path to run; got backend_used="
        f"{report.extraction.backend_used!r}, warnings={report.extraction.warnings}"
    )
    present_ids = {d.id for d in report.declarations if d.status.value != "not_detected"}
    assert present_ids, "Gemini read no declarations at all from either photo"

    print(f"\nGemini live scan: model={report.extraction.gemini_model_used}, "
         f"elapsed={elapsed:.1f}s, "
         f"tokens(in/out/thought)="
         f"{report.extraction.gemini_input_tokens}/"
         f"{report.extraction.gemini_output_tokens}/"
         f"{report.extraction.gemini_thought_tokens}")
    for d in report.declarations:
        print(f"  {d.id}: {d.status.value}")
