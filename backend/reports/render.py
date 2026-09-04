"""Render a Report into JSON, HTML, PDF, and DOCX.

One canonical `Report` (schemas.report) drives every format, so they never
diverge. HTML is the intermediate for PDF (WeasyPrint). DOCX is built directly
PDF rendering is optional at runtime: if WeasyPrints native
stack is unavailable, `render_pdf` raises a clear error rather than failing
silently.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

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
    return template.render(r=report)


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
