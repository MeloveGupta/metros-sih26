"""Metros HTTP API (FastAPI).

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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.errors import MetrosError
from ..db.repository import (
    append_audit,
    get_report,
    init_db,
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

app = FastAPI(title="Metros API", version="0.1.0")

_engine = make_engine()
init_db(_engine)
_Session = session_factory(_engine)


def get_session():
    with _Session() as session:
        yield session


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


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
    llm: bool = Form(True),
    session=Depends(get_session),
):
    # Prototype: no auth. Actions are attributed to a default field officer.
    user = {"sub": "prototype-officer", "role": "officer"}
    if len(images) < 2:
        raise HTTPException(
            status_code=400,
            detail="upload both the front and back of the pack (two images)",
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
    inspection = Inspection(officer=Officer(id=user["sub"], name=user["sub"],
                                            role=user["role"]))
    report = run_scan(decoded, ocrs, marker_mm=marker_mm, dict_name=dict_name,
                      product=product, inspection=inspection,
                      image_file=images[0].filename or "upload.jpg",
                      extract_backend="regex" if llm is False else "auto")

    save_report(session, report, created_by=user["sub"])
    append_audit(session, action="scan", user_id=user["sub"], target=report.report_id)
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans")
def list_scans(disposition: Optional[str] = None, product_name: Optional[str] = None,
               limit: int = 50, offset: int = 0,
               session=Depends(get_session)):
    rows = search_scans(session, disposition=disposition, product_name=product_name,
                        limit=limit, offset=offset)
    return [
        {"id": r.id, "ref_no": r.ref_no, "disposition": r.disposition,
         "calibrated": r.calibrated, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@app.get("/scans/{scan_id}")
def fetch_scan(scan_id: str, session=Depends(get_session)):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans/{scan_id}/report.pdf")
def download_pdf(scan_id: str, session=Depends(get_session)):
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
def finalize(scan_id: str, body: FinalizeBody, session=Depends(get_session)):
    """Record the officer's decision on each flagged item and finalize the report.

    Each decision is appended (append-only) to the report's officer_actions and
    the audit log; the finalized report re-renders into the PDF with real
    officer findings.
    """
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")

    now = datetime.now(timezone.utc)
    officer = body.officer_name.strip() or "field officer"
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
            officer_id=officer,
            at=now,
        ))
        append_audit(session, action=f"officer_{a.verdict}", user_id=officer,
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
