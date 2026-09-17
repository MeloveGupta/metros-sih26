"""Runtime configuration, sourced from environment variables.

Every setting has a working default so the pipeline runs with no environment
at all (SQLite, auth-secret placeholder, on-device OCR). Secrets (the JWT
secret) must be overridden in production via `.env` (see `.env.example`).
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
    # Reject calibration if independent re-detections of the marker disagree on
    # its corners by more than this (px). A homography fit to exactly 4 points
    # always reprojects with ~zero residual, so that is not used as a gate.
    max_corner_jitter_px: float = field(
        default_factory=lambda: _env_float("MAX_CORNER_JITTER_PX", 2.0)
    )
    # Beyond this many marker-side-lengths from the marker centre, a
    # measurement is not_assessable (homography error grows with extrapolation
    # distance and can no longer be trusted).
    max_extrapolation_sides: float = field(
        default_factory=lambda: _env_float("MAX_EXTRAPOLATION_SIDES", 4.0)
    )

    # Auth
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-insecure-secret"))
    jwt_alg: str = field(default_factory=lambda: _env("JWT_ALG", "HS256"))
    jwt_expire_minutes: int = field(
        default_factory=lambda: int(_env("JWT_EXPIRE_MINUTES", "480"))
    )
    # Bypasses auth entirely for local development ONLY -- refused in production.
    auth_disabled: bool = field(
        default_factory=lambda: _env("METROS_AUTH_DISABLED", "") == "1"
    )
    # "development" (default) | "production". Gates auth_disabled and the
    # default JWT secret so neither can silently ship to a real deployment.
    env: str = field(default_factory=lambda: _env("METROS_ENV", "development"))

    # Database / storage (used by db + api layers)
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", f"sqlite:///{REPO_ROOT / 'data' / 'metroscan.db'}"
        )
    )
    # Original uploaded images + declaration crops (evidence). Local disk for
    # the prototype; a real deployment would point this at durable storage.
    uploads_dir: Path = field(
        default_factory=lambda: REPO_ROOT / _env("UPLOADS_DIR", "data/uploads")
    )

    # Label reader: "gemini" (default, this experimental branch) uses Google
    # Gemini's free tier to read declarations straight off the photos -- see
    # backend/extract/gemini_reader.py; it needs GEMINI_API_KEY, and silently
    # falls back to "tesseract" (needs requirements-ocr.txt) whenever the key
    # is missing or a call fails, so a scan is never silently empty either
    # way. See backend/vision/ocr.py's select_ocr_engine() and
    # backend/api/main.py's /scan handler for exactly how the fallback works.
    ocr_engine: str = field(default_factory=lambda: _env("METROS_OCR_ENGINE", "gemini"))

    # Comma-separated origins allowed to call this API cross-origin (e.g. a
    # Vercel frontend calling a Render/Railway backend). Empty by default --
    # fail closed, matching this app's posture elsewhere (METROS_AUTH_DISABLED
    # etc.) -- same-origin deployments (Docker Compose's nginx) need nothing here.
    allowed_origins: str = field(default_factory=lambda: _env("ALLOWED_ORIGINS", ""))


def get_settings() -> Settings:
    """Return a fresh settings snapshot from the current environment."""
    return Settings()


def production_safety_check(settings: Settings | None = None) -> None:
    """Refuse to start in production with an auth bypass or the default,
    publicly-known JWT secret -- both are fine for local development, never
    for a real deployment."""
    settings = settings or get_settings()
    if settings.env != "production":
        return
    if settings.auth_disabled:
        raise RuntimeError(
            "METROS_AUTH_DISABLED=1 is not allowed when METROS_ENV=production."
        )
    if settings.jwt_secret == "dev-insecure-secret":
        raise RuntimeError(
            "JWT_SECRET must be set to a real secret when METROS_ENV=production "
            "(the default is publicly known)."
        )


def marker_size_mismatch_warning(settings: Settings | None = None) -> str | None:
    """A warning message if MARKER_SIZE_MM doesn't match the card generator's
    own default, else None. Every millimetre figure in a report is wrong if
    the configured marker size doesn't match what was actually printed."""
    from ..vision.card import DEFAULT_MARKER_MM  # lazy: avoid a core->vision load-order dep

    settings = settings or get_settings()
    if settings.marker_size_mm == DEFAULT_MARKER_MM:
        return None
    return (
        f"MARKER_SIZE_MM={settings.marker_size_mm:.1f} differs from the "
        f"calibration card generator's default ({DEFAULT_MARKER_MM:.1f} mm) -- "
        "make sure scripts/gen_calibration_card.py was run with a matching "
        "--marker-mm, or every millimetre measurement will be wrong."
    )
