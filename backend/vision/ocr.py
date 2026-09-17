"""OCR adapters.

The pipeline consumes an `OcrResult` (full text + tokens with pixel boxes).
Two engines are supported -- the hosted PaddleOCR API (text only, see
`paddle_api.py`; no GPU or model download needed) and Tesseract (word-level
boxes, used automatically as the fallback and always for Rule 7/8's boxes
regardless of which engine read the declaration text) -- chosen by
`select_ocr_engine()` below. `ocr_from_text` lets callers/tests supply text
(and optional boxes) directly — useful for the CLI's "paste the label text"
mode and for deterministic testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from ..core.config import get_settings
from ..core.errors import OcrError

BBox = Tuple[int, int, int, int]  # x, y, w, h


@dataclass
class Token:
    text: str
    bbox: Optional[BBox] = None
    confidence: float = 1.0


@dataclass
class OcrResult:
    text: str
    tokens: List[Token] = field(default_factory=list)


def ocr_from_text(text: str, tokens: Optional[List[Token]] = None) -> OcrResult:
    """Build an OcrResult from known text (offline, no model)."""
    if tokens is None:
        tokens = [Token(text=line) for line in text.splitlines() if line.strip()]
    return OcrResult(text=text, tokens=tokens)


def tesseract_available() -> bool:
    """True if pytesseract imports and the tesseract binary is on PATH."""
    try:
        import shutil
        import pytesseract  # noqa: F401
        return shutil.which("tesseract") is not None
    except Exception:
        return False


def tesseract_ocr(image: np.ndarray, lang: str = "eng") -> OcrResult:
    """Run Tesseract over a BGR image, returning word tokens with pixel boxes.

    Offline and free. Raises OcrError if pytesseract or the binary is missing.
    """
    try:
        import cv2
        import pytesseract
        from pytesseract import Output
    except Exception as exc:
        raise OcrError(
            "Tesseract OCR is not available. Install the engine (`brew install "
            "tesseract`) and `pip install pytesseract`. Underlying error: {}".format(exc)
        ) from exc

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    try:
        data = pytesseract.image_to_data(gray, lang=lang, output_type=Output.DICT)
    except Exception as exc:
        raise OcrError(f"Tesseract failed: {exc}") from exc

    tokens: List[Token] = []
    lines: dict = {}
    n = len(data.get("text", []))
    for i in range(n):
        word = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if not word or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        tokens.append(Token(text=word, bbox=(int(x), int(y), int(w), int(h)),
                            confidence=conf / 100.0))
        key = (data.get("block_num", [0] * n)[i], data.get("par_num", [0] * n)[i],
               data.get("line_num", [0] * n)[i])
        lines.setdefault(key, []).append(word)

    text = "\n".join(" ".join(ws) for ws in lines.values())
    return OcrResult(text=text, tokens=tokens)


def paddleocr_api_available() -> bool:
    """True if PADDLEOCR_ACCESS_TOKEN is configured. Does not validate it."""
    from .paddle_api import paddleocr_api_configured
    return paddleocr_api_configured()


def engine_available(engine: str) -> bool:
    """True if the named OCR engine ("paddleocr_api" or "tesseract") is
    configured/installed and usable right now."""
    if engine == "paddleocr_api":
        return paddleocr_api_available()
    return tesseract_available()


def _tesseract_per_image(images) -> List[OcrResult]:
    ocrs = []
    for img in images:
        try:
            ocrs.append(tesseract_ocr(img))
        except OcrError:
            ocrs.append(ocr_from_text(""))
    return ocrs


def select_ocr_engine(
    images: List[np.ndarray],
    label_text: Optional[str],
    engine: Optional[str] = None,
    image_hashes: Optional[List[str]] = None,
) -> Tuple[List[OcrResult], str, Optional[str]]:
    """Choose and run an OCR engine for a scan's images.

    Returns (ocrs, backend_used, warning): `ocrs` is a list of OcrResult
    aligned 1:1 with `images` (as `run_scan()` expects); `backend_used` is
    "paddleocr_api" / "tesseract" / "label_text" (see
    schemas.report.Extraction.backend_used); `warning` is a human-readable
    string when a fallback occurred, else None. Never raises -- an engine
    that's missing or fails just falls back, so a scan never silently
    returns nothing.

    `image_hashes` (one SHA-256 hex digest per image) is forwarded to the
    hosted API's on-disk cache, if used -- see `paddle_api.py`.
    """
    if label_text:
        blanks = [ocr_from_text("") for _ in images[1:]]
        return [ocr_from_text(label_text)] + blanks, "label_text", None

    engine = engine or get_settings().ocr_engine

    if engine == "paddleocr_api":
        try:
            from .paddle_api import read_label
            combined = read_label(images, image_hashes=image_hashes)
            blanks = [ocr_from_text("") for _ in images[1:]]
            return [combined] + blanks, "paddleocr_api", None
        except Exception as exc:
            warning = f"PaddleOCR API unavailable/failed ({exc}); used Tesseract OCR instead"
            if tesseract_available():
                return _tesseract_per_image(images), "tesseract", warning
            return ([ocr_from_text("") for _ in images], "tesseract",
                    warning + "; Tesseract is not installed either, no text could be read")

    # engine == "tesseract" (explicitly chosen, not a fallback -- no warning).
    if tesseract_available():
        return _tesseract_per_image(images), "tesseract", None
    return ([ocr_from_text("") for _ in images], "tesseract",
            "Tesseract OCR is not installed; no text could be read")
