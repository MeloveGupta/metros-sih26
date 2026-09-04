"""API tests (prototype: no auth) with FastAPI TestClient over a temp SQLite DB."""
from __future__ import annotations

import os
import tempfile

import cv2
import numpy as np
import pytest

# Point the app at an isolated DB BEFORE importing it.
_DB = os.path.join(tempfile.mkdtemp(), "api_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import main as api  # noqa: E402


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
    return TestClient(api.app)


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_scan_no_auth_and_fetch(client):
    r = client.post(
        "/scan",
        files={"images": ("chips.png", _marker_png(), "image/png")},
        data={"label_text": "MRP Rs. 45.00 (incl. of all taxes)\nNet Qty 90 g",
              "marker_mm": "40", "product_name": "Masala Chips"},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["calibration"]["verdict"] == "calibrated"
    scan_id = report["report_id"]

    got = client.get(f"/scans/{scan_id}")
    assert got.status_code == 200
    assert got.json()["product"]["name"] == "Masala Chips"

    listed = client.get("/scans").json()
    assert any(s["id"] == scan_id for s in listed)

    docx = client.get(f"/scans/{scan_id}/report.docx")
    assert docx.status_code == 200
    assert docx.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_scan_without_label_text_still_works(client):
    r = client.post(
        "/scan",
        files={"images": ("p.png", _marker_png(), "image/png")},
        data={"marker_mm": "40"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["calibration"]["verdict"] == "calibrated"


def test_scan_multiple_images(client):
    # front + back: two files under the same "images" field.
    r = client.post(
        "/scan",
        files=[
            ("images", ("front.png", _marker_png(), "image/png")),
            ("images", ("back.png", _marker_png(marker_id=0), "image/png")),
        ],
        data={"label_text": "MRP Rs. 30.00 (incl. of all taxes)", "marker_mm": "40"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["calibration"]["verdict"] == "calibrated"


def test_override_requires_reason(client):
    scan_id = client.post(
        "/scan",
        files={"images": ("a.png", _marker_png(), "image/png")},
        data={"label_text": "MRP Rs. 10", "marker_mm": "40"},
    ).json()["report_id"]

    bad = client.post(f"/scans/{scan_id}/actions",
                      data={"declaration_id": "mrp", "action": "override"})
    assert bad.status_code == 400

    ok = client.post(f"/scans/{scan_id}/actions",
                     data={"declaration_id": "mrp", "action": "override",
                           "reason": "verified with caliper"})
    assert ok.status_code == 200
