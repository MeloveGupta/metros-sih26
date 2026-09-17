"""Metros HTTP API (FastAPI).

Endpoints:
  POST /auth/token                login -> JWT
  POST /users                     create a user (admin)
  GET  /users                     list users (admin)
  POST /scan                      upload image (+ optional label text / metadata) -> Report
  GET  /scans                     search the repository (paged)
  GET  /stats                     dashboard KPIs
  GET  /scans/{id}                fetch a stored report
  GET  /scans/{id}/review-items   findings that need an officer's decision
  GET  /scans/{id}/images/{n}     download the n-th uploaded evidence image
  GET  /scans/{id}/crops/{name}   download a declaration's evidence crop
  GET  /scans/{id}/report.pdf     download the PDF report
  GET  /scans/{id}/report.docx    download the editable DOCX report
  POST /scans/{id}/finalize       officer verification (audited)
  GET  /health

Auth is on by default (Bearer JWT from /auth/token); METROS_AUTH_DISABLED=1
bypasses it for local development only (refused at startup in production --
see core.config.production_safety_check). Millimetre verdicts require a
calibration marker in the image; without one, OCR/label text is still read.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from starlette.concurrency import run_in_threadpool

from ..core import storage
from ..core.config import get_settings, marker_size_mismatch_warning, production_safety_check
from ..core.errors import MetrosError
from ..db.repository import (
    append_audit,
    create_user,
    ensure_admin_user,
    get_report,
    get_user_by_email,
    init_db,
    list_users,
    make_engine,
    save_report,
    search_scans,
    session_factory,
    stats as db_stats,
    update_report,
)
from ..pipeline import EvidenceImageInput, run_scan
from ..reports.render import render_docx, render_pdf
from ..schemas.report import Inspection, Officer, OfficerAction, Product
from ..extract import gemini_reader
from ..vision.ocr import engine_available, ocr_from_text, select_ocr_engine
from .auth import CurrentUser, require_role
from .security import ROLES, create_access_token, hash_password, verify_password

app = FastAPI(title="Metros API", version="0.1.0")

_allowed_origins = [o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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


def _bootstrap_admin_from_env() -> None:
    """Create an admin from ADMIN_EMAIL/ADMIN_PASSWORD on startup, if set and
    no users exist yet (see db.repository.ensure_admin_user)."""
    settings = get_settings()
    if not (settings.admin_email and settings.admin_password):
        return
    with _Session() as session:
        ensure_admin_user(session, email=settings.admin_email,
                          password_hash=hash_password(settings.admin_password))


_bootstrap_admin_from_env()


@app.get("/health")
def health(session=Depends(get_session)):
    settings = get_settings()
    # A cheap DB round-trip, not just "the process is up": this endpoint
    # doubles as an uptime-monitor keep-alive target (Render's free tier
    # spins the container down after ~15 min idle) -- pinging it should
    # also touch Supabase, whose free tier pauses a project after enough
    # days of no API activity, or the container could stay warm while the
    # database underneath it is asleep.
    try:
        session.execute(sql_text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False
    return {
        "status": "ok",
        "version": app.version,
        "database_ok": database_ok,
        "ocr_engine": settings.ocr_engine,
        "ocr_engine_available": engine_available(settings.ocr_engine),
        "gemini_api_key_configured": gemini_reader.gemini_available(),
        "gemini_model": gemini_reader.DEFAULT_MODEL,
        "gemini_fallback_model": gemini_reader.FALLBACK_MODEL,
    }


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


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    name = Path(name or "upload").name  # strip any directory component
    name = _UNSAFE_FILENAME.sub("_", name)
    return name or "upload"


_DEFAULT_ROLES = ["front", "back"]


def _save_uploads(report_id: str, filenames: List[str], raw: List[bytes],
                  roles: Optional[List[str]] = None) -> List[EvidenceImageInput]:
    """Persist the uploaded bytes as-is (not re-encoded pixels) and hash them."""
    saved = []
    for idx, (name, data) in enumerate(zip(filenames, raw)):
        safe = _safe_filename(name or f"image_{idx}.jpg")
        rel = f"{report_id}/{idx}_{safe}"
        storage.save_bytes(rel, data)
        role = roles[idx] if roles else (_DEFAULT_ROLES[idx] if idx < len(_DEFAULT_ROLES) else "other")
        saved.append(EvidenceImageInput(
            path=rel, sha256="sha256:" + hashlib.sha256(data).hexdigest(), role=role))
    return saved


@app.post("/scan")
async def scan(
    images: List[UploadFile] = File(default=[]),
    label_text: Optional[str] = Form(None),
    marker_mm: Optional[float] = Form(None),
    dict_name: str = Form("DICT_4X4_50"),
    product_name: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    session=Depends(get_session),
    current_user: CurrentUser = Depends(require_role("officer", "admin")),
):
    if source is not None and source not in ("retail_pack", "ecommerce_listing"):
        raise HTTPException(
            status_code=400,
            detail="source must be one of: retail_pack, ecommerce_listing",
        )
    is_listing = source == "ecommerce_listing"

    if is_listing:
        if not images and not (label_text and label_text.strip()):
            raise HTTPException(
                status_code=400,
                detail="an e-commerce listing scan needs at least one screenshot or pasted listing text",
            )
    elif len(images) < 2:
        raise HTTPException(
            status_code=400,
            detail="upload both the front and back of the pack (two images)",
        )

    if category is not None and category not in ("food", "cosmetic", "other_non_food", "unknown"):
        raise HTTPException(
            status_code=400,
            detail="category must be one of: food, cosmetic, other_non_food, unknown",
        )

    if images:
        raw_bytes = [await f.read() for f in images]
        decoded = [_decode_image(data) for data in raw_bytes]
        filenames = [f.filename or f"image_{i}.jpg" for i, f in enumerate(images)]
        roles = None  # front/back/other, per _save_uploads' default
    else:
        # Text-only listing: no physical/digital screenshot at all. Keep a
        # placeholder image so the pipeline's "at least one image" invariant
        # holds; it is saved and hashed like any other evidence file.
        blank = np.full((40, 40, 3), 255, np.uint8)
        raw_bytes = [cv2.imencode(".png", blank)[1].tobytes()]
        decoded = [blank]
        filenames = ["listing-text-only.png"]
        roles = ["listing"]

    report_id = str(uuid.uuid4())
    # Blocking (network I/O to Supabase Storage or local disk) -- run off the
    # event loop so a slow upload doesn't stall every other request this
    # single worker is handling, Render's own health check included.
    evidence_images = await run_in_threadpool(
        _save_uploads, report_id, filenames, raw_bytes, roles=roles
    )

    settings = get_settings()
    # Gated on gemini_available() too: without GEMINI_API_KEY configured,
    # stay on the Tesseract path rather than skip OCR and produce an empty
    # report (Gemini's own OCR-fallback inside dispatch.py only helps once
    # text has already been read from *some* source).
    use_gemini = (settings.ocr_engine == "gemini" and not label_text
                 and gemini_reader.gemini_available())
    if use_gemini:
        # The vision path reads the images directly -- skip Tesseract entirely
        # for speed (Rule 7/8's own marker-token fallback in run_scan() covers
        # word/line boxes lazily if a calibration card is actually found).
        ocrs = [ocr_from_text("") for _ in decoded]
        ocr_backend_used, ocr_warning, extract_backend = "tesseract", None, "gemini"
    else:
        # Tesseract OCR -- CPU-bound, same event-loop concern as run_scan below.
        ocrs, ocr_backend_used, ocr_warning = await run_in_threadpool(
            select_ocr_engine, decoded, label_text
        )
        extract_backend = "regex"

    product = Product(name=product_name, category=category, source=source)
    inspection = Inspection(officer=Officer(id=current_user.sub,
                                            name=current_user.name or current_user.sub,
                                            role=current_user.role))
    try:
        # The dominant cost of a scan: ArUco calibration alone runs up to six
        # detection passes per image (see backend/vision/scale.py) plus rule
        # evaluation -- squarely CPU-bound, and can take tens of seconds on a
        # throttled host. Off the event loop, so it can't starve health
        # checks or other requests this worker is handling concurrently.
        report = await run_in_threadpool(
            run_scan, decoded, ocrs, marker_mm=marker_mm, dict_name=dict_name,
            product=product, inspection=inspection,
            image_file=filenames[0],
            ocr_backend_used=ocr_backend_used,
            ocr_warning=ocr_warning,
            extract_backend=extract_backend,
            label_text_provided=bool(label_text),
            category=category,
            report_id=report_id, evidence_images=evidence_images,
            save_crops=True, skip_physical_measurement=is_listing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    save_report(session, report, created_by=current_user.sub)
    append_audit(session, action="scan", user_id=current_user.sub, target=report.report_id)
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans")
def list_scans(disposition: Optional[str] = None, product_name: Optional[str] = None,
               category: Optional[str] = None,
               finalized: Optional[bool] = None, has_rule7_flag: Optional[bool] = None,
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               limit: int = 50, offset: int = 0,
               session=Depends(get_session),
               _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    try:
        parsed_from = datetime.fromisoformat(date_from) if date_from else None
        parsed_to = datetime.fromisoformat(date_to) if date_to else None
    except ValueError:
        raise HTTPException(status_code=400, detail="date_from/date_to must be ISO dates")

    rows, total = search_scans(
        session, disposition=disposition, product_name=product_name,
        category=category, finalized=finalized, has_rule7_flag=has_rule7_flag,
        date_from=parsed_from, date_to=parsed_to, limit=limit, offset=offset,
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [
            {"id": r.id, "ref_no": r.ref_no, "disposition": r.disposition,
             "calibrated": r.calibrated, "finalized": r.finalized,
             "has_rule7_flag": r.has_rule7_flag,
             "product_name": r.product.name if r.product else None,
             "brand": r.product.brand if r.product else None,
             "created_at": r.created_at.isoformat()}
            for r in rows
        ],
    }


@app.get("/stats")
def get_stats(session=Depends(get_session),
             _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    return db_stats(session)


@app.get("/scans/{scan_id}")
def fetch_scan(scan_id: str, session=Depends(get_session),
               _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return JSONResponse(content=report.model_dump(by_alias=True, mode="json"))


@app.get("/scans/{scan_id}/images/{n}")
def get_scan_image(scan_id: str, n: int, session=Depends(get_session),
                   _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    if n < 0 or n >= len(report.evidence.images):
        raise HTTPException(status_code=404, detail="no such evidence image")
    img = report.evidence.images[n]
    try:
        data = storage.read_bytes(img.file)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="evidence image file is missing")
    media_type = mimetypes.guess_type(img.file)[0] or "application/octet-stream"
    return Response(content=data, media_type=media_type)


@app.get("/scans/{scan_id}/crops/{name}")
def get_scan_crop(scan_id: str, name: str, session=Depends(get_session),
                  _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    matches = (
        d for group in (report.declarations, report.placement, report.readability)
        for d in group if d.id == name and d.evidence_crop
    )
    finding = next(matches, None)
    if finding is None:
        raise HTTPException(status_code=404, detail="no such evidence crop")
    try:
        data = storage.read_bytes(finding.evidence_crop)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="evidence crop file is missing")
    media_type = mimetypes.guess_type(finding.evidence_crop)[0] or "application/octet-stream"
    return Response(content=data, media_type=media_type)


_REVIEW_STATUSES = {"potential_non_compliance", "not_detected", "not_assessable"}


def _review_items(report) -> List[dict]:
    """Every declaration/font/placement finding an officer must personally
    check -- anything the automated pipeline couldn't clear on its own."""
    items = []
    for d in report.declarations:
        if d.status.value in _REVIEW_STATUSES:
            items.append({"kind": "declaration", "id": d.id, "label": d.label,
                          "clause": d.clause_ref.clause, "status": d.status.value,
                          "reason": d.note})
    for f in report.font_analysis.items:
        if f.status.value in _REVIEW_STATUSES:
            items.append({"kind": "font", "id": f.declaration_id,
                          "label": f"Letter height -- {f.declaration_id}",
                          "clause": "Rule 7", "status": f.status.value,
                          "reason": f.reason})
    for p in report.placement:
        if p.status.value in _REVIEW_STATUSES:
            items.append({"kind": "placement", "id": p.id, "label": p.label,
                          "clause": p.clause_ref.clause, "status": p.status.value,
                          "reason": p.note})
    return items


