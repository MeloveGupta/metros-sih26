"""Command-line runner for a single scan (offline, no server).

Useful for the field/demo workflow and for generating report artifacts:

    python -m backend.cli scan photo.jpg --label-file label.txt --out-dir out/
    python -m backend.cli scan photo.jpg --marker-mm 40 --panel-cm2 250

If PaddleOCR is installed, `--label-*` may be omitted and text is read from the
image. Millimetre verdicts require a calibration marker in the photo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import cv2

from .core.errors import MetrosError
from .pipeline import run_scan
from .reports.render import render_docx, render_html, render_json
from .schemas.report import Product, Status
from .vision.ocr import ocr_from_text, paddle_ocr, tesseract_available, tesseract_ocr

_STATUS_MARK = {
    Status.COMPLIANT: "OK  ",
    Status.POTENTIAL_NON_COMPLIANCE: "FLAG",
    Status.NOT_DETECTED: "MISS",
    Status.NOT_ASSESSABLE: "N/A ",
    Status.NOT_APPLICABLE: "--  ",
}


def _print_summary(report) -> None:
    print(f"\nMetros report {report.ref_no or report.report_id}")
    print(f"  calibration : {report.calibration.verdict.value}"
          + (f" ({report.calibration.mm_per_pixel:.5f} mm/px)"
             if report.calibration.mm_per_pixel else ""))
    print(f"  disposition : {report.disposition.value}")
    print("  declarations:")
    for d in report.declarations:
        print(f"    [{_STATUS_MARK.get(d.status, '?')}] {d.label} ({d.clause_ref.clause})")
    if report.font_analysis.items:
        print("  font (Rule 7):")
        for i in report.font_analysis.items:
            h = (f"{i.height_mm.value:.2f}±{i.height_mm.uncertainty:.2f}mm"
                 if i.height_mm else "n/a")
            thr = f" vs {i.threshold_mm:.1f}mm" if i.threshold_mm is not None else ""
            print(f"    [{_STATUS_MARK.get(i.status, '?')}] {i.declaration_id}: {h}{thr}")
    if report.summary.required_actions:
        print("  officer actions required:")
        for a in report.summary.required_actions:
            print(f"    - {a}")


def _cmd_scan(args: argparse.Namespace) -> int:
    image = cv2.imread(args.image)
    if image is None:
        print(f"error: could not read image {args.image}", file=sys.stderr)
        return 2

    label_text: Optional[str] = None
    if args.label_file:
        label_text = Path(args.label_file).read_text(encoding="utf-8")
    elif args.label_text:
        label_text = args.label_text

    if label_text is not None:
        ocr = ocr_from_text(label_text)
    elif tesseract_available():
        ocr = tesseract_ocr(image)
    else:
        try:
            ocr = paddle_ocr(image)
        except MetrosError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    report = run_scan(
        image, ocr,
        marker_mm=args.marker_mm,
        dict_name=args.dict,
        product=Product(name=args.product) if args.product else None,
        panel_area_cm2=args.panel_cm2,
        molded=args.molded,
        image_file=Path(args.image).name,
        extract_backend="auto" if args.llm else "regex",
    )

    _print_summary(report)

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = report.report_id[:8]
        (out / f"{stem}.json").write_text(render_json(report), encoding="utf-8")
        (out / f"{stem}.html").write_text(render_html(report), encoding="utf-8")
        render_docx(report, out / f"{stem}.docx")
        print(f"\nwrote {stem}.json / .html / .docx to {out}/")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="metros", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan one product image")
    scan.add_argument("image")
    scan.add_argument("--label-text", help="label text (offline OCR bypass)")
    scan.add_argument("--label-file", help="file containing label text")
    scan.add_argument("--marker-mm", type=float, default=None,
                      help="printed marker side length in mm")
    scan.add_argument("--dict", default="DICT_4X4_50", help="ArUco dictionary")
    scan.add_argument("--panel-cm2", type=float, default=None,
                      help="principal display panel area (cm^2) for Rule 7 band")
    scan.add_argument("--molded", action="store_true",
                      help="declarations are blown/molded (higher thresholds)")
    scan.add_argument("--llm", action="store_true",
                      help="use the Claude extraction fast-path (needs `ant auth login`)")
    scan.add_argument("--product", help="product name")
    scan.add_argument("--out-dir", help="write JSON/HTML/DOCX report here")
    scan.set_defaults(func=_cmd_scan)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
