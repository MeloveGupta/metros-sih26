"""Persistence + search for scans, products, users, and the audit log."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, Optional

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings
from ..schemas.report import Report
from .models import AuditLog, Base, ProductRow, ScanRow, User


def make_engine(database_url: Optional[str] = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if url.startswith("sqlite"):
        connect_args["timeout"] = 15  # wait out transient locks instead of erroring
    # Ensure the parent directory exists for a file-based SQLite DB.
    if url.startswith("sqlite:///") and ":memory:" not in url:
        db_path = Path(url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args=connect_args, future=True)
    # WAL improves concurrent read/write durability for the file-based DB.
    if url.startswith("sqlite:///") and ":memory:" not in url:
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables (idempotent)."""
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


# --- scans ---

def save_report(session: Session, report: Report,
                created_by: Optional[str] = None) -> ScanRow:
    """Persist a report (and its product, if named) and return the scan row."""
    product_id = None
    if report.product and (report.product.name or report.product.barcode_text):
        product_id = str(uuid.uuid4())
        session.add(ProductRow(
            id=product_id,
            name=report.product.name or "",
            brand=report.product.brand or "",
            category=report.product.category or "",
            source=report.product.source or "",
            barcode_text=report.product.barcode_text or "",
        ))

    row = ScanRow(
        id=report.report_id,
        product_id=product_id,
        ref_no=report.ref_no or "",
        disposition=report.disposition.value,
        calibrated=report.calibration.verdict.value,
        sha256=report.evidence.original.sha256,
        report_json=report.model_dump_json(by_alias=True),
        created_by=created_by,
    )
    session.add(row)
    session.commit()
    return row


def get_report(session: Session, scan_id: str) -> Optional[Report]:
    row = session.get(ScanRow, scan_id)
    if row is None:
        return None
    return Report.model_validate_json(row.report_json)


def update_report(session: Session, report: Report) -> Optional[ScanRow]:
    """Overwrite the stored report JSON + denormalized columns for an existing scan."""
    row = session.get(ScanRow, report.report_id)
    if row is None:
        return None
    row.disposition = report.disposition.value
    row.report_json = report.model_dump_json(by_alias=True)
    session.commit()
    return row


def search_scans(
    session: Session,
    *,
    disposition: Optional[str] = None,
    sha256: Optional[str] = None,
    product_name: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[ScanRow]:
    stmt = select(ScanRow)
    if disposition:
        stmt = stmt.where(ScanRow.disposition == disposition)
    if sha256:
        stmt = stmt.where(ScanRow.sha256 == sha256)
    if product_name:
        stmt = (stmt.join(ProductRow, ScanRow.product_id == ProductRow.id)
                    .where(ProductRow.name.ilike(f"%{product_name}%")))
    stmt = stmt.order_by(ScanRow.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


# --- audit ---

def append_audit(session: Session, *, action: str, user_id: Optional[str] = None,
                 target: str = "", reason: str = "") -> None:
    session.add(AuditLog(user_id=user_id, action=action, target=target, reason=reason))
    session.commit()


# --- users ---

def create_user(session: Session, *, email: str, name: str, role: str,
                pw_hash: str) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=name, role=role, pw_hash=pw_hash)
    session.add(user)
    session.commit()
    return user


def get_user_by_email(session: Session, email: str) -> Optional[User]:
    return session.scalar(select(User).where(User.email == email))
