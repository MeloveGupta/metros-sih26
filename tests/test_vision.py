"""Tests for scale recovery and metric measurement (the Rule 7 moat)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.vision.scale import MIN_CORNER_JITTER_PX, detect_scale, px_to_mm
from backend.vision.measure import (
    glyph_height_mm,
    glyph_width_ratio,
    panel_area_cm2,
)


def test_detect_scale_frontal_recovers_mm_per_pixel(scene_factory):
    img, meta = scene_factory(marker_mm=40.0, side_px=400)
    res = detect_scale(img, marker_mm=40.0)
    assert res.calibrated
    assert res.marker_id == 0
    # Frontal: mm_per_pixel should match ground truth within 1%.
    assert res.mm_per_pixel == pytest.approx(meta["mm_per_pixel"], rel=0.01)
    assert res.corner_jitter_px < 2.0
    assert 0.0 < res.detection_confidence <= 1.0


def test_detect_scale_no_marker_is_uncalibrated():
    blank = np.full((300, 300, 3), 255, np.uint8)
    res = detect_scale(blank, marker_mm=40.0)
    assert not res.calibrated
    assert res.reason and "no calibration marker" in res.reason


def test_wrong_marker_id_uncalibrated(scene_factory):
    img, _ = scene_factory(marker_id=0)
    res = detect_scale(img, marker_mm=40.0, marker_id=42)
    assert not res.calibrated
    assert "42" in res.reason


def test_glyph_height_mm_frontal(scene_factory):
    # 90 px tall glyph at 0.1 mm/px => 9.0 mm true height.
    img, meta = scene_factory(marker_mm=40.0, side_px=400, glyph=(600, 150, 40, 90))
    res = detect_scale(img, marker_mm=40.0)
    assert res.calibrated
    mean_side_px = meta["side_px"]
    m = glyph_height_mm(res.H_img_to_mm, meta["glyph_bbox_px"], res.marker_mm,
                        mean_side_px, res.corner_jitter_px)
    assert m.value == pytest.approx(meta["glyph_height_mm_true"], rel=0.02)
    assert m.uncertainty > 0  # every mm value carries a band


def test_glyph_width_ratio(scene_factory):
    img, meta = scene_factory(glyph=(600, 150, 30, 90))  # w/h = 1/3
    res = detect_scale(img, marker_mm=40.0)
    ratio = glyph_width_ratio(res.H_img_to_mm, meta["glyph_bbox_px"])
    assert ratio == pytest.approx(30 / 90, rel=0.03)


def test_panel_area_cm2(scene_factory):
    # 500x300 px panel at 0.1 mm/px => 50mm x 30mm = 1500 mm^2 = 15 cm^2.
    img, meta = scene_factory(marker_mm=40.0, side_px=400)
    res = detect_scale(img, marker_mm=40.0)
    poly = [(600, 150), (1100, 150), (1100, 450), (600, 450)]
    area = panel_area_cm2(res.H_img_to_mm, poly, res.marker_mm,
                          meta["side_px"], res.corner_jitter_px)
    assert area.unit == "cm^2"
    assert area.value == pytest.approx(15.0, rel=0.02)


def test_detect_scale_perspective_recovers_true_mm(scene_factory):
    """Warp the whole scene; the homography must still recover true mm."""
    img, meta = scene_factory(marker_mm=40.0, side_px=400, glyph=(600, 150, 40, 90))
    h, w = img.shape[:2]
    # Mild perspective warp.
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[30, 20], [w - 10, 40], [w - 40, h - 15], [15, h - 35]])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, M, (w, h), borderValue=(255, 255, 255))

    res = detect_scale(warped, marker_mm=40.0)
    assert res.calibrated

    # Map the glyph bbox corners through the same warp to find them in `warped`.
    gx, gy, gw, gh = meta["glyph_bbox_px"]
    corners = np.float32([[gx, gy], [gx + gw, gy], [gx + gw, gy + gh], [gx, gy + gh]])
    warped_corners = cv2.perspectiveTransform(corners[None], M)[0]
    xs, ys = warped_corners[:, 0], warped_corners[:, 1]
    bbox_w = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    m = glyph_height_mm(res.H_img_to_mm, bbox_w, res.marker_mm,
                        meta["side_px"], res.corner_jitter_px)
    # Perspective-correct height should still be ~9 mm (looser tol under warp),
    # and within the measurement's own reported uncertainty band.
    assert m.value == pytest.approx(meta["glyph_height_mm_true"], rel=0.08)
    true_h = meta["glyph_height_mm_true"]
    assert (m.value - m.uncertainty) <= true_h <= (m.value + m.uncertainty)


def test_corner_jitter_gate_rejects_unstable_calibration(scene_factory):
    """A homography fit to 4 points always reprojects at ~0px, so the old
    residual gate never rejected anything. The corner-jitter floor
    (`MIN_CORNER_JITTER_PX`) means any calibration can be forced to fail an
    unrealistically strict limit -- proving the gate is load-bearing again."""
    img, _ = scene_factory(marker_mm=40.0, side_px=400)
    res = detect_scale(img, marker_mm=40.0, max_corner_jitter_px=0.1)
    assert not res.calibrated
    assert res.reason and "jitter" in res.reason


def test_corner_jitter_floor_and_blur_increase_instability(scene_factory):
    """A heavily blurred/shrunk marker should show more corner instability
    than a clean, sharp one (or fail to calibrate outright)."""
    clean, _ = scene_factory(marker_mm=40.0, side_px=400)
    clean_res = detect_scale(clean, marker_mm=40.0)
    assert clean_res.calibrated
    assert clean_res.corner_jitter_px >= MIN_CORNER_JITTER_PX

    small, _ = scene_factory(marker_mm=40.0, side_px=60)
    blurred = cv2.GaussianBlur(small, (9, 9), sigmaX=3.0)
    blurred_res = detect_scale(blurred, marker_mm=40.0)
    # Either it fails to calibrate at all, or it calibrates with visibly worse
    # (or at least not-better) corner stability than the clean frontal shot.
    if blurred_res.calibrated:
        assert blurred_res.corner_jitter_px >= clean_res.corner_jitter_px
    else:
        assert blurred_res.reason
