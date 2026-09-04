#!/usr/bin/env python3
"""Generate a printable ArUco calibration card for Metros (SIH26034).

The card is an ID-1 / CR80 rectangle (85.60 x 53.98 mm, ISO/IEC 7810) carrying a
single ArUco marker of a known, printed side length. Placed in the same plane as
a product declaration, the marker gives the vision pipeline a metric scale
(mm_per_pixel) and, via its four corners, a homography for perspective
correction -- with no personally identifiable information (unlike an Aadhaar or
other ID card).

Print at 100% / "actual size" (no fit-to-page scaling), then verify with a ruler
that the printed marker side matches --marker-mm before trusting measurements.

Usage:
    python scripts/gen_calibration_card.py               # A4 PNG at out/calibration_card.png
    python scripts/gen_calibration_card.py --dpi 600 --marker-mm 40 --out out/card.png
    python scripts/gen_calibration_card.py --dict DICT_5X5_50 --marker-id 7

Dependencies: opencv-contrib-python, numpy (both already in requirements.txt).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# ID-1 / CR80 card, ISO/IEC 7810.
CARD_W_MM = 85.60
CARD_H_MM = 53.98

# ISO 216 A4.
A4_W_MM = 210.0
A4_H_MM = 297.0


def mm_to_px(mm: float, dpi: int) -> int:
    """Convert millimetres to pixels at the given print resolution."""
    return int(round(mm * dpi / 25.4))


def resolve_dictionary(name: str) -> "cv2.aruco.Dictionary":
    """Look up a predefined ArUco dictionary by name, across OpenCV versions."""
    aruco = cv2.aruco
    const = getattr(aruco, name, None)
    if const is None:
        valid = [n for n in dir(aruco) if n.startswith("DICT_")]
        raise SystemExit(f"Unknown dictionary {name!r}. Valid names:\n  " + "\n  ".join(valid))
    # OpenCV >= 4.7 renamed Dictionary_get -> getPredefinedDictionary.
    if hasattr(aruco, "getPredefinedDictionary"):
        return aruco.getPredefinedDictionary(const)
    return aruco.Dictionary_get(const)


def render_marker(dictionary: "cv2.aruco.Dictionary", marker_id: int, side_px: int) -> np.ndarray:
    """Render one ArUco marker as a (side_px x side_px) uint8 image."""
    aruco = cv2.aruco
    # OpenCV >= 4.7 renamed drawMarker -> generateImageMarker.
    if hasattr(aruco, "generateImageMarker"):
        return aruco.generateImageMarker(dictionary, marker_id, side_px)
    img = np.zeros((side_px, side_px), dtype=np.uint8)
    return aruco.drawMarker(dictionary, marker_id, side_px, img, 1)


def build_card(dpi: int, marker_mm: float, dict_name: str, marker_id: int) -> np.ndarray:
    """Compose the full A4 sheet (white) with a centred CR80 card and marker."""
    if marker_mm <= 0 or marker_mm > min(CARD_W_MM, CARD_H_MM) - 8:
        raise SystemExit(
            f"--marker-mm must be >0 and leave >=4mm quiet zone on the card "
            f"(max {min(CARD_W_MM, CARD_H_MM) - 8:.1f} mm)."
        )

    page_w, page_h = mm_to_px(A4_W_MM, dpi), mm_to_px(A4_H_MM, dpi)
    card_w, card_h = mm_to_px(CARD_W_MM, dpi), mm_to_px(CARD_H_MM, dpi)
    marker_px = mm_to_px(marker_mm, dpi)

    # White page (BGR).
    page = np.full((page_h, page_w, 3), 255, dtype=np.uint8)

    # Centre the card on the page.
    cx0 = (page_w - card_w) // 2
    cy0 = (page_h - card_h) // 2
    cx1, cy1 = cx0 + card_w, cy0 + card_h

    black = (0, 0, 0)
    grey = (150, 150, 150)

    # Card outline + corner cut/crop marks.
    cv2.rectangle(page, (cx0, cy0), (cx1, cy1), black, 2)
    tick = mm_to_px(4, dpi)
    for (x, y) in [(cx0, cy0), (cx1, cy0), (cx0, cy1), (cx1, cy1)]:
        cv2.line(page, (x - tick, y), (x + tick, y), grey, 1)
        cv2.line(page, (x, y - tick), (x, y + tick), grey, 1)

    # Marker: left-justified inside the card with a quiet zone.
    quiet = mm_to_px(6, dpi)
    mx0 = cx0 + quiet
    my0 = cy0 + (card_h - marker_px) // 2
    marker = render_marker(resolve_dictionary(dict_name), marker_id, marker_px)
    page[my0:my0 + marker_px, mx0:mx0 + marker_px] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)

    # Labels to the right of the marker.
    tx = mx0 + marker_px + mm_to_px(5, dpi)
    ty = cy0 + mm_to_px(14, dpi)
    line_h = mm_to_px(7, dpi)
    scale = dpi / 300.0  # keep text legible across DPIs
    lines = [
        "Metros calibration card",
        f"{dict_name}  id={marker_id}",
        f"marker side = {marker_mm:.1f} mm",
        "Print at 100% (actual size).",
        "Verify with a ruler before use.",
    ]
    for i, text in enumerate(lines):
        weight = 2 if i == 0 else 1
        cv2.putText(page, text, (tx, ty + i * line_h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, black, weight, cv2.LINE_AA)

    return page


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpi", type=int, default=300, help="print resolution (default 300)")
    ap.add_argument("--marker-mm", type=float, default=40.0,
                    help="printed marker side length in mm (default 40)")
    ap.add_argument("--dict", dest="dict_name", default="DICT_4X4_50",
                    help="ArUco dictionary name (default DICT_4X4_50)")
    ap.add_argument("--marker-id", type=int, default=0, help="marker id (default 0)")
    ap.add_argument("--out", type=Path, default=Path("out/calibration_card.png"),
                    help="output PNG path (default out/calibration_card.png)")
    args = ap.parse_args(argv)

    if args.dpi < 72:
        raise SystemExit("--dpi must be >= 72 for a printable card.")

    page = build_card(args.dpi, args.marker_mm, args.dict_name, args.marker_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.out), page):
        raise SystemExit(f"Failed to write {args.out}")

    print(f"Wrote {args.out}  ({page.shape[1]}x{page.shape[0]} px @ {args.dpi} dpi)")
    print(f"Card {CARD_W_MM}x{CARD_H_MM} mm, marker {args.marker_mm} mm "
          f"({args.dict_name} id={args.marker_id}).")
    print("Print at 100% and confirm the marker side with a ruler before measuring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
