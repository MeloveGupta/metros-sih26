"""Metros HTTP API (FastAPI).

Endpoints:
  POST /auth/token                login -> JWT
  POST /users                     create a user (admin)
  GET  /users                     list users (admin)
  POST /scan                      upload image (+ optional label text / metadata) -> Report
  GET  /scans                     search the repository
  GET  /scans/{id}                fetch a stored report
  GET  /scans/{id}/report.pdf     download the PDF report
  POST /scans/{id}/finalize       officer verification (audited)
  GET  /health

Auth is on by default (Bearer JWT from /auth/token); METROS_AUTH_DISABLED=1
bypasses it for local development only (refused at startup in production --
see core.config.production_safety_check). Millimetre verdicts require a
calibration marker in the image; without one, OCR/label text is still read.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..core.config import get_settings, marker_size_mismatch_warning, production_safety_check
from ..core.errors import MetrosError
from ..db.repository import (
    append_audit,
    create_user,
    get_report,
    get_user_by_email,
    init_db,
    list_users,
    make_engine,
    save_report,
    search_scans,
    session_factory,
    update_report,
)
from ..pipeline import run_scan
from ..reports.render import render_pdf
from ..schemas.report import Inspection, Officer, OfficerAction, Product
from ..vision.ocr import (
    OcrResult,
    ocr_from_text,
    paddle_ocr,
    tesseract_available,
    tesseract_ocr,
)
from .auth import CurrentUser, get_current_user, require_role
from .security import ROLES, create_access_token, hash_password, verify_password

app = FastAPI(title="Metros API", version="0.1.0")

production_safety_check()

_marker_warning = marker_size_mismatch_warning()
if _marker_warning:
    logging.getLogger(__name__).warning(_marker_warning)

_engine = make_engine()
init_db(_engine)
_Session = session_factory(_engine)


def get_session():
    with _Session() as session:
        yield session


@app.get("/health")
def health():
    from ..extract.llm import llm_available
    return {"status": "ok", "version": app.version, "llm_available": llm_available()}


class TokenRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str


@app.post("/auth/token", response_model=TokenResponse)
def login(body: TokenRequest, session=Depends(get_session)):
    user = get_user_by_email(session, body.email)
    if user is None or not verify_password(body.password, user.pw_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = create_access_token(sub=user.id, role=user.role, name=user.name)
    return TokenResponse(access_token=token, role=user.role, name=user.name)


class CreateUserRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    role: str = "officer"


@app.post("/users")
def create_user_route(body: CreateUserRequest, session=Depends(get_session),
                      _admin: CurrentUser = Depends(require_role("admin"))):
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    if get_user_by_email(session, body.email) is not None:
        raise HTTPException(status_code=409, detail="a user with that email already exists")
    user = create_user(session, email=body.email, name=body.name or body.email,
                       role=body.role, pw_hash=hash_password(body.password))
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@app.get("/users")
def list_users_route(session=Depends(get_session),
                     _admin: CurrentUser = Depends(require_role("admin"))):
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role}
            for u in list_users(session)]


def _decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="could not decode image")
    return img


def _ocr_image(img, label_text: Optional[str]) -> OcrResult:
    if label_text:
        return ocr_from_text(label_text)
    if tesseract_available():
        try:
            return tesseract_ocr(img)
        except MetrosError:
            return ocr_from_text("")
    try:
        return paddle_ocr(img)
    except MetrosError:
        return ocr_from_text("")


@app.post("/scan")
async def scan(
    images: List[UploadFile] = File(...),
    label_text: Optional[str] = Form(None),
    marker_mm: Optional[float] = Form(None),
    dict_name: str = Form("DICT_4X4_50"),
    product_name: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    common_name: Optional[str] = Form(None),
    panel_shape: Optional[str] = Form(None),
    panel_height_cm: Optional[float] = Form(None),
    panel_width_cm: Optional[float] = Form(None),
    panel_circumference_cm: Optional[float] = Form(None),
    panel_area_cm2_other: Optional[float] = Form(None),
    llm: bool = Form(True),
    session=Depends(get_session),
    current_user: CurrentUser = Depends(require_role("officer", "admin")),
):
    if len(images) < 2:
        raise HTTPException(
            status_code=400,
            detail="upload both the front and back of the pack (two images)",
        )

    if category is not None and category not in ("food", "cosmetic", "other_non_food", "unknown"):
        raise HTTPException(
            status_code=400,
            detail="category must be one of: food, cosmetic, other_non_food, unknown",
        )

    decoded = [_decode_image(await f.read()) for f in images]

    # OCR is only needed when the LLM vision path is NOT used (it reads images
    # directly). Skipping Tesseract when AI is on removes N slow OCR passes.
    from ..extract.llm import llm_available
    use_llm = llm is not False and llm_available()
    if use_llm and not label_text:
        ocrs = [ocr_from_text("") for _ in decoded]
    else:
        ocrs = [_ocr_image(img, label_text if i == 0 else None)
                for i, img in enumerate(decoded)]

    product = Product(name=product_name, brand=brand, category=category, source=source)
    inspection = Inspection(officer=Officer(id=current_user.sub,
                                            name=current_user.name or current_user.sub,
                                            role=current_user.role))
    try:
        report = run_scan(decoded, ocrs, marker_mm=marker_mm, dict_name=dict_name,
                          product=product, inspection=inspection,
                          image_file=images[0].filename or "upload.jpg",
                          extract_backend="regex" if llm is False else "auto",
                          label_text_provided=bool(label_text),
                          common_name=common_name,
                          panel_shape=panel_shape, panel_height_cm=panel_height_cm,
                          panel_width_cm=panel_width_cm,
                          panel_circumference_cm=panel_circumference_cm,
                          panel_area_cm2_other=panel_area_cm2_other,
                          category=category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    save_report(session, report, created_by=current_user.sub)
    append_audit(session, action="scan", user_id=current_user.sub, target=report.report_id)
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans")
def list_scans(disposition: Optional[str] = None, product_name: Optional[str] = None,
               limit: int = 50, offset: int = 0,
               session=Depends(get_session),
               _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    rows = search_scans(session, disposition=disposition, product_name=product_name,
                        limit=limit, offset=offset)
    return [
        {"id": r.id, "ref_no": r.ref_no, "disposition": r.disposition,
         "calibrated": r.calibrated, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@app.get("/scans/{scan_id}")
def fetch_scan(scan_id: str, session=Depends(get_session),
               _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans/{scan_id}/report.pdf")
def download_pdf(scan_id: str, session=Depends(get_session),
                 _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    out = Path(tempfile.gettempdir()) / f"metros-{scan_id}.pdf"
    try:
        render_pdf(report, out)
    except MetrosError as exc:
        raise HTTPException(status_code=503, detail=f"PDF rendering unavailable: {exc}")
    return FileResponse(str(out), filename=f"metros-{scan_id}.pdf", media_type="application/pdf")


class FinalizeAction(BaseModel):
    declaration_id: str
    label: str = ""
    verdict: str            # "verified_compliant" | "confirmed_issue"
    note: str = ""


class FinalizeBody(BaseModel):
    officer_name: str = ""
    actions: List[FinalizeAction] = []


@app.post("/scans/{scan_id}/finalize")
def finalize(scan_id: str, body: FinalizeBody, session=Depends(get_session),
            current_user: CurrentUser = Depends(require_role("officer", "admin"))):
    """Record the officer's decision on each flagged item and finalize the report.

    Officer identity comes from the JWT, never a free-text field; `officer_name`
    is kept only as an optional display override. Each decision is appended
    (append-only) to the report's officer_actions and the audit log; the
    finalized report re-renders into the PDF with real officer findings.
    """
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")

    now = datetime.now(timezone.utc)
    officer_id = current_user.sub
    officer = body.officer_name.strip() or current_user.name or current_user.sub
    for a in body.actions:
        if a.verdict == "confirmed_issue" and not a.note.strip():
            raise HTTPException(
                status_code=400,
                detail=f"a confirmed non-compliance needs a note ({a.label or a.declaration_id})",
            )
        report.officer_actions.append(OfficerAction(
            declaration_id=a.declaration_id,
            action=a.verdict,
            reason=a.note.strip() or None,
            officer_id=officer_id,
            at=now,
        ))
        append_audit(session, action=f"officer_{a.verdict}", user_id=officer_id,
                     target=f"{scan_id}/{a.declaration_id}", reason=a.note.strip())

    # Record who finalized + when in the inspection block.
    if report.inspection.officer:
        report.inspection.officer.name = officer
    report.finalized_at = now
    report.finalized_by = officer

    update_report(session, report)
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


# --- Serve the built frontend (single origin; no dev server / HMR reloads) ---
# Mounted last so every API route above takes priority. Run `make frontend-build`
# to produce frontend/dist, then tunnel to this server (:8000).
_DIST = get_settings().repo_root / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse as _FileResponse

    @app.get("/", include_in_schema=False)
    def _index():
        return _FileResponse(str(_DIST / "index.html"))

    # assets/ and other built files
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