@app.get("/scans/{scan_id}/review-items")
def get_review_items(scan_id: str, session=Depends(get_session),
                     _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return {"items": _review_items(report)}


def _require_finalized_or_nothing_to_review(report) -> None:
    if report.finalized_at is None and _review_items(report):
        raise HTTPException(
            status_code=409,
            detail="this report has unresolved review items; finalize it before downloading",
        )


@app.get("/scans/{scan_id}/report.pdf")
def download_pdf(scan_id: str, session=Depends(get_session),
                 _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    _require_finalized_or_nothing_to_review(report)
    # A report's rendering is deterministic from its (immutable once
    # downloadable) Report JSON, so cache the bytes in the storage backend
    # the first time -- avoids re-running WeasyPrint on every download.
    cache_key = f"{scan_id}/report.pdf"
    try:
        data = storage.read_bytes(cache_key)
    except FileNotFoundError:
        out = Path(tempfile.gettempdir()) / f"metros-{scan_id}.pdf"
        try:
            render_pdf(report, out)
        except MetrosError as exc:
            raise HTTPException(status_code=503, detail=f"PDF rendering unavailable: {exc}")
        data = out.read_bytes()
        storage.save_bytes(cache_key, data)
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="metros-{scan_id}.pdf"'},
    )


@app.get("/scans/{scan_id}/report.docx")
def download_docx(scan_id: str, session=Depends(get_session),
                  _user: CurrentUser = Depends(require_role("officer", "admin", "auditor"))):
    report = get_report(session, scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail="scan not found")
    _require_finalized_or_nothing_to_review(report)
    cache_key = f"{scan_id}/report.docx"
    try:
        data = storage.read_bytes(cache_key)
    except FileNotFoundError:
        out = Path(tempfile.gettempdir()) / f"metros-{scan_id}.docx"
        try:
            render_docx(report, out)
        except MetrosError as exc:
            raise HTTPException(status_code=503, detail=f"DOCX rendering unavailable: {exc}")
        data = out.read_bytes()
        storage.save_bytes(cache_key, data)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="metros-{scan_id}.docx"'},
    )


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
    if report.finalized_at is not None:
        raise HTTPException(status_code=409, detail="this report is already finalized")

    review_items = _review_items(report)
    submitted_ids = {a.declaration_id for a in body.actions}
    missing = [item["label"] for item in review_items if item["id"] not in submitted_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"a decision is required for every flagged item, missing: {', '.join(missing)}",
        )

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

    report.final_disposition = (
        "potential_non_compliance_confirmed_by_officer"
        if any(a.verdict == "confirmed_issue" for a in body.actions)
        else "verified_compliant_by_officer"
    )

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
