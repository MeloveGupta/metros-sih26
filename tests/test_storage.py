"""Tests for the evidence/report storage abstraction (backend.core.storage).

Only the local-disk backend is exercised here (no live Supabase project in
CI) -- the Supabase branch is a thin, directly-inspectable REST client (see
storage.py); its selection logic (`_supabase_configured`) is covered below.
"""
from __future__ import annotations

import httpx
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


# --- regression: Supabase Storage's real-world "not found" shape ---
#
# Caught live against a real Supabase project: a missing object comes back
# as HTTP 400 (not 404) with the real status wrapped inside the JSON body,
# e.g. {"statusCode":"404","error":"not_found","code":"NoSuchKey"} for a
# missing file, or {"statusCode":"404","error":"Bucket not found",
# "code":"NoSuchBucket"} for a wrong bucket name. A literal `status_code ==
# 404` check misses both, so a routine cache-miss (a report not yet
# rendered) crashed as an unhandled 500 instead of falling through to
# "render it now" -- this is the exact bug from that incident.

def test_not_found_detected_on_plain_404():
    resp = httpx.Response(404, request=httpx.Request("GET", "https://x/y"))
    assert storage._is_not_found_response(resp) is True


def test_not_found_detected_on_400_wrapped_missing_key():
    resp = httpx.Response(
        400, json={"statusCode": "404", "error": "not_found", "code": "NoSuchKey"},
        request=httpx.Request("GET", "https://x/y"),
    )
    assert storage._is_not_found_response(resp) is True


def test_not_found_detected_on_400_wrapped_missing_bucket():
    resp = httpx.Response(
        400, json={"statusCode": "404", "error": "Bucket not found", "code": "NoSuchBucket"},
        request=httpx.Request("GET", "https://x/y"),
    )
    assert storage._is_not_found_response(resp) is True


def test_other_400_is_not_treated_as_not_found():
    resp = httpx.Response(
        400, json={"statusCode": "400", "error": "invalid_request", "code": "SomethingElse"},
        request=httpx.Request("GET", "https://x/y"),
    )
    assert storage._is_not_found_response(resp) is False
