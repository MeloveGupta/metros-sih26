"""OCR adapters.

The pipeline consumes an `OcrResult` (full text + tokens with pixel boxes).
Tesseract (word-level boxes) is the only OCR *engine* here -- it's used
directly when `METROS_OCR_ENGINE=tesseract`, as the automatic fallback
otherwise, and always for Rule 7/8's boxes regardless of which reader
extracted the declaration text (see `pipeline.py`'s marker-token fallback).
A vision-based reader (`backend/extract/gemini_reader.py`) plugs in at the
*extraction* layer instead, not here -- it returns structured declaration
fields directly, not plain text for these regex-facing engines to feed.
`ocr_from_text` lets callers/tests supply text (and optional boxes)
directly — useful for the CLI's "paste the label text" mode and for
deterministic testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

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


def engine_available(engine: str) -> bool:
    """True if the named OCR engine ("tesseract") is installed and usable
    right now."""
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
    engine: Optional[str] = None,  # accepted for call-site stability; Tesseract is the only OCR engine now
) -> Tuple[List[OcrResult], str, Optional[str]]:
    """Choose and run an OCR engine for a scan's images.

    Returns (ocrs, backend_used, warning): `ocrs` is a list of OcrResult
    aligned 1:1 with `images` (as `run_scan()` expects); `backend_used` is
    "tesseract" / "label_text" (see schemas.report.Extraction.backend_used);
    `warning` is a human-readable string when Tesseract isn't available at
    all, else None. Never raises -- a scan never silently returns nothing.
    Callers using the Gemini vision reader instead don't call this function
    at all -- see `backend/api/main.py`'s `/scan` handler.
    """
    if label_text:
        blanks = [ocr_from_text("") for _ in images[1:]]
        return [ocr_from_text(label_text)] + blanks, "label_text", None

    if tesseract_available():
        return _tesseract_per_image(images), "tesseract", None
    return ([ocr_from_text("") for _ in images], "tesseract",
            "Tesseract OCR is not installed; no text could be read")
