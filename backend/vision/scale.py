"""Scale recovery from a printed ArUco calibration card.

A known-size marker in the image plane gives a metric scale (mm-per-pixel) and a
homography that maps image pixels to a flat millimetre coordinate frame, which
lets us measure lengths/areas anywhere on the (planar) label in real mm — with a
reprojection residual we can turn into a measurement uncertainty.

Design guardrail: if no marker is found, or its geometry is too degraded, this
returns an *uncalibrated* result. Callers must then refuse to emit mm verdicts.
Barcode width is never used as a scale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ..core.errors import CalibrationError

# Default printed marker tolerance (mm): home/office printers + cutting.
MARKER_PRINT_TOLERANCE_MM = 0.2


@dataclass
class CalibrationResult:
    """Outcome of scale recovery."""

    found: bool
    dict_name: str
    marker_id: Optional[int] = None
    marker_mm: Optional[float] = None
    corners_px: Optional[np.ndarray] = None      # (4, 2) float32, TL,TR,BR,BL
    H_img_to_mm: Optional[np.ndarray] = None      # 3x3 homography, pixels -> mm
    mm_per_pixel: Optional[float] = None          # mean scalar (reporting only)
    residual_px: Optional[float] = None           # RMS reprojection error, px
    detection_confidence: Optional[float] = None  # 0..1 heuristic
    reason: Optional[str] = None

    @property
    def calibrated(self) -> bool:
        return self.found and self.H_img_to_mm is not None


def _resolve_dictionary(name: str):
    aruco = cv2.aruco
    const = getattr(aruco, name, None)
    if const is None:
        raise CalibrationError(f"Unknown ArUco dictionary {name!r}")
    if hasattr(aruco, "getPredefinedDictionary"):
        return aruco.getPredefinedDictionary(const)
    return aruco.Dictionary_get(const)


def _detect_markers(gray: np.ndarray, dictionary):
    aruco = cv2.aruco
    if hasattr(aruco, "ArucoDetector"):  # OpenCV >= 4.7
        params = aruco.DetectorParameters()
        detector = aruco.ArucoDetector(dictionary, params)
        return detector.detectMarkers(gray)
    params = aruco.DetectorParameters_create()
    return aruco.detectMarkers(gray, dictionary, parameters=params)


def _order_corners(c: np.ndarray) -> np.ndarray:
    """ArUco returns corners already ordered TL,TR,BR,BL; return as (4,2)."""
    return c.reshape(4, 2).astype(np.float32)


def detect_scale(
    image: np.ndarray,
    marker_mm: float,
    dict_name: str = "DICT_4X4_50",
    marker_id: Optional[int] = None,
    max_residual_px: float = 5.0,
) -> CalibrationResult:
    """Recover metric scale from a calibration marker in `image` (BGR or gray).

    Args:
        image: input image (H,W,3 BGR or H,W grayscale).
        marker_mm: physical printed side length of the marker, in mm.
        dict_name: ArUco dictionary the card was generated with.
        marker_id: if given, use only this marker id; else the first detected.
        max_residual_px: reject calibration above this RMS reprojection error.

    Returns:
        CalibrationResult. `.calibrated` is False when no usable marker was found
        (never raises for a simply-absent marker — that is a valid outcome).
    """
    if marker_mm <= 0:
        raise CalibrationError("marker_mm must be positive")

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dictionary = _resolve_dictionary(dict_name)
    corners, ids, _ = _detect_markers(gray, dictionary)

    if ids is None or len(ids) == 0:
        return CalibrationResult(False, dict_name, reason="no calibration marker detected")

    ids = ids.ravel().tolist()
    if marker_id is not None:
        if marker_id not in ids:
            return CalibrationResult(
                False, dict_name, reason=f"marker id {marker_id} not present (found {ids})"
            )
        idx = ids.index(marker_id)
    else:
        idx = 0
        marker_id = ids[0]

    src = _order_corners(corners[idx])

    # Metric destination square (mm), same corner order TL,TR,BR,BL.
    dst = np.array(
        [[0, 0], [marker_mm, 0], [marker_mm, marker_mm], [0, marker_mm]],
        dtype=np.float32,
    )

    H, _ = cv2.findHomography(src, dst, method=0)
    if H is None:
        return CalibrationResult(
            False, dict_name, marker_id=marker_id, reason="homography could not be computed"
        )

    # Reprojection residual in pixels: map the ideal mm square back to px and
    # compare with the detected corners (RMS).
    H_inv = np.linalg.inv(H)
    dst_h = np.hstack([dst, np.ones((4, 1), np.float32)])
    reproj = (H_inv @ dst_h.T).T
    reproj = reproj[:, :2] / reproj[:, 2:3]
    residual_px = float(np.sqrt(np.mean(np.sum((reproj - src) ** 2, axis=1))))

    # Mean scalar mm-per-pixel from the four side lengths (reporting only;
    # the homography carries the real perspective-correct mapping).
    side_px = [
        np.linalg.norm(src[0] - src[1]),
        np.linalg.norm(src[1] - src[2]),
        np.linalg.norm(src[2] - src[3]),
        np.linalg.norm(src[3] - src[0]),
    ]
    mean_side_px = float(np.mean(side_px))
    if mean_side_px <= 0:
        return CalibrationResult(
            False, dict_name, marker_id=marker_id, reason="degenerate marker geometry"
        )
    mm_per_pixel = marker_mm / mean_side_px

    # Confidence heuristic: penalize residual and side-length anisotropy.
    anisotropy = float(np.std(side_px) / mean_side_px)
    confidence = max(0.0, 1.0 - residual_px / max_residual_px) * max(0.0, 1.0 - anisotropy)

    result = CalibrationResult(
        found=True,
        dict_name=dict_name,
        marker_id=marker_id,
        marker_mm=marker_mm,
        corners_px=src,
        H_img_to_mm=H,
        mm_per_pixel=mm_per_pixel,
        residual_px=residual_px,
        detection_confidence=round(confidence, 4),
    )

    if residual_px > max_residual_px:
        result.found = False
        result.H_img_to_mm = None
        result.reason = (
            f"reprojection residual {residual_px:.2f}px exceeds limit {max_residual_px:.2f}px"
        )
    return result


def px_to_mm(H_img_to_mm: np.ndarray, points_px: np.ndarray) -> np.ndarray:
    """Map (N,2) pixel points to millimetre coordinates via the homography."""
    pts = np.asarray(points_px, dtype=np.float64).reshape(-1, 2)
    homog = np.hstack([pts, np.ones((len(pts), 1))])
    mapped = (H_img_to_mm @ homog.T).T
    return mapped[:, :2] / mapped[:, 2:3]
