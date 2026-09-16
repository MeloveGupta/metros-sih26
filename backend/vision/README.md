# vision — the moat

Scale recovery + OCR + glyph-to-millimetre measurement.

- `scale` — detect the ArUco calibration card, compute mm_per_pixel + a
  corner-jitter quality signal via independent re-detections.
- `ocr` — `select_ocr_engine()` picks and runs the configured engine
  (`METROS_OCR_ENGINE`): Tesseract (word-level boxes) or, on this
  experimental branch, PaddleOCR-VL (`paddle_vl.py`, text only -- falls
  back to Tesseract automatically if unavailable/fails). Rule 7/8 always
  get their boxes from Tesseract on the calibrated image regardless of
  which engine read the declaration text (see `pipeline.py`'s marker-token
  fallback) — PaddleOCR-VL's own boxes are block-level, too coarse to use.
- `measure` / `glyphs` — per-glyph pixel boxes x mm_per_pixel -> glyph_mm;
  Rule 7 height + width-ratio checks.
- `card` — the calibration card's own geometry, used to exclude its printed
  text from Rule 7 measurement.
- `quality` — blur/glare heuristics that gate readability (Rule 9).

Deterministic. No model measures anything. This is the differentiator.
