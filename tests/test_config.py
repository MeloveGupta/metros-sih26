"""Tests for runtime configuration checks."""
from __future__ import annotations

from backend.core.config import Settings, marker_size_mismatch_warning
from backend.vision.card import DEFAULT_MARKER_MM


def test_marker_size_matches_default_no_warning():
    settings = Settings(marker_size_mm=DEFAULT_MARKER_MM)
    assert marker_size_mismatch_warning(settings) is None


def test_marker_size_mismatch_warns():
    settings = Settings(marker_size_mm=20.0)
    msg = marker_size_mismatch_warning(settings)
    assert msg is not None
    assert "20.0" in msg and f"{DEFAULT_MARKER_MM:.1f}" in msg
