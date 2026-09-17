#!/usr/bin/env python3
"""Compare OCR engines (Tesseract vs. PaddleOCR-VL) on real pack photos.

Usage:
    python scripts/compare_readers.py --photos-dir path/to/photos \\
        --ground-truth path/to/ground_truth.json --out-csv out/compare.csv

For each photo x each engine: reads the image, extracts declarations with
the existing deterministic regex parsers, and compares against a
hand-written ground truth. Prints a summary table and writes a CSV with one
row per (photo, engine).

Filling in a ground truth file for 20 real packs:
1. Photograph front + back of each pack (or just the principal display panel).
2. For each photo, list every declaration actually printed on that pack and
   its exact text, e.g.:
       {
         "pack01_front.jpg": {
           "mrp": "MRP Rs. 45.00 (incl. of all taxes)",
           "net_quantity": "Net Qty 90 g",
           "mfg_date": "Mfg Aug 2026"
         },
         ...
       }
   Only include declarations that are actually printed and legible in that
   specific photo -- an engine correctly not detecting an absent declaration
   should not count as a miss.
3. See scripts/compare_readers.example.json for a minimal worked example.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.extract.dispatch import extract_declarations  # noqa: E402
from backend.rules.catalog import load_catalog  # noqa: E402
from backend.vision.ocr import select_ocr_engine  # noqa: E402

_ENGINES = ("tesseract", "paddleocr_api")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _value_matches(extracted: str, expected: str) -> bool:
    a, b = _normalize(extracted), _normalize(expected)
    return bool(a) and bool(b) and (a in b or b in a)


def compare(photos_dir: Path, ground_truth: Dict[str, Dict[str, str]],
           engines: List[str]) -> List[dict]:
    catalog = load_catalog()
    rows = []

    for filename, expected in ground_truth.items():
        path = photos_dir / filename
        img = cv2.imread(str(path))
        if img is None:
            print(f"warning: could not read {path}, skipping", file=sys.stderr)
            continue

        for engine in engines:
            t0 = time.monotonic()
            ocrs, backend_used, warning = select_ocr_engine([img], None, engine=engine)
            elapsed = time.monotonic() - t0

            if backend_used != engine:
                print(f"warning: {filename} requested {engine!r} but got "
                     f"{backend_used!r} ({warning}) -- recording under "
                     f"{backend_used!r}, not {engine!r}", file=sys.stderr)

            outcome = extract_declarations(ocrs[0].text, catalog)
            fields = {f.id: f for f in outcome.fields}

            detected = sum(1 for decl_id in expected if fields.get(decl_id) and fields[decl_id].present)
            matched = sum(
                1 for decl_id, exp_val in expected.items()
                if fields.get(decl_id) and fields[decl_id].present
                and fields[decl_id].value and _value_matches(fields[decl_id].value, exp_val)
            )
            rows.append({
                "photo": filename,
                "engine": backend_used,
                "declarations_expected": len(expected),
                "declarations_detected": detected,
                "values_matched": matched,
                "seconds": round(elapsed, 2),
            })
    return rows


def _print_table(rows: List[dict]) -> None:
    by_engine: Dict[str, List[dict]] = {}
    for r in rows:
        by_engine.setdefault(r["engine"], []).append(r)

    header = f"{'engine':<14}{'photos':>8}{'detected/expected':>20}{'values matched':>16}{'avg sec/image':>16}"
    print(header)
    print("-" * len(header))
    for engine, engine_rows in sorted(by_engine.items()):
        n = len(engine_rows)
        expected = sum(r["declarations_expected"] for r in engine_rows)
        detected = sum(r["declarations_detected"] for r in engine_rows)
        matched = sum(r["values_matched"] for r in engine_rows)
        avg_sec = sum(r["seconds"] for r in engine_rows) / n if n else 0.0
        print(f"{engine:<14}{n:>8}{f'{detected}/{expected}':>20}{matched:>16}{avg_sec:>16.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photos-dir", required=True, type=Path)
    ap.add_argument("--ground-truth", required=True, type=Path)
    ap.add_argument("--engines", default=",".join(_ENGINES),
                    help=f"comma-separated engines to compare (default: {','.join(_ENGINES)})")
    ap.add_argument("--out-csv", type=Path, default=None)
    args = ap.parse_args()

    ground_truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]

    rows = compare(args.photos_dir, ground_truth, engines)
    if not rows:
        print("no photos compared -- check --photos-dir and the ground-truth filenames", file=sys.stderr)
        return 1

    _print_table(rows)

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {args.out_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
