"""OCR adapter.

The pipeline consumes an `OcrResult` (full text + tokens with pixel boxes).
The default backend is PaddleOCR (offline, per-character/line boxes); it is
imported lazily so the rest of the system runs without it. `ocr_from_text` lets
callers/tests supply text (and optional boxes) directly — useful for the CLI's
"paste the label text" mode and for deterministic testing.
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


def paddle_ocr(image: np.ndarray, lang: str = "en") -> OcrResult:
    """Run PaddleOCR over a BGR image. Raises OcrError if PaddleOCR is absent."""
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # ImportError or backend load failure
        raise OcrError(
            "PaddleOCR is not installed. Install `paddleocr` + `paddlepaddle` "
            "(see requirements.txt), or use ocr_from_text() to supply label text. "
            f"Underlying error: {exc}"
        ) from exc

    engine = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    raw = engine.ocr(image, cls=True)
    tokens: List[Token] = []
    lines: List[str] = []
    for page in raw or []:
        for entry in page or []:
            box, (txt, conf) = entry
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            bbox = (int(min(xs)), int(min(ys)),
                    int(max(xs) - min(xs)), int(max(ys) - min(ys)))
            tokens.append(Token(text=txt, bbox=bbox, confidence=float(conf)))
            lines.append(txt)
    return OcrResult(text="\n".join(lines), tokens=tokens)
