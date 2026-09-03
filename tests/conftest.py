"""Shared test fixtures + path setup so `backend` imports from the repo root."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _marker_image(dict_name: str, marker_id: int, side_px: int) -> np.ndarray:
    aruco = cv2.aruco
    const = getattr(aruco, dict_name)
    dictionary = (aruco.getPredefinedDictionary(const)
                  if hasattr(aruco, "getPredefinedDictionary")
                  else aruco.Dictionary_get(const))
    if hasattr(aruco, "generateImageMarker"):
        return aruco.generateImageMarker(dictionary, marker_id, side_px)
    img = np.zeros((side_px, side_px), np.uint8)
    return aruco.drawMarker(dictionary, marker_id, side_px, img, 1)


@pytest.fixture
def scene_factory():
    """Build a frontal BGR scene: white canvas + marker, plus optional glyph box.

    Returns (image, meta) where meta has mm_per_pixel, marker_mm, and any glyph
    truth. Frontal => mm_per_pixel is exact (pure scale, no perspective).
    """

    def _make(marker_mm=40.0, side_px=400, pad=120, dict_name="DICT_4X4_50",
              marker_id=0, glyph=None):
        canvas = np.full((side_px + 2 * pad, side_px + 3 * pad, 3), 255, np.uint8)
        marker = cv2.cvtColor(_marker_image(dict_name, marker_id, side_px),
                              cv2.COLOR_GRAY2BGR)
        mx, my = pad, pad
        canvas[my:my + side_px, mx:mx + side_px] = marker
        meta = {
            "marker_mm": marker_mm,
            "side_px": side_px,
            "mm_per_pixel": marker_mm / side_px,
            "dict_name": dict_name,
            "marker_id": marker_id,
        }
        if glyph is not None:
            gx, gy, gw, gh = glyph  # px bbox to the right of the marker
            cv2.rectangle(canvas, (gx, gy), (gx + gw, gy + gh), (0, 0, 0), -1)
            meta["glyph_bbox_px"] = (gx, gy, gw, gh)
            meta["glyph_height_mm_true"] = gh * meta["mm_per_pixel"]
            meta["glyph_width_mm_true"] = gw * meta["mm_per_pixel"]
        return canvas, meta

    return _make
