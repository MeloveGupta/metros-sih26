"""API tests with FastAPI TestClient over a temporary SQLite DB."""
from __future__ import annotations

import os
import tempfile

import cv2
import numpy as np
import pytest

# Point the app at an isolated DB + deterministic secret BEFORE importing it.
_DB = os.path.join(tempfile.mkdtemp(), "api_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import main as api  # noqa: E402
from backend.api.security import hash_password  # noqa: E402
from backend.db.repository import create_user  # noqa: E402


def _marker_png(marker_id=0, side_px=400, pad=120) -> bytes:
    aruco = cv2.aruco
    d = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    m = (aruco.generateImageMarker(d, marker_id, side_px)
         if hasattr(aruco, "generateImageMarker")
         else aruco.drawMarker(d, marker_id, side_px))
    canvas = np.full((side_px + 2 * pad, side_px + 3 * pad, 3), 255, np.uint8)
    canvas[pad:pad + side_px, pad:pad + side_px] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    return cv2.imencode(".png", canvas)[1].tobytes()


@pytest.fixture(scope="module")
def client():
    with api._Session() as s:
        create_user(s, email="officer@x.gov", name="Officer One", role="officer",
                    pw_hash=hash_password("pw123"))
        create_user(s, email="auditor@x.gov", name="Auditor", role="auditor",
                    pw_hash=hash_password("pw123"))
    return TestClient(api.app)


def _login(client, email, pw="pw123"):
    r = client.post("/auth/token", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_login_bad_password(client):
    r = client.post("/auth/token", data={"username": "officer@x.gov", "password": "wrong"})
    assert r.status_code == 401


def test_scan_requires_auth(client):
    r = client.post("/scan", files={"image": ("a.png", _marker_png(), "image/png")})
    assert r.status_code == 401


def test_scan_and_fetch(client):
    token = _login(client, "officer@x.gov")
    hdr = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/scan",
        headers=hdr,
        files={"image": ("chips.png", _marker_png(), "image/png")},
        data={"label_text": "MRP Rs. 45.00 (incl. of all taxes)\nNet Qty 90 g",
              "marker_mm": "40", "product_name": "Masala Chips"},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["calibration"]["verdict"] == "calibrated"
    scan_id = report["report_id"]

    got = client.get(f"/scans/{scan_id}", headers=hdr)
    assert got.status_code == 200
    assert got.json()["product"]["name"] == "Masala Chips"

    listed = client.get("/scans", headers=hdr).json()
    assert any(s["id"] == scan_id for s in listed)

    docx = client.get(f"/scans/{scan_id}/report.docx", headers=hdr)
    assert docx.status_code == 200
    assert docx.headers["content-type"].startswith(
        "application/vnd.openxmlformats")


def test_auditor_cannot_scan(client):
    token = _login(client, "auditor@x.gov")
    r = client.post("/scan", headers={"Authorization": f"Bearer {token}"},
                    files={"image": ("a.png", _marker_png(), "image/png")},
                    data={"label_text": "MRP Rs. 10"})
    assert r.status_code == 403


def test_override_requires_reason(client):
    token = _login(client, "officer@x.gov")
    hdr = {"Authorization": f"Bearer {token}"}
    scan_id = client.post(
        "/scan", headers=hdr,
        files={"image": ("a.png", _marker_png(), "image/png")},
        data={"label_text": "MRP Rs. 10", "marker_mm": "40"},
    ).json()["report_id"]

    bad = client.post(f"/scans/{scan_id}/actions", headers=hdr,
                      data={"declaration_id": "mrp", "action": "override"})
    assert bad.status_code == 400

    ok = client.post(f"/scans/{scan_id}/actions", headers=hdr,
                     data={"declaration_id": "mrp", "action": "override",
                           "reason": "verified with caliper"})
    assert ok.status_code == 200
