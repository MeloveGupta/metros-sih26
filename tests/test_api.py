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
        files=[("images", ("front.png", _marker_png(), "image/png")),
               ("images", ("back.png", _marker_png(), "image/png"))],
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

    pdf = client.get(f"/scans/{scan_id}/report.pdf")
    # 200 when WeasyPrint's native stack is present, 503 when it isn't.
    assert pdf.status_code in (200, 503)
    if pdf.status_code == 200:
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content[:5] == b"%PDF-"


def test_scan_without_label_text_still_works(client):
    r = client.post(
        "/scan",
        files=[("images", ("f.png", _marker_png(), "image/png")),
               ("images", ("b.png", _marker_png(), "image/png"))],
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


def test_single_image_rejected(client):
    r = client.post("/scan",
                    files={"images": ("only.png", _marker_png(), "image/png")},
                    data={"marker_mm": "40"})
    assert r.status_code == 400


def test_finalize_records_officer_actions(client):
    scan_id = client.post(
        "/scan",
        files=[("images", ("f.png", _marker_png(), "image/png")),
               ("images", ("b.png", _marker_png(), "image/png"))],
        data={"label_text": "MRP Rs. 10", "marker_mm": "40"},
    ).json()["report_id"]

    # confirmed issue without a note is rejected
    bad = client.post(f"/scans/{scan_id}/finalize", json={
        "actions": [{"declaration_id": "mrp", "verdict": "confirmed_issue", "note": ""}]})
    assert bad.status_code == 400

    ok = client.post(f"/scans/{scan_id}/finalize", json={
        "officer_name": "Insp. Rao",
        "actions": [
            {"declaration_id": "mrp", "verdict": "verified_compliant", "note": "reads 10 on pack"},
            {"declaration_id": "net_quantity", "verdict": "confirmed_issue", "note": "no net qty"},
        ]})
    assert ok.status_code == 200
    body = ok.json()
    assert body["finalized_by"] == "Insp. Rao"
    assert len(body["officer_actions"]) == 2
