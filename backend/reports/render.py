"""Render a Report into JSON, HTML, PDF, and DOCX.

One canonical `Report` (schemas.report) drives every format, so they never
diverge. HTML is the intermediate for PDF (WeasyPrint); DOCX is built
directly with python-docx, mirroring the same section order. Both PDF and
DOCX rendering are optional at runtime: if the underlying library isn't
available, the render function raises a clear error rather than failing
silently.
"""
from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..core import storage
from ..core.config import get_settings
from ..core.errors import MetrosError
from ..schemas.report import Report

_STATUS_LABEL = {
    "compliant": "COMPLIANT",
    "potential_non_compliance": "POTENTIAL NON-COMPLIANCE",
    "not_detected": "NOT DETECTED",
    "not_assessable": "NOT ASSESSABLE",
    "not_applicable": "NOT APPLICABLE",
}


def _data_uri(rel_path: str) -> Optional[str]:
    """Base64-inline a stored evidence file (local disk or Supabase Storage,
    via core.storage) so PDF/DOCX rendering doesn't need file-system access
    configuration (WeasyPrint) or a second pass (DOCX)."""
    try:
        raw = storage.read_bytes(rel_path)
    except FileNotFoundError:
        return None
    mime = mimetypes.guess_type(rel_path)[0] or "image/png"
    data = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{data}"


def _evidence_photos(report: Report) -> List[dict]:
    out = []
    for img in report.evidence.images:
        uri = _data_uri(img.file)
        if uri:
            out.append({"role": img.role, "file": img.file, "data_uri": uri})
    return out


def _evidence_crops(report: Report) -> List[dict]:
    out = []
    for group in (report.declarations, report.placement, report.readability):
        for d in group:
            if not d.evidence_crop:
                continue
            uri = _data_uri(d.evidence_crop)
            if uri:
                out.append({"label": d.label, "data_uri": uri})
    return out


def render_json(report: Report, indent: int = 2) -> str:
    """Canonical machine-readable record."""
    return report.model_dump_json(indent=indent, by_alias=True)


def _environment(template_dir: Optional[Path]) -> Environment:
    template_dir = template_dir or get_settings().report_template_dir
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )


def render_html(report: Report, template_dir: Optional[Path] = None) -> str:
    env = _environment(template_dir)
    template = env.get_template("report.html.j2")
    return template.render(r=report, evidence_photos=_evidence_photos(report),
                           evidence_crops=_evidence_crops(report))


def render_pdf(report: Report, out_path: Path, template_dir: Optional[Path] = None) -> Path:
    """Render to PDF via WeasyPrint. Raises if WeasyPrint is unavailable."""
    try:
        from weasyprint import HTML  # heavy native deps; import lazily
    except Exception as exc:  # ImportError or native lib load failure
        raise MetrosError(
            "PDF rendering requires WeasyPrint and its native libraries "
            "(cairo, pango). Install per requirements.txt, or use "
            f"render_html. Underlying error: {exc}"
        ) from exc

    html = render_html(report, template_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(out_path))
    return out_path


def _status_text(status) -> str:
    return status.value.replace("_", " ")


def _add_findings_table(doc, rows, headers) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
    for values in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, values):
            cell.text = text


