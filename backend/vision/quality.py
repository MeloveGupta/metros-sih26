"""Readability heuristics beyond letter height (Rule 9): contrast, capture
quality (blur/glare). These are heuristics, not figures given in the Rules
themselves -- thresholds live in the catalog's `readability` block and are
documented as such wherever they're shown.
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

BBox = Tuple[int, int, int, int]


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(gray_0_255: float) -> float:
    return _srgb_to_linear(gray_0_255 / 255.0)


def measure_contrast(image: np.ndarray, bbox: BBox) -> Optional[float]:
    """WCAG-style luminance contrast ratio between the (Otsu-split) glyph
    pixels and their background within `bbox`. Higher is better; 1.0 means no
    contrast at all. Returns None if the crop is degenerate or has no split."""
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return None
    ih, iw = image.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(iw, int(x + w)), min(ih, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = image[y0:y1, x0:x1]
    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg = gray[binary == 0]
    bg = gray[binary == 255]
    if fg.size == 0 or bg.size == 0:
        return None
    l1 = _relative_luminance(float(np.mean(fg)))
    l2 = _relative_luminance(float(np.mean(bg)))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def blur_score(image: np.ndarray) -> float:
    """Variance of the Laplacian -- lower means blurrier."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def glare_fraction(image: np.ndarray, roi: Optional[BBox] = None) -> float:
    """Fraction of near-saturated (>=250) pixels -- a proxy for blown-out glare."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if roi is not None:
        x, y, w, h = roi
        ih, iw = gray.shape[:2]
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(iw, int(x + w)), min(ih, int(y + h))
        if x1 > x0 and y1 > y0:
            gray = gray[y0:y1, x0:x1]
    if gray.size == 0:
        return 0.0
    return float(np.count_nonzero(gray >= 250)) / float(gray.size)
