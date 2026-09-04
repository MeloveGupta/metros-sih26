"""MetroScan HTTP API (FastAPI).

Endpoints:
  POST /auth/token           login -> JWT
  POST /scan                 upload image (+ optional label text / metadata) -> Report
  GET  /scans                search the repository
  GET  /scans/{id}           fetch a stored report
  GET  /scans/{id}/report.docx   download editable report
  POST /scans/{id}/actions   officer verification / override (audited)
  GET  /health

Offline-first: if PaddleOCR is not installed, callers pass `label_text` and the
scan still runs. Millimetre verdicts require a calibration marker in the image.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from ..core.config import get_settings
from ..core.errors import MetroScanError
from ..db.repository import (
    append_audit,
    get_report,
    get_user_by_email,
    init_db,
    make_engine,
    save_report,
    search_scans,
    session_factory,
)
from ..pipeline import run_scan
from ..reports.render import render_docx
from ..schemas.report import Inspection, Officer, Product
from ..vision.ocr import OcrResult, ocr_from_text, paddle_ocr
from .security import (
    create_access_token,
    decode_token,
    role_allows,
    verify_password,
)

app = FastAPI(title="MetroScan API", version="0.1.0")
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")

_engine = make_engine()
init_db(_engine)
_Session = session_factory(_engine)


def get_session():
    with _Session() as session:
        yield session


def current_user(token: str = Depends(oauth2)) -> dict:
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return {"sub": payload.get("sub"), "role": payload.get("role", "officer")}


def require_roles(*allowed: str):
    def _dep(user: dict = Depends(current_user)) -> dict:
        if not role_allows(user["role"], allowed):
            raise HTTPException(status_code=403, detail=f"role {user['role']} not permitted")
        return user
    return _dep


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


@app.post("/auth/token")
def login(form: OAuth2PasswordRequestForm = Depends(), session=Depends(get_session)):
    user = get_user_by_email(session, form.username)
    if user is None or not verify_password(form.password, user.pw_hash):
        raise HTTPException(status_code=401, detail="incorrect email or password")
    token = create_access_token(sub=user.id, role=user.role)
    return {"access_token": token, "token_type": "bearer", "role": user.role}


def _decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="could not decode image")
    return img


@app.post("/scan")
async def scan(
    image: UploadFile = File(...),
    label_text: Optional[str] = Form(None),
    marker_mm: Optional[float] = Form(None),
    dict_name: str = Form("DICT_4X4_50"),
    product_name: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    llm: bool = Form(False),
    user: dict = Depends(require_roles("officer", "admin")),
    session=Depends(get_session),
):
    img = _decode_image(await image.read())

    # OCR: PaddleOCR if available, else the provided label text.
    if label_text:
        ocr: OcrResult = ocr_from_text(label_text)
    else:
        try:
            ocr = paddle_ocr(img)
        except MetroScanError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"OCR unavailable and no label_text provided: {exc}",
            )

    product = Product(name=product_name, brand=brand, category=category, source=source)
    inspection = Inspection(officer=Officer(id=user["sub"] or "unknown", name=user["sub"] or "officer",
                                            role=user["role"]))
    report = run_scan(img, ocr, marker_mm=marker_mm, dict_name=dict_name,
                      product=product, inspection=inspection,
                      image_file=image.filename or "upload.jpg",
                      extract_backend="auto" if llm else "regex")

    save_report(session, report, created_by=user["sub"])
    append_audit(session, action="scan", user_id=user["sub"], target=report.report_id)
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans")
def list_scans(disposition: Optional[str] = None, product_name: Optional[str] = None,
               limit: int = 50, offset: int = 0,
               user: dict = Depends(require_roles("officer", "admin", "auditor")),
               session=Depends(get_session)):
    rows = search_scans(session, disposition=disposition, product_name=product_name,
                        limit=limit, offset=offset)
    return [
        {"id": r.id, "ref_no": r.ref_no, "disposition": r.disposition,
         "calibrated": r.calibrated, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@app.get("/scans/{scan_id}")
def fetch_scan(scan_id: str,
               user: dict = Depends(require_roles("officer", "admin", "auditor")),
               session=Depends(get_session)):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans/{scan_id}/report.docx")
def download_docx(scan_id: str,
                  user: dict = Depends(require_roles("officer", "admin", "auditor")),
                  session=Depends(get_session)):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    out = Path(tempfile.gettempdir()) / f"metroscan-{scan_id}.docx"
    render_docx(report, out)
    return FileResponse(str(out), filename=f"metroscan-{scan_id}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.post("/scans/{scan_id}/actions")
def officer_action(scan_id: str, declaration_id: str = Form(...), action: str = Form(...),
                   reason: str = Form(""),
                   user: dict = Depends(require_roles("officer", "admin")),
                   session=Depends(get_session)):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    if action == "override" and not reason:
        raise HTTPException(status_code=400, detail="override requires a reason")
    append_audit(session, action=f"officer_{action}", user_id=user["sub"],
                 target=f"{scan_id}/{declaration_id}", reason=reason)
    return {"status": "recorded", "scan_id": scan_id, "declaration_id": declaration_id,
            "action": action}
