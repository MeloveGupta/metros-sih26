"""PaddleOCR-VL-1.6 label reader (experimental, local-only).

PaddleOCR-VL is a ~1B-parameter vision-language document-parsing model. It
reads a label image directly and returns its recognized text in reading
order -- this module wraps it into the same `OcrResult` contract every other
OCR engine in this codebase uses (`backend/vision/ocr.py`), so declaration
extraction is one deterministic regex path regardless of which engine
supplied the text.

It does NOT supply usable per-line/word bounding boxes for Rule 7/8: its
documented output (`parsing_res_list[].block_bbox`) is block/paragraph-level
-- too coarse to isolate one declaration's text from another (a block can
span several declarations). Rule 7/8 keep using Tesseract for boxes on the
calibrated image via the existing marker-token fallback in
`backend/pipeline.py` (it lazily runs `tesseract_ocr()` on the calibration
image whenever the primary OCR result has no tokens) -- this module only
ever returns `tokens=[]`.
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import List

import cv2
import numpy as np

from ..core.errors import OcrError
from .ocr import OcrResult

_log = logging.getLogger(__name__)

_PIPELINE_VERSION = "v1.6"

_pipeline = None  # lazy singleton


def paddleocr_vl_available() -> bool:
    """True if paddlepaddle + paddleocr[doc-parser] import cleanly. Import
    only -- does not load the (large) model, mirroring tesseract_available()'s
    cheap-check shape in backend/vision/ocr.py."""
    try:
        import paddle  # noqa: F401 -- paddlepaddle
        from paddleocr import PaddleOCRVL  # noqa: F401
        return True
    except Exception:
        return False


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from paddleocr import PaddleOCRVL
        t0 = time.monotonic()
        _pipeline = PaddleOCRVL(pipeline_version=_PIPELINE_VERSION)
        _log.info("PaddleOCR-VL-%s model loaded in %.1fs",
                  _PIPELINE_VERSION, time.monotonic() - t0)
    return _pipeline


def _read_one(pipeline, image: np.ndarray) -> str:
    # predict() is only ever documented against a file path, not an in-memory
    # array -- writing a temp file is the doc-grounded choice rather than
    # guessing an undocumented ndarray-input API.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = str(Path(tmp_dir) / "label.png")
        cv2.imwrite(tmp_path, image)
        t0 = time.monotonic()
        results = list(pipeline.predict(tmp_path))
        _log.info("PaddleOCR-VL read one image in %.1fs", time.monotonic() - t0)

    blocks: List[str] = []
    for res in results:
        for block in res["res"].get("parsing_res_list", []):
            content = (block.get("block_content") or "").strip()
            if content:
                blocks.append(content)
    return "\n".join(blocks)


def read_label(images: List[np.ndarray]) -> OcrResult:
    """Read every image with PaddleOCR-VL and return one combined OcrResult
    (plain text only, in reading order -- see module docstring re: boxes).

    Raises OcrError if the engine is unavailable or inference fails, so
    callers can fall back to Tesseract (see select_ocr_engine() in
    backend/vision/ocr.py).
    """
    if not paddleocr_vl_available():
        raise OcrError(
            "PaddleOCR-VL is not installed. Install paddlepaddle-gpu (or "
            "paddlepaddle for CPU) and `paddleocr[doc-parser]>=3.6.0` -- see "
            "requirements-paddle.txt."
        )
    try:
        pipeline = _get_pipeline()
        parts = [_read_one(pipeline, img) for img in images]
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError(f"PaddleOCR-VL failed: {exc}") from exc
    return OcrResult(text="\n".join(p for p in parts if p))
