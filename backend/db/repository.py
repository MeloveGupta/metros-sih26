"""Persistence + search for scans, products, users, and the audit log."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings
from ..schemas.report import Report, Status
from .models import AuditLog, Base, ProductRow, ScanRow, User


def make_engine(database_url: Optional[str] = None) -> Engine:
    url = database_url or get_settings().database_url
    is_sqlite = url.startswith("sqlite")
    engine_kwargs: dict = {"future": True}

    if is_sqlite:
        connect_args = {"check_same_thread": False, "timeout": 15}
        # Ensure the parent directory exists for a file-based SQLite DB.
        if url.startswith("sqlite:///") and ":memory:" not in url:
            db_path = Path(url.replace("sqlite:///", "", 1))
            db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Postgres, including Supabase's pgbouncer connection pooler:
        # disable psycopg3's server-side prepared statements. In pgbouncer's
        # transaction-pooling mode, a later EXECUTE can land on a different
        # physical connection than the one that PREPAREd the statement --
        # "prepared statement ... does not exist" otherwise. Harmless for a
        # direct (non-pooled) connection too. pool_pre_ping guards against
        # the pooler silently dropping idle connections between requests.
        connect_args = {"prepare_threshold": None}
        engine_kwargs["pool_pre_ping"] = True

    engine = create_engine(url, connect_args=connect_args, **engine_kwargs)

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

def _has_rule7_flag(report: Report) -> bool:
    return any(i.status == Status.POTENTIAL_NON_COMPLIANCE for i in report.font_analysis.items)


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
        sha256=report.evidence.original.sha256 if report.evidence.original else "",
        finalized=report.finalized_at is not None,
        has_rule7_flag=_has_rule7_flag(report),
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
    row.finalized = report.finalized_at is not None
    row.has_rule7_flag = _has_rule7_flag(report)
    row.report_json = report.model_dump_json(by_alias=True)
    session.commit()
    return row


def search_scans(
    session: Session,
    *,
    disposition: Optional[str] = None,
    sha256: Optional[str] = None,
    product_name: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    finalized: Optional[bool] = None,
    has_rule7_flag: Optional[bool] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[ScanRow], int]:
    """Search scans with paging. Returns (rows for this page, total matches)."""
    stmt = select(ScanRow)
    if product_name or brand or category:
        stmt = stmt.join(ProductRow, ScanRow.product_id == ProductRow.id)
        if product_name:
            stmt = stmt.where(ProductRow.name.ilike(f"%{product_name}%"))
        if brand:
            stmt = stmt.where(ProductRow.brand.ilike(f"%{brand}%"))
        if category:
            stmt = stmt.where(ProductRow.category == category)
    if disposition:
        stmt = stmt.where(ScanRow.disposition == disposition)
    if sha256:
        stmt = stmt.where(ScanRow.sha256 == sha256)
    if finalized is not None:
        stmt = stmt.where(ScanRow.finalized.is_(finalized))
    if has_rule7_flag is not None:
        stmt = stmt.where(ScanRow.has_rule7_flag.is_(has_rule7_flag))
    if date_from is not None:
        stmt = stmt.where(ScanRow.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(ScanRow.created_at <= date_to)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(ScanRow.created_at.desc()).limit(limit).offset(offset)
    rows = list(session.scalars(stmt))
    return rows, total


def stats(session: Session) -> dict:
    """Aggregate KPIs for the officer dashboard."""
    total = session.scalar(select(func.count()).select_from(ScanRow)) or 0
    by_disposition = dict(
        session.execute(select(ScanRow.disposition, func.count()).group_by(ScanRow.disposition)).all()
    )
    calibrated_count = session.scalar(
        select(func.count()).where(ScanRow.calibrated == "calibrated")
    ) or 0
    finalized_count = session.scalar(
        select(func.count()).where(ScanRow.finalized.is_(True))
    ) or 0

    # Most-flagged declarations: scans the JSON of flagged reports. Acceptable
    # at prototype scale; a denormalized flag-count table would be the next
    # step if this becomes a bottleneck at real volume.
    flag_counts: dict = {}
    flagged_rows = session.scalars(
        select(ScanRow).where(ScanRow.disposition == Status.POTENTIAL_NON_COMPLIANCE.value)
    )
    for row in flagged_rows:
        try:
            report = Report.model_validate_json(row.report_json)
        except Exception:
            continue
        for d in report.declarations:
            if d.status == Status.POTENTIAL_NON_COMPLIANCE:
                flag_counts[d.id] = flag_counts.get(d.id, 0) + 1
    most_flagged = sorted(flag_counts.items(), key=lambda kv: -kv[1])[:10]

    since = datetime.now(timezone.utc) - timedelta(days=30)
    daily_rows = session.execute(
        select(func.date(ScanRow.created_at), func.count())
        .where(ScanRow.created_at >= since)
        .group_by(func.date(ScanRow.created_at))
        .order_by(func.date(ScanRow.created_at))
    ).all()

    return {
        "total": total,
        "by_disposition": by_disposition,
        "most_flagged_declarations": [{"id": k, "count": v} for k, v in most_flagged],
        "scans_per_day": [{"date": str(d), "count": c} for d, c in daily_rows],
        "pct_calibrated": round(100 * calibrated_count / total, 1) if total else 0.0,
        "pct_finalized": round(100 * finalized_count / total, 1) if total else 0.0,
    }


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


def list_users(session: Session) -> List[User]:
    return list(session.scalars(select(User).order_by(User.email)))


def ensure_admin_user(session: Session, *, email: str, password_hash: str,
                      name: str = "Admin") -> Optional[User]:
    """First-boot convenience: create an admin with this email/password hash
    if no users exist yet at all. There's no sign-up flow, so a fresh
    database otherwise has no way to log in without a manual DB write.
    No-op (returns None) once any user exists, so it's safe to call on
    every startup with the env vars left set."""
    if list_users(session):
        return None
    return create_user(session, email=email, name=name, role="admin", pw_hash=password_hash)
