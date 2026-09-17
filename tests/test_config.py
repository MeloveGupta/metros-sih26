"""Tests for runtime configuration checks."""
from __future__ import annotations

import pytest

from backend.core.config import Settings, marker_size_mismatch_warning, production_safety_check
from backend.vision.card import DEFAULT_MARKER_MM


def test_marker_size_matches_default_no_warning():
    settings = Settings(marker_size_mm=DEFAULT_MARKER_MM)
    assert marker_size_mismatch_warning(settings) is None


def test_marker_size_mismatch_warns():
    settings = Settings(marker_size_mm=20.0)
    msg = marker_size_mismatch_warning(settings)
    assert msg is not None
    assert "20.0" in msg and f"{DEFAULT_MARKER_MM:.1f}" in msg


# --- 3.1: production safety guards ---

def test_production_refuses_auth_disabled():
    settings = Settings(env="production", auth_disabled=True, jwt_secret="a-real-secret")
    with pytest.raises(RuntimeError, match="METROS_AUTH_DISABLED"):
        production_safety_check(settings)


def test_production_refuses_default_jwt_secret():
    settings = Settings(env="production", auth_disabled=False, jwt_secret="dev-insecure-secret")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        production_safety_check(settings)


def test_production_refuses_env_example_placeholder_jwt_secret():
    # .env.example ships JWT_SECRET=change-me -- a deployment that never
    # overrides it must be refused too, not just config.py's own default.
    settings = Settings(env="production", auth_disabled=False, jwt_secret="change-me")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        production_safety_check(settings)


def test_production_with_real_secret_and_auth_enabled_is_fine():
    settings = Settings(env="production", auth_disabled=False, jwt_secret="a-real-secret")
    production_safety_check(settings)  # must not raise


def test_development_allows_auth_disabled_and_default_secret():
    settings = Settings(env="development", auth_disabled=True, jwt_secret="dev-insecure-secret")
    production_safety_check(settings)  # must not raise
