"""Character-level glyph boxes within an OCR token (Rule 7 support).

Word/line bounding boxes are wrong for Rule 7: a word is always wider than a
third of its own height (so the width-ratio check never fails), and mixes
ascenders/descenders/x-height into a single "height" that isn't any one
glyph's real cap height. This extracts individual glyph boxes by binarising
the token's crop and taking connected components, so both checks run on
actual character geometry.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

BBox = Tuple[int, int, int, int]

# Speck/noise floor: components smaller than this many pixels are dropped.
MIN_GLYPH_AREA_PX = 4


def extract_glyph_boxes(
    image: np.ndarray, token_bbox: BBox, token_text: str,
) -> List[Tuple[BBox, Optional[str]]]:
    """Connected-component glyph boxes inside a token's crop, left to right.

    Each box is paired with its best-guess source character: a positional,
    left-to-right match against the alphanumeric characters in `token_text`,
    trusted only when the component count matches the character count (an
    Otsu binarisation can merge or split glyphs, so this is a "best effort,
    or don't guess" pairing, not a real per-character OCR). Boxes are in
    absolute image pixel coordinates.
    """
    x, y, w, h = token_bbox
    if w <= 0 or h <= 0:
        return []
    ih, iw = image.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(iw, int(x + w)), min(ih, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return []

    crop = image[y0:y1, x0:x1]
    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    boxes: List[BBox] = []
    for i in range(1, n):  # label 0 is background
        bx, by, bw, bh, area = stats[i]
        if area < MIN_GLYPH_AREA_PX:
            continue  # speck, not a glyph
        boxes.append((bx, by, bw, bh))
    boxes.sort(key=lambda b: b[0])

    chars = [c for c in token_text if c.isalnum()]
    assigned: List[Optional[str]] = chars if len(chars) == len(boxes) else [None] * len(boxes)

    return [((bx + x0, by + y0, bw, bh), c) for (bx, by, bw, bh), c in zip(boxes, assigned)]
