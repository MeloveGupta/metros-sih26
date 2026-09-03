"""Runtime configuration, sourced from environment variables.

Offline-first: every setting has a working default so the pipeline runs with no
environment at all. Secrets (JWT, storage creds) must be overridden in
production via `.env` (see `.env.example`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = two levels up from this file (backend/core/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:  # never silently swallow a bad config value
        raise ValueError(f"Environment variable {name}={raw!r} is not a float") from exc


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot."""

    # Paths
    repo_root: Path = REPO_ROOT
    rule_catalog_path: Path = field(
        default_factory=lambda: REPO_ROOT / _env("RULE_CATALOG", "rules/lmpc-2011.yaml")
    )
    report_template_dir: Path = field(
        default_factory=lambda: REPO_ROOT / "backend" / "reports" / "templates"
    )

    # Calibration
    marker_size_mm: float = field(default_factory=lambda: _env_float("MARKER_SIZE_MM", 40.0))
    # Reject calibration if the homography reprojection residual exceeds this (px).
    max_homography_residual_px: float = field(
        default_factory=lambda: _env_float("MAX_HOMOGRAPHY_RESIDUAL_PX", 5.0)
    )

    # Auth
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-insecure-secret"))
    jwt_alg: str = field(default_factory=lambda: _env("JWT_ALG", "HS256"))
    jwt_expire_minutes: int = field(
        default_factory=lambda: int(_env("JWT_EXPIRE_MINUTES", "480"))
    )

    # Database / storage (used by db + api layers)
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "sqlite:///./metroscan.db")
    )

    # Optional LLM fast-path; empty => fully offline extraction.
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))


def get_settings() -> Settings:
    """Return a fresh settings snapshot from the current environment."""
    return Settings()
