"""Auth/RBAC tests: the real JWT flow (not the METROS_AUTH_DISABLED bypass
used by test_api.py). Reuses the same app/DB singleton as test_api.py -- the
env var that controls the bypass is read fresh per-request, so it can be
toggled per test regardless of import order.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.api import main as api
from backend.api.security import hash_password
from backend.db.repository import create_user, get_user_by_email

_PASSWORD = "s3cret-pass"


def _marker_png(marker_id=0, side_px=400, pad=120) -> bytes:
    aruco = cv2.aruco
    d = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    m = (aruco.generateImageMarker(d, marker_id, side_px)
         if hasattr(aruco, "generateImageMarker")
         else aruco.drawMarker(d, marker_id, side_px))
    canvas = np.full((side_px + 2 * pad, side_px + 3 * pad, 3), 255, np.uint8)
    canvas[pad:pad + side_px, pad:pad + side_px] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    return cv2.imencode(".png", canvas)[1].tobytes()


def _scan_files():
    return [("images", ("f.png", _marker_png(), "image/png")),
            ("images", ("b.png", _marker_png(), "image/png"))]


@pytest.fixture(scope="module")
def client():
    return TestClient(api.app)


@pytest.fixture(scope="module")
def users():
    """Seed one officer and one auditor for these tests (idempotent)."""
    emails = {"officer": "officer.auth-test@example.com",
             "auditor": "auditor.auth-test@example.com"}
    with api._Session() as session:
        for role, email in emails.items():
            if get_user_by_email(session, email) is None:
                create_user(session, email=email, name=role.title(), role=role,
                           pw_hash=hash_password(_PASSWORD))
    return emails


@pytest.fixture(autouse=True)
def _auth_enabled(monkeypatch):
    """These tests exercise the real auth flow, not the local-dev bypass."""
    monkeypatch.delenv("METROS_AUTH_DISABLED", raising=False)


def _token(client, email, password=_PASSWORD):
    r = client.post("/auth/token", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_login_wrong_password_is_401(client, users):
    r = client.post("/auth/token", json={"email": users["officer"], "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_email_is_401(client):
    r = client.post("/auth/token", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401


def test_scan_without_token_is_401(client):
    r = client.post("/scan", files=_scan_files(),
                    data={"marker_mm": "40", "label_text": "MRP Rs. 45.00 (incl. of all taxes)"})
    assert r.status_code == 401


def test_auditor_cannot_scan_403(client, users):
    token = _token(client, users["auditor"])
    r = client.post("/scan", files=_scan_files(),
                    data={"marker_mm": "40", "label_text": "MRP Rs. 45.00 (incl. of all taxes)"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_auditor_can_read_scans(client, users):
    token = _token(client, users["auditor"])
    r = client.get("/scans", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_officer_scan_and_finalize_succeeds(client, users):
    token = _token(client, users["officer"])
    headers = {"Authorization": f"Bearer {token}"}

    scan_resp = client.post(
        "/scan", files=_scan_files(),
        data={"marker_mm": "40", "label_text": "MRP Rs. 45.00 (incl. of all taxes)"},
        headers=headers,
    )
    assert scan_resp.status_code == 200, scan_resp.text
    report = scan_resp.json()
    # Officer identity comes from the JWT, not a free-text field.
    assert report["inspection"]["officer"]["id"]
    assert report["inspection"]["officer"]["role"] == "officer"

    finalize_resp = client.post(
        f"/scans/{report['report_id']}/finalize", json={"actions": []}, headers=headers,
    )
    assert finalize_resp.status_code == 200, finalize_resp.text


def test_auditor_cannot_finalize_403(client, users):
    officer_token = _token(client, users["officer"])
    scan_id = client.post(
        "/scan", files=_scan_files(),
        data={"marker_mm": "40", "label_text": "MRP Rs. 45.00 (incl. of all taxes)"},
        headers={"Authorization": f"Bearer {officer_token}"},
    ).json()["report_id"]

    auditor_token = _token(client, users["auditor"])
    r = client.post(f"/scans/{scan_id}/finalize", json={"actions": []},
                    headers={"Authorization": f"Bearer {auditor_token}"})
    assert r.status_code == 403


def test_non_admin_cannot_create_users(client, users):
    token = _token(client, users["officer"])
    r = client.post("/users", json={"email": "new@example.com", "password": "x", "role": "officer"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
