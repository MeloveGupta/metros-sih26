"""Tests for the scan repository (in-memory SQLite)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.db.repository import (
    append_audit,
    get_report,
    init_db,
    make_engine,
    save_report,
    search_scans,
    session_factory,
)
from backend.schemas.report import (
    Evidence,
    OriginalImage,
    Product,
    Report,
    RuleCatalogInfo,
    Status,
)


def _report(report_id: str, name: str, disposition: Status) -> Report:
    return Report(
        report_id=report_id,
        ref_no=f"MS-{report_id}",
        generated_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        rule_catalog=RuleCatalogInfo(version="2011", hash="sha256:x"),
        disposition=disposition,
        product=Product(name=name, brand="Acme"),
        evidence=Evidence(original=OriginalImage(file="a.jpg", sha256="sha256:h1")),
    )


@pytest.fixture
def session():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    Session = session_factory(engine)
    with Session() as s:
        yield s


def test_save_and_get_roundtrip(session):
    rep = _report("r1", "Masala Chips", Status.COMPLIANT)
    save_report(session, rep, created_by="officer-1")
    loaded = get_report(session, "r1")
    assert loaded is not None
    assert loaded.product.name == "Masala Chips"
    assert loaded.disposition == Status.COMPLIANT


def test_search_by_disposition_and_name(session):
    save_report(session, _report("r1", "Chips", Status.COMPLIANT))
    save_report(session, _report("r2", "Biscuits", Status.POTENTIAL_NON_COMPLIANCE))

    flagged = search_scans(session, disposition="potential_non_compliance")
    assert len(flagged) == 1 and flagged[0].id == "r2"

    by_name = search_scans(session, product_name="chip")
    assert len(by_name) == 1 and by_name[0].id == "r1"


def test_audit_log(session):
    append_audit(session, action="override", user_id="officer-1",
                 target="r1/mrp", reason="verified physically")
    from backend.db.models import AuditLog
    rows = session.query(AuditLog).all()
    assert len(rows) == 1 and rows[0].action == "override"


def test_idempotent_init_db():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    init_db(engine)  # must not raise
