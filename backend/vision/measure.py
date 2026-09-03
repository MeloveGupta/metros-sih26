"""Metric measurements on a calibrated image (Rule 7 support).

Given the pixels->mm homography from `scale.detect_scale`, measure glyph height,
glyph width ratio, and principal-display-panel area in real units, each with an
uncertainty interval. All measurement here is *decision-support*: results feed
the rule engine, which flags POTENTIAL non-compliance for officer verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np

from ..core.errors import MeasurementError
from .scale import MARKER_PRINT_TOLERANCE_MM, px_to_mm

# Sub-pixel edge localization error assumed for a glyph boundary (px).
EDGE_LOCALIZATION_PX = 1.0


@dataclass
class MmMeasurement:
    value: float
    uncertainty: float
    unit: str = "mm"


def _relative_uncertainty(marker_mm: float, mean_side_px: float, residual_px: float,
                          span_px: float) -> float:
    """Combine independent relative error sources in quadrature.

    - marker print/cut tolerance vs. its size,
    - homography residual vs. marker side,
    - edge localization vs. the span being measured.
    """
    terms = [
        MARKER_PRINT_TOLERANCE_MM / marker_mm,
        residual_px / mean_side_px if mean_side_px else 0.0,
        EDGE_LOCALIZATION_PX / span_px if span_px else 0.0,
    ]
    return float(np.sqrt(sum(t * t for t in terms)))


def glyph_height_mm(
    H_img_to_mm: np.ndarray,
    bbox_px: Tuple[float, float, float, float],
    marker_mm: float,
    mean_side_px: float,
    residual_px: float,
) -> MmMeasurement:
    """Measure the height (mm) of a glyph/text bounding box (x, y, w, h in px)."""
    x, y, w, h = bbox_px
    if w <= 0 or h <= 0:
        raise MeasurementError(f"invalid bbox {bbox_px!r}")

    top_mid = [x + w / 2.0, y]
    bot_mid = [x + w / 2.0, y + h]
    p = px_to_mm(H_img_to_mm, np.array([top_mid, bot_mid]))
    height = float(np.linalg.norm(p[0] - p[1]))

    rel = _relative_uncertainty(marker_mm, mean_side_px, residual_px, span_px=h)
    return MmMeasurement(round(height, 3), round(height * rel, 3))


def glyph_width_ratio(
    H_img_to_mm: np.ndarray,
    bbox_px: Tuple[float, float, float, float],
) -> float:
    """Width/height ratio of a glyph box in metric space (Rule 7(3): >= 1/3)."""
    x, y, w, h = bbox_px
    if w <= 0 or h <= 0:
        raise MeasurementError(f"invalid bbox {bbox_px!r}")
    corners = px_to_mm(
        H_img_to_mm,
        np.array([[x + w / 2, y], [x + w / 2, y + h], [x, y + h / 2], [x + w, y + h / 2]]),
    )
    height_mm = float(np.linalg.norm(corners[0] - corners[1]))
    width_mm = float(np.linalg.norm(corners[2] - corners[3]))
    if height_mm <= 0:
        raise MeasurementError("degenerate glyph height")
    return round(width_mm / height_mm, 4)


def _polygon_area_mm2(points_mm: np.ndarray) -> float:
    """Shoelace area of a polygon given (N,2) mm coordinates."""
    x = points_mm[:, 0]
    y = points_mm[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def panel_area_cm2(
    H_img_to_mm: np.ndarray,
    polygon_px: Sequence[Tuple[float, float]],
    marker_mm: float,
    mean_side_px: float,
    residual_px: float,
) -> MmMeasurement:
    """Area (cm^2) of the principal display panel from its pixel polygon."""
    poly = np.asarray(polygon_px, dtype=np.float64)
    if len(poly) < 3:
        raise MeasurementError("panel polygon needs >= 3 points")
    mm_pts = px_to_mm(H_img_to_mm, poly)
    area_mm2 = _polygon_area_mm2(mm_pts)
    area_cm2 = area_mm2 / 100.0

    # Area uncertainty ~ 2x the linear relative error (area ~ length^2).
    perimeter_px = float(
        np.sum(np.linalg.norm(np.diff(np.vstack([poly, poly[0]]), axis=0), axis=1))
    )
    typical_span_px = perimeter_px / max(len(poly), 1)
    rel = 2.0 * _relative_uncertainty(marker_mm, mean_side_px, residual_px, typical_span_px)
    return MmMeasurement(round(area_cm2, 3), round(area_cm2 * rel, 3), unit="cm^2")
