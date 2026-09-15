"""Scale recovery from a printed ArUco calibration card.

A known-size marker in the image plane gives a metric scale (mm-per-pixel) and a
homography that maps image pixels to a flat millimetre coordinate frame, which
lets us measure lengths/areas anywhere on the (planar) label in real mm. A
homography fit to exactly 4 corners always reprojects with ~zero residual, so
that number is not a real quality signal; instead we re-detect the marker
independently (raw / CLAHE-enhanced / upscaled) and use the largest corner
displacement across those detections (`corner_jitter_px`) as the calibration
quality measure feeding the measurement uncertainty.

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
    corner_jitter_px: Optional[float] = None      # max corner displacement across
                                                   # independent re-detections, px
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


def _make_params():
    """Detector params tuned for real phone photos of a printed card.

    Wider adaptive-threshold window range (marker size varies a lot in a
    product+card shot), lower min perimeter (catch a small marker), sub-pixel
    corner refinement (accurate mm), tolerant polygon approximation.
    """
    aruco = cv2.aruco
    params = aruco.DetectorParameters() if hasattr(aruco, "ArucoDetector") \
        else aruco.DetectorParameters_create()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 53
    params.adaptiveThreshWinSizeStep = 8
    params.minMarkerPerimeterRate = 0.01     # allow a smaller marker in frame
    params.maxMarkerPerimeterRate = 4.0
    params.polygonalApproxAccuracyRate = 0.06
    params.minCornerDistanceRate = 0.03
    try:
        params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
        params.cornerRefinementWinSize = 5
    except Exception:
        pass
    return params


def _detect_once(gray: np.ndarray, dictionary):
    aruco = cv2.aruco
    params = _make_params()
    if hasattr(aruco, "ArucoDetector"):  # OpenCV >= 4.7
        return aruco.ArucoDetector(dictionary, params).detectMarkers(gray)
    return aruco.detectMarkers(gray, dictionary, parameters=params)


def _detect_markers(gray: np.ndarray, dictionary):
    """Detect markers, retrying with contrast enhancement / upscale if needed."""
    corners, ids, rej = _detect_once(gray, dictionary)
    if ids is not None and len(ids):
        return corners, ids, rej

    # Retry 1: CLAHE contrast boost (helps glare / uneven lighting).
    try:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        corners, ids, rej = _detect_once(clahe.apply(gray), dictionary)
        if ids is not None and len(ids):
            return corners, ids, rej
    except Exception:
        pass

    # Retry 2: upscale small images (marker too few pixels to decode).
    h, w = gray.shape[:2]
    if max(h, w) < 2200:
        scale = 2200 / max(h, w)
        up = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        c2, ids2, rej2 = _detect_once(up, dictionary)
        if ids2 is not None and len(ids2):
            for c in c2:  # map corners back to original resolution
                c /= scale
            return c2, ids2, rej2

    return corners, ids, rej


def _order_corners(c: np.ndarray) -> np.ndarray:
    """ArUco returns corners already ordered TL,TR,BR,BL; return as (4,2)."""
    return c.reshape(4, 2).astype(np.float32)


def _extract_id_corners(corners, ids, marker_id: int) -> Optional[np.ndarray]:
    if ids is None or len(ids) == 0:
        return None
    ids_list = ids.ravel().tolist()
    if marker_id not in ids_list:
        return None
    return _order_corners(corners[ids_list.index(marker_id)])


def _independent_detections(gray: np.ndarray, dictionary, marker_id: int) -> list:
    """Detect `marker_id` independently on raw / CLAHE / 1.5x-upscaled variants.

    Each variant is attempted regardless of whether the others succeed, so the
    spread between their corner estimates is a real (not zero-by-construction)
    signal of how stable the detection is.
    """
    found: list = []

    c, ids, _ = _detect_once(gray, dictionary)
    raw = _extract_id_corners(c, ids, marker_id)
    if raw is not None:
        found.append(raw)

    try:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        c2, ids2, _ = _detect_once(clahe.apply(gray), dictionary)
        enhanced = _extract_id_corners(c2, ids2, marker_id)
        if enhanced is not None:
            found.append(enhanced)
    except Exception:
        pass

    try:
        scale = 1.5
        h, w = gray.shape[:2]
        up = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        c3, ids3, _ = _detect_once(up, dictionary)
        upscaled = _extract_id_corners(c3, ids3, marker_id)
        if upscaled is not None:
            found.append(upscaled / scale)
    except Exception:
        pass

    return found


# A homography reprojection error is not a real corner-jitter floor: even a
# perfect detection has sub-pixel quantization noise, so never report below this.
MIN_CORNER_JITTER_PX = 0.5


def _corner_jitter_px(variant_corners: list) -> float:
    """Max single-corner displacement (px) across independent detections."""
    if len(variant_corners) < 2:
        return MIN_CORNER_JITTER_PX
    base = variant_corners[0]
    max_disp = 0.0
    for other in variant_corners[1:]:
        d = np.linalg.norm(base - other, axis=1)
        max_disp = max(max_disp, float(np.max(d)))
    return max(MIN_CORNER_JITTER_PX, max_disp)


def detect_scale(
    image: np.ndarray,
    marker_mm: float,
    dict_name: str = "DICT_4X4_50",
    marker_id: Optional[int] = None,
    max_corner_jitter_px: float = 2.0,
) -> CalibrationResult:
    """Recover metric scale from a calibration marker in `image` (BGR or gray).

    Args:
        image: input image (H,W,3 BGR or H,W grayscale).
        marker_mm: physical printed side length of the marker, in mm.
        dict_name: ArUco dictionary the card was generated with.
        marker_id: if given, use only this marker id; else the first detected.
        max_corner_jitter_px: reject calibration above this corner instability.

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

    ids_list = ids.ravel().tolist()
    if marker_id is not None:
        if marker_id not in ids_list:
            return CalibrationResult(
                False, dict_name, reason=f"marker id {marker_id} not present (found {ids_list})"
            )
        idx = ids_list.index(marker_id)
    else:
        idx = 0
        marker_id = ids_list[0]

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

    # Corner stability: re-detect independently (raw/CLAHE/upscale) and take
    # the largest single-corner displacement as the calibration quality signal.
    variants = [src] + _independent_detections(gray, dictionary, marker_id)
    corner_jitter_px = _corner_jitter_px(variants)

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

    # Confidence heuristic: penalize corner jitter and side-length anisotropy.
    anisotropy = float(np.std(side_px) / mean_side_px)
    confidence = (max(0.0, 1.0 - corner_jitter_px / max_corner_jitter_px)
                 * max(0.0, 1.0 - anisotropy))

    result = CalibrationResult(
        found=True,
        dict_name=dict_name,
        marker_id=marker_id,
        marker_mm=marker_mm,
        corners_px=src,
        H_img_to_mm=H,
        mm_per_pixel=mm_per_pixel,
        corner_jitter_px=corner_jitter_px,
        detection_confidence=round(confidence, 4),
    )

    if corner_jitter_px > max_corner_jitter_px:
        result.found = False
        result.H_img_to_mm = None
        result.reason = (
            f"corner detection unstable ({corner_jitter_px:.2f}px jitter) "
            f"exceeds limit {max_corner_jitter_px:.2f}px"
        )
    return result


def px_to_mm(H_img_to_mm: np.ndarray, points_px: np.ndarray) -> np.ndarray:
    """Map (N,2) pixel points to millimetre coordinates via the homography."""
    pts = np.asarray(points_px, dtype=np.float64).reshape(-1, 2)
    homog = np.hstack([pts, np.ones((len(pts), 1))])
    mapped = (H_img_to_mm @ homog.T).T
    return mapped[:, :2] / mapped[:, 2:3]
