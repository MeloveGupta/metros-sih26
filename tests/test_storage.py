"""Tests for the evidence/report storage abstraction (backend.core.storage).

Only the local-disk backend is exercised here (no live Supabase project in
CI) -- the Supabase branch is a thin, directly-inspectable REST client (see
storage.py); its selection logic (`_supabase_configured`) is covered below.
"""
from __future__ import annotations

import pytest

from backend.core import storage


def test_local_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_BUCKET", raising=False)

    storage.save_bytes("r1/crops/mrp.png", b"fake-png-bytes")
    assert storage.read_bytes("r1/crops/mrp.png") == b"fake-png-bytes"
    assert (tmp_path / "r1" / "crops" / "mrp.png").is_file()


def test_local_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_BUCKET", raising=False)

    with pytest.raises(FileNotFoundError):
        storage.read_bytes("nope/does-not-exist.png")


def test_supabase_selected_only_when_all_three_vars_set(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_BUCKET", raising=False)
    assert storage._supabase_configured() is False

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    monkeypatch.setenv("SUPABASE_BUCKET", "evidence")
    assert storage._supabase_configured() is True
