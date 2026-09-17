"""PaddleOCR official hosted API label reader (experimental, no GPU needed).

Wraps `paddleocr.PaddleOCRClient` -- confirmed (in a fresh venv, `pip
install paddleocr` alone, no `paddlepaddle`) to import and construct with
nothing but the `paddleocr` package itself. Two hosted tasks:

- `parse_document()` (model "PaddleOCR-VL-1.6"): reads a label image
  directly and returns markdown text in reading order -- `read_label()`
  strips the markdown syntax down to plain text and feeds it to the same
  deterministic regex parsers every other OCR engine in this codebase
  feeds (no model decides a declaration's value).
- `ocr()` (model "PP-OCRv6"): classic per-region text detection. Whether
  its result carries usable coordinates for Rule 7/8 was NOT confirmed from
  the docs (the client library passes `prunedResult` through verbatim from
  the server, untyped) -- `read_boxes()` is deliberately not implemented
  until a live call with a real access token confirms the actual shape.
  Rule 7/8 keep using Tesseract on the calibrated image in the meantime,
  via the existing marker-token fallback in `backend/pipeline.py` -- same
  safe default the local PaddleOCR-VL branch used, and zero pipeline
  changes needed either way.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from ..core.config import get_settings
from ..core.errors import OcrError
from .ocr import OcrResult

_log = logging.getLogger(__name__)

_MODEL = "PaddleOCR-VL-1.6"
_REQUEST_TIMEOUT = 30.0
_POLL_TIMEOUT = 90.0

_client_singleton = None  # lazy singleton

# Message only (no secrets, no traceback) -- surfaced read-only via /health.
_last_api_error: str | None = None


def paddleocr_api_configured() -> bool:
    """True if PADDLEOCR_ACCESS_TOKEN is set. Does not validate the token."""
    return bool(os.environ.get("PADDLEOCR_ACCESS_TOKEN"))


def _record_error(message: str) -> None:
    global _last_api_error
    _last_api_error = message
    _log.warning("PaddleOCR API error: %s", message)


def last_api_error() -> str | None:
    return _last_api_error


def _cache_dir() -> Path:
    """Plain-text cache of parse_document() results by image SHA-256, so a
    re-scan of the same photo (retries, officer re-opening a draft) doesn't
    burn the 3,000-pages/model/day quota. Best-effort: a cache miss/error
    just means calling the API again, never a failure."""
    d = get_settings().uploads_dir.parent / "ocr_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_get(image_hash: str) -> Optional[str]:
    path = _cache_dir() / f"{image_hash}.txt"
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None


def _cache_set(image_hash: str, text: str) -> None:
    try:
        (_cache_dir() / f"{image_hash}.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass


def _get_client():
    global _client_singleton
    if _client_singleton is None:
        from paddleocr import PaddleOCRClient
        _client_singleton = PaddleOCRClient(
            request_timeout=_REQUEST_TIMEOUT, poll_timeout=_POLL_TIMEOUT,
        )
    return _client_singleton


def _encode_jpeg_stripped(image: np.ndarray, max_side: int = 2000) -> bytes:
    """Downscale to <= max_side on the long edge, JPEG-encode. cv2.imencode
    operates on the decoded pixel array, not the original file, so no EXIF
    (or any other metadata) from the source photo is ever present in the
    output -- nothing to explicitly strip."""
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest > max_side:
        scale = max_side / longest
        image = cv2.resize(image, (int(w * scale), int(h * scale)),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise OcrError("could not encode image for the PaddleOCR API")
    return buf.tobytes()


_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_EMPHASIS_RE = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)(.+?)\1")
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", re.MULTILINE)
_MD_TABLE_PIPE_RE = re.compile(r"\s*\|\s*")
_MD_CODE_FENCE_RE = re.compile(r"^```.*$", re.MULTILINE)
_BLANK_RUNS_RE = re.compile(r"\n{3,}")


def _markdown_to_text(markdown: str) -> str:
    """Strip markdown formatting down to plain reading-order text -- not a
    full renderer, just enough that the regex parsers see clean text
    instead of `**MRP Rs. 45.00**` or `| Net Qty | 90 g |`."""
    text = markdown or ""
    text = _MD_CODE_FENCE_RE.sub("", text)
    text = _MD_TABLE_SEP_RE.sub("", text)
    text = _MD_IMAGE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub(r"\2", text)
    text = _MD_TABLE_PIPE_RE.sub(" ", text)
    text = _BLANK_RUNS_RE.sub("\n\n", text)
    return text.strip()


def _parse_one(image: np.ndarray) -> str:
    from paddleocr import PaddleOCRAPIError, NetworkError

    jpeg_bytes = _encode_jpeg_stripped(image)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = str(Path(tmp_dir) / "label.jpg")
        Path(tmp_path).write_bytes(jpeg_bytes)

        client = _get_client()
        attempts = 0
        while True:
            attempts += 1
            try:
                t0 = time.monotonic()
                result = client.parse_document(model=_MODEL, file_path=tmp_path)
                _log.info("PaddleOCR API parse_document() took %.1fs", time.monotonic() - t0)
                break
            except NetworkError:
                if attempts >= 2:
                    raise
                continue  # one retry on network errors only
            except PaddleOCRAPIError as exc:
                raise OcrError(f"PaddleOCR API {type(exc).__name__}: {exc}") from exc

    return "\n".join(_markdown_to_text(p.markdown_text) for p in result.pages)


def read_label(
    images: List[np.ndarray], image_hashes: Optional[List[str]] = None,
) -> OcrResult:
    """Read every image with the hosted PaddleOCR-VL-1.6 task and return one
    combined OcrResult (plain text only, in reading order -- see module
    docstring re: boxes). Raises OcrError on any failure so the caller
    (select_ocr_engine) falls back to Tesseract.

    `image_hashes` (one SHA-256 hex digest per image, same order as
    `images`) enables the on-disk cache; pass None to skip caching (e.g.
    the CLI, where quota is less of a concern for a single manual scan).
    """
    if not paddleocr_api_configured():
        raise OcrError(
            "PADDLEOCR_ACCESS_TOKEN is not set -- get one at "
            "https://aistudio.baidu.com/account/accessToken and add it to .env."
        )
    hashes = image_hashes or [None] * len(images)
    try:
        parts = []
        for img, h in zip(images, hashes):
            cached = _cache_get(h) if h else None
            if cached is not None:
                parts.append(cached)
                continue
            text = _parse_one(img)
            if h:
                _cache_set(h, text)
            parts.append(text)
    except OcrError as exc:
        _record_error(str(exc))
        raise
    except Exception as exc:
        _record_error(str(exc))
        raise OcrError(f"PaddleOCR API failed: {exc}") from exc
    return OcrResult(text="\n".join(p for p in parts if p))
