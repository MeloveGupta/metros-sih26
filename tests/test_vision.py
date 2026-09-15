"""Tests for scale recovery and metric measurement (the Rule 7 moat)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.vision.scale import MIN_CORNER_JITTER_PX, detect_scale, px_to_mm
from backend.vision.measure import (
    glyph_height_mm,
    glyph_height_mm_boxes,
    glyph_width_ratio,
    glyph_width_ratio_boxes,
    panel_area_cm2,
)
from backend.vision.glyphs import extract_glyph_boxes
from backend.vision.quality import blur_score, glare_fraction, measure_contrast


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


# --- 2.8: character-level glyph boxes for width ratio and height ---

def _synthetic_glyph_row(image, x0, y0, glyph_w, glyph_h, count=3, gap=14):
    """Draw `count` solid black rectangles side by side (standing in for
    glyphs of an exact known width x height) and return the enclosing bbox."""
    for i in range(count):
        x = x0 + i * (glyph_w + gap)
        cv2.rectangle(image, (x, y0), (x + glyph_w, y0 + glyph_h), (0, 0, 0), -1)
    total_w = count * glyph_w + (count - 1) * gap
    return (x0, y0, total_w, glyph_h)


def test_condensed_glyphs_flagged_by_per_glyph_width_ratio(scene_factory):
    """A WORD bbox is always wider than 1/3 its height, so the old
    whole-token measurement could never fail the width-ratio check. Individual
    glyph boxes (each narrower than the token) can."""
    img, meta = scene_factory(marker_mm=40.0, side_px=400, pad=250)
    # Condensed: each glyph is only 20% as wide as it is tall (< 1/3). Placed
    # below the calibration card's own footprint (see backend.vision.card) so
    # the pixels are actually drawn within the (larger) canvas.
    bbox = _synthetic_glyph_row(img, 600, 780, glyph_w=18, glyph_h=90, count=3)
    res = detect_scale(img, marker_mm=40.0)
    assert res.calibrated

    glyph_boxes = extract_glyph_boxes(img, bbox, "245")
    assert len(glyph_boxes) == 3
    result = glyph_width_ratio_boxes(res.H_img_to_mm, glyph_boxes, exempt_chars={"1", "i", "I", "l"})
    assert result is not None
    ratio, _char = result
    assert ratio < 1 / 3

    # The whole-token bbox measurement would NOT have caught this (3 glyphs +
    # 2 gaps make the token far wider than 1/3 its height).
    assert glyph_width_ratio(res.H_img_to_mm, bbox) > 1 / 3


def test_normal_glyphs_pass_per_glyph_width_ratio(scene_factory):
    img, meta = scene_factory(marker_mm=40.0, side_px=400, pad=250)
    # Normal: each glyph is 45% as wide as it is tall (> 1/3).
    bbox = _synthetic_glyph_row(img, 600, 780, glyph_w=40, glyph_h=90, count=3)
    res = detect_scale(img, marker_mm=40.0)
    assert res.calibrated

    glyph_boxes = extract_glyph_boxes(img, bbox, "245")
    result = glyph_width_ratio_boxes(res.H_img_to_mm, glyph_boxes, exempt_chars={"1", "i", "I", "l"})
    assert result is not None
    ratio, _char = result
    assert ratio >= 1 / 3


def test_glyph_boxes_exempt_characters_are_skipped():
    """A narrow '1' or 'I' must not fail the width-ratio check."""
    # Build glyph boxes directly: a narrow "1" (exempt) and a normal "2".
    glyph_boxes = [((0, 0, 10, 90), "1"), ((20, 0, 40, 90), "2")]
    H = np.eye(3)  # identity homography: pixel coords == mm coords for this test
    result = glyph_width_ratio_boxes(H, glyph_boxes, exempt_chars={"1", "i", "I", "l"})
    assert result is not None
    ratio, char = result
    assert char == "2"  # the narrow "1" was skipped, not the smallest ratio


def test_glyph_height_from_boxes_uses_median(scene_factory):
    img, meta = scene_factory(marker_mm=40.0, side_px=400, pad=250)
    bbox = _synthetic_glyph_row(img, 600, 780, glyph_w=40, glyph_h=90, count=3)
    res = detect_scale(img, marker_mm=40.0)
    glyph_boxes = extract_glyph_boxes(img, bbox, "245")
    m = glyph_height_mm_boxes(res.H_img_to_mm, glyph_boxes, res.marker_mm,
                              meta["side_px"], res.corner_jitter_px)
    assert m is not None
    # 90px tall at 0.1mm/px => 9.0mm, same as the whole-token measurement here
    # since all three synthetic glyphs are the same height.
    assert m.value == pytest.approx(9.0, rel=0.05)


# --- 2.10: readability beyond letter height (Rule 9) ---

def test_measure_contrast_high_for_black_on_white():
    img = np.full((100, 100, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (80, 80), (0, 0, 0), -1)
    ratio = measure_contrast(img, (0, 0, 100, 100))
    assert ratio is not None
    assert ratio > 10.0  # black-on-white is near-maximal WCAG contrast (21:1)


def test_measure_contrast_low_for_light_gray_on_white():
    img = np.full((100, 100, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (80, 80), (210, 210, 210), -1)
    ratio = measure_contrast(img, (0, 0, 100, 100))
    assert ratio is not None
    assert ratio < 2.0  # light grey on white is barely distinguishable


def test_blur_score_lower_for_blurred_image():
    sharp = np.full((200, 200, 3), 255, np.uint8)
    for i in range(0, 200, 10):
        cv2.line(sharp, (i, 0), (i, 200), (0, 0, 0), 1)
    blurred = cv2.GaussianBlur(sharp, (15, 15), sigmaX=6.0)
    assert blur_score(blurred) < blur_score(sharp)


def test_glare_fraction_detects_saturated_patch():
    img = np.full((100, 100, 3), 128, np.uint8)
    clean = glare_fraction(img)
    cv2.rectangle(img, (10, 10), (90, 90), (255, 255, 255), -1)
    glare = glare_fraction(img)
    assert glare > clean
    assert glare > 0.5