def render_docx(report: Report, out_path: Path) -> Path:
    """Render to an editable DOCX. Mirrors the PDF's section order/content
    from the same `Report` object, so the two formats never diverge."""
    try:
        from docx import Document  # heavy-ish; import lazily like render_pdf
        from docx.shared import Inches
    except Exception as exc:  # ImportError
        raise MetrosError(
            "DOCX rendering requires python-docx. Install per requirements.txt. "
            f"Underlying error: {exc}"
        ) from exc

    doc = Document()

    doc.add_heading("METROS", level=0)
    doc.add_paragraph(f"Legal Metrology compliance report — "
                      f"{report.ref_no or report.report_id[:8]}")
    doc.add_paragraph(f"Generated: {report.generated_at}")

    doc.add_heading(f"Disposition: {_status_text(report.disposition).upper()}", level=1)
    doc.add_paragraph(
        "Decision-support. Potential non-compliance flagged for officer verification. "
        "Not a final legal finding; physical verification required for enforcement."
    )

    if report.extraction:
        reader = {"tesseract": "Tesseract OCR",
                 "label_text": "pasted label text",
                 "gemini": "Gemini (vision)"}.get(
                     report.extraction.backend_used, "Tesseract OCR")
        doc.add_paragraph(f"Reader: {reader}")
        if report.extraction.gemini_model_used:
            tokens = (f"in={report.extraction.gemini_input_tokens}, "
                     f"out={report.extraction.gemini_output_tokens}, "
                     f"thought={report.extraction.gemini_thought_tokens}")
            doc.add_paragraph(f"Gemini model: {report.extraction.gemini_model_used} ({tokens})")
        for w in report.extraction.warnings:
            doc.add_paragraph(f"Warning: {w}")

    if report.product.name or report.product.brand:
        doc.add_heading("Product", level=1)
        doc.add_paragraph(f"Name: {report.product.name or '-'}")
        doc.add_paragraph(f"Brand: {report.product.brand or '-'}")
        doc.add_paragraph(f"Category: {report.product.category or '-'}")
        doc.add_paragraph(f"Source: {report.product.source or '-'}")

    doc.add_heading("01 Declarations · Rule 6", level=1)
    _add_findings_table(
        doc,
        [(d.label, d.clause_ref.clause, d.extracted or "not found on pack", _status_text(d.status))
         for d in report.declarations],
        ["Declaration", "Clause", "Extracted", "Status"],
    )

    doc.add_heading("02 Letter height · Rule 7", level=1)
    cal = report.calibration
    cal_line = f"Calibration: {_status_text(cal.verdict)}"
    if cal.mm_per_pixel:
        cal_line += f" · {cal.mm_per_pixel:.5f} mm/px"
    if cal.corner_jitter_px is not None:
        cal_line += f" · corner jitter {cal.corner_jitter_px:.2f}px"
    if cal.reason:
        cal_line += f" · {cal.reason}"
    doc.add_paragraph(cal_line)

    fa = report.font_analysis
    if fa.panel_area_cm2 and fa.table_i_band:
        doc.add_paragraph(
            f"Principal display panel area {fa.panel_area_cm2.value:.1f} ± "
            f"{fa.panel_area_cm2.uncertainty:.1f} cm² → Table-I band "
            f"{fa.table_i_band.area_band}, minimum letter height "
            f"{fa.table_i_band.min_height_mm} mm."
        )
    if fa.items:
        _add_findings_table(
            doc,
            [(
                i.declaration_id,
                f"{i.height_mm.value:.2f} ± {i.height_mm.uncertainty:.2f} mm" if i.height_mm else "-",
                f"{i.threshold_mm:.1f} mm" if i.threshold_mm is not None else "-",
                _status_text(i.status),
                i.reason or "",
            ) for i in fa.items],
            ["Label text", "Measured height", "Threshold", "Status", "Reason"],
        )
    else:
        doc.add_paragraph("No letter-height measurement (no Metros card in frame, or uncalibrated).")

    if report.placement:
        doc.add_heading("03 Placement · Rule 8", level=1)
        _add_findings_table(
            doc,
            [(p.label, p.clause_ref.clause, p.note or "", _status_text(p.status))
             for p in report.placement],
            ["Item", "Clause", "Note", "Status"],
        )

    if report.readability:
        doc.add_heading("04 Readability · Rule 9", level=1)
        _add_findings_table(
            doc,
            [(p.label, p.clause_ref.clause, p.note or "", _status_text(p.status))
             for p in report.readability],
            ["Item", "Clause", "Note", "Status"],
        )

    if report.officer_actions:
        doc.add_heading("05 Officer verification", level=1)
        _add_findings_table(
            doc,
            [(
                a.declaration_id,
                "verified compliant" if a.action == "verified_compliant" else "issue confirmed",
                a.reason or "-",
            ) for a in report.officer_actions],
            ["Item", "Decision", "Officer note"],
        )
        finalized_line = f"Finalized by {report.finalized_by or '-'}"
        if report.finalized_at:
            finalized_line += f" on {report.finalized_at}"
        doc.add_paragraph(finalized_line + ".")
    elif report.summary.required_actions:
        doc.add_heading("05 Officer actions required", level=1)
        for a in report.summary.required_actions:
            doc.add_paragraph(a, style="List Bullet")
        doc.add_paragraph("Not yet verified by an officer.")

    doc.add_heading("06 Evidence & legal basis", level=1)
    doc.add_paragraph(f"Original file: {report.evidence.original.file}")
    doc.add_paragraph(
        f"Resolution: {report.evidence.original.width} × {report.evidence.original.height} px"
    )
    doc.add_paragraph(f"SHA-256: {report.evidence.original.sha256}")
    for img in report.evidence.images:
        try:
            raw = storage.read_bytes(img.file)
        except FileNotFoundError:
            continue
        doc.add_paragraph(f"Photo ({img.role}):")
        doc.add_picture(io.BytesIO(raw), width=Inches(3.0))
    for group in (report.declarations, report.placement, report.readability):
        for d in group:
            if not d.evidence_crop:
                continue
            try:
                raw = storage.read_bytes(d.evidence_crop)
            except FileNotFoundError:
                continue
            doc.add_paragraph(f"Evidence crop -- {d.label}:")
            doc.add_picture(io.BytesIO(raw), width=Inches(2.0))
    if report.legal_basis.get("statute"):
        doc.add_paragraph(f"Statute: {report.legal_basis['statute']}")
    doc.add_paragraph(f"Rule catalog: {report.rule_catalog.version}")
    doc.add_paragraph(report.evidence.integrity_note)
    doc.add_paragraph(report.limitations)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
