"""API tests (auth disabled: these exercise scan/search behaviour, not auth
itself -- see test_auth.py for the real auth flow) with FastAPI TestClient
over a temp SQLite DB."""
from __future__ import annotations

import hashlib
import os
import tempfile

import cv2
import numpy as np
import pytest

# Point the app at an isolated DB + uploads dir BEFORE importing it.
_DB = os.path.join(tempfile.mkdtemp(), "api_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["UPLOADS_DIR"] = tempfile.mkdtemp()
os.environ["METROS_AUTH_DISABLED"] = "1"

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
    assert any(s["id"] == scan_id for s in listed["results"])

    # This scan leaves review items outstanding (e.g. the static Rule 8
    # grouping check, generic name needing confirmation) -- downloads are
    # gated until an officer finalizes.
    review_items = client.get(f"/scans/{scan_id}/review-items").json()["items"]
    assert review_items
    assert client.get(f"/scans/{scan_id}/report.pdf").status_code == 409
    actions = [{"declaration_id": i["id"], "verdict": "verified_compliant", "note": "checked"}
              for i in review_items]
    finalized = client.post(f"/scans/{scan_id}/finalize", json={"actions": actions})
    assert finalized.status_code == 200

    pdf = client.get(f"/scans/{scan_id}/report.pdf")
    # 200 when WeasyPrint's native stack is present, 503 when it isn't.
    assert pdf.status_code in (200, 503)
    if pdf.status_code == 200:
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content[:5] == b"%PDF-"

    docx = client.get(f"/scans/{scan_id}/report.docx")
    assert docx.status_code in (200, 503)
    if docx.status_code == 200:
        assert docx.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert docx.content[:2] == b"PK"  # DOCX is a zip archive


def test_scan_persists_raw_upload_bytes_with_matching_hash_and_roles(client):
    front_bytes = _marker_png(marker_id=1)
    back_bytes = _marker_png(marker_id=1)
    r = client.post(
        "/scan",
        files=[("images", ("front.png", front_bytes, "image/png")),
               ("images", ("back.png", back_bytes, "image/png"))],
        data={"marker_mm": "40"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    images = body["evidence"]["images"]
    assert len(images) == 2
    assert [i["role"] for i in images] == ["front", "back"]
    assert images[0]["sha256"] == "sha256:" + hashlib.sha256(front_bytes).hexdigest()
    assert images[1]["sha256"] == "sha256:" + hashlib.sha256(back_bytes).hexdigest()

    got = client.get(f"/scans/{body['report_id']}/images/0")
    assert got.status_code == 200
    assert got.content == front_bytes  # exact uploaded bytes, not a re-encoded copy

    assert client.get(f"/scans/{body['report_id']}/images/9").status_code == 404
    assert client.get(f"/scans/{body['report_id']}/crops/nope").status_code == 404
    assert client.get("/scans/no-such-scan/images/0").status_code == 404


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


def test_scan_ignores_removed_form_fields(client):
    """brand/common_name/panel_shape/panel dimensions were dropped from the
    web scan form -- a stale client (or old bookmark) that still posts them
    must not have them silently take effect."""
    r = client.post(
        "/scan",
        files=[("images", ("front.png", _marker_png(), "image/png")),
               ("images", ("back.png", _marker_png(), "image/png"))],
        data={"label_text": "MRP Rs. 45.00 (incl. of all taxes)", "marker_mm": "40",
              "brand": "Acme Foods", "common_name": "tomato ketchup",
              "panel_shape": "rectangular", "panel_height_cm": "20", "panel_width_cm": "30"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["brand"] is None
    common_name = next(d for d in body["declarations"] if d["id"] == "common_name")
    assert common_name["status"] == "not_assessable"
    assert body["font_analysis"].get("panel_area_cm2") is None


def test_ecommerce_listing_accepts_text_only_input(client):
    r = client.post(
        "/scan",
        data={"source": "ecommerce_listing",
              "label_text": "MRP Rs. 45.00 (incl. of all taxes)\nNet Qty 90 g\nMfg Aug 2026"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    mfg = next(d for d in body["declarations"] if d["id"] == "mfg_date")
    assert mfg["status"] == "not_applicable"
    assert body["font_analysis"]["items"] == []
    assert body["placement"] == []


def test_ecommerce_listing_accepts_a_single_screenshot(client):
    r = client.post(
        "/scan",
        files=[("images", ("listing.png", _marker_png(), "image/png"))],
        data={"source": "ecommerce_listing", "marker_mm": "40"},
    )
    assert r.status_code == 200, r.text


def test_ecommerce_listing_rejects_no_images_and_no_text(client):
    r = client.post("/scan", data={"source": "ecommerce_listing"})
    assert r.status_code == 400


def test_invalid_source_rejected(client):
    r = client.post("/scan", data={"source": "not-a-real-source",
                                   "label_text": "MRP Rs. 45.00"})
    assert r.status_code == 400


def _scan_with_gaps(client) -> str:
    return client.post(
        "/scan",
        files=[("images", ("f.png", _marker_png(), "image/png")),
               ("images", ("b.png", _marker_png(), "image/png"))],
        data={"label_text": "MRP Rs. 10", "marker_mm": "40"},
    ).json()["report_id"]


def test_review_items_lists_every_flagged_finding(client):
    scan_id = _scan_with_gaps(client)
    items = client.get(f"/scans/{scan_id}/review-items").json()["items"]
    assert items  # MRP-only label text leaves several Rule 6 declarations unresolved
    assert all(i["status"] in ("potential_non_compliance", "not_detected", "not_assessable")
              for i in items)
    assert any(i["id"] == "net_quantity" for i in items)


def test_finalize_records_officer_actions(client):
    scan_id = _scan_with_gaps(client)
    items = client.get(f"/scans/{scan_id}/review-items").json()["items"]

    # confirmed issue without a note is rejected
    bad = client.post(f"/scans/{scan_id}/finalize", json={
        "actions": [{"declaration_id": "mrp", "verdict": "confirmed_issue", "note": ""}]})
    assert bad.status_code == 400

    # a decision on only some of the flagged items is rejected, naming what's missing
    partial = client.post(f"/scans/{scan_id}/finalize", json={
        "actions": [{"declaration_id": items[0]["id"], "verdict": "verified_compliant",
                    "note": "checked"}]})
    assert partial.status_code == 400
    assert "missing" in partial.json()["detail"]

    actions = [
        {"declaration_id": i["id"],
         "verdict": "confirmed_issue" if i["id"] == "net_quantity" else "verified_compliant",
         "note": "no net qty on pack" if i["id"] == "net_quantity" else "checked physically"}
        for i in items
    ]
    ok = client.post(f"/scans/{scan_id}/finalize",
                     json={"officer_name": "Insp. Rao", "actions": actions})
    assert ok.status_code == 200
    body = ok.json()
    assert body["finalized_by"] == "Insp. Rao"
    assert len(body["officer_actions"]) == len(items)
    assert body["final_disposition"] == "potential_non_compliance_confirmed_by_officer"

    # already finalized -> 409, even with a fully-decided body
    again = client.post(f"/scans/{scan_id}/finalize",
                        json={"officer_name": "Insp. Rao", "actions": actions})
    assert again.status_code == 409


def test_download_gated_until_finalized_when_review_items_pending(client):
    scan_id = _scan_with_gaps(client)
    assert client.get(f"/scans/{scan_id}/report.pdf").status_code == 409

    items = client.get(f"/scans/{scan_id}/review-items").json()["items"]
    actions = [{"declaration_id": i["id"], "verdict": "verified_compliant", "note": "checked"}
              for i in items]
    client.post(f"/scans/{scan_id}/finalize", json={"actions": actions})

    pdf = client.get(f"/scans/{scan_id}/report.pdf")
    assert pdf.status_code in (200, 503)  # 503 only if WeasyPrint natives are missing


def test_download_allowed_once_the_one_remaining_review_item_is_cleared(client):
    # E-commerce listing (no font/placement checks) with every Rule 6 declaration
    # either matched or legitimately not_applicable -- the label text alone can
    # get every declaration except common_name to a final status; common_name
    # can no longer be supplied via the form, so it's always the one thing left
    # to review, never a silent pass.
    scan_id = client.post(
        "/scan",
        data={"label_text": (
            "Tomato Ketchup\n"
            "Manufactured by: FoodCo Pvt Ltd, Plot 12, Pune, Maharashtra 411001\n"
            "Net Qty 90 g\n"
            "MRP Rs. 45.00 (incl. of all taxes)\n"
            "Unit sale price: Rs. 0.50 per g\n"
            "Consumer care: FoodCo Care, 12 MG Road, Pune 411001, care@foodco.in, 1800-123-4567\n"
        ), "category": "food", "source": "ecommerce_listing"},
    ).json()["report_id"]
    assert client.get(f"/scans/{scan_id}/report.pdf").status_code == 409

    items = client.get(f"/scans/{scan_id}/review-items").json()["items"]
    assert [i["id"] for i in items] == ["common_name"]
    assert items[0]["reason"] == "generic name needs officer confirmation"

    finalized = client.post(f"/scans/{scan_id}/finalize", json={"actions": [
        {"declaration_id": "common_name", "verdict": "verified_compliant", "note": "checked on pack"},
    ]})
    assert finalized.status_code == 200

    assert client.get(f"/scans/{scan_id}/report.pdf").status_code in (200, 503)


def test_review_items_include_reason_text():
    """_review_items() must carry the finding's reason/note through -- an
    officer needs to know *why* something needs review, not just that it
    does. Exercised directly against a hand-built Report so it doesn't
    depend on OCR/vision producing a specific fallback branch."""
    from datetime import datetime, timezone

    from backend.schemas.report import (
        ClauseRef, DeclarationFinding, Evidence, FontAnalysis, FontItem,
        OriginalImage, Report, RuleCatalogInfo, Status,
    )

    report = Report(
        report_id="r-review",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        rule_catalog=RuleCatalogInfo(version="test", hash="sha256:x"),
        evidence=Evidence(images=[OriginalImage(file="a.jpg", sha256="x", width=1, height=1)]),
        declarations=[
            DeclarationFinding(id="common_name", label="Common name",
                               clause_ref=ClauseRef(clause="Rule 6(1)(b)"),
                               status=Status.NOT_ASSESSABLE,
                               note="generic name needs officer confirmation"),
        ],
        placement=[
            DeclarationFinding(id="placement_x", label="Placement X",
                               clause_ref=ClauseRef(clause="Rule 8(1)"),
                               status=Status.NOT_ASSESSABLE, note="some placement reason"),
        ],
        font_analysis=FontAnalysis(items=[
            FontItem(declaration_id="mrp", status=Status.NOT_ASSESSABLE,
                     reason="panel size not captured, Table-I band not selected"),
        ]),
    )
    by_kind = {i["kind"]: i for i in api._review_items(report)}
    assert by_kind["declaration"]["reason"] == "generic name needs officer confirmation"
    assert by_kind["font"]["reason"] == "panel size not captured, Table-I band not selected"
    assert by_kind["placement"]["reason"] == "some placement reason"
