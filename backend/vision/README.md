# vision — the moat

Scale recovery + OCR + glyph-to-millimetre measurement.

- `scale` — detect the ArUco calibration card, compute mm_per_pixel + a
  corner-jitter quality signal via independent re-detections.
- `ocr` — `select_ocr_engine()` runs Tesseract (word-level boxes) when no
  label text is pasted; pasted label text skips OCR entirely. On this
  experimental branch, a vision-based Gemini reader lives at the
  *extraction* layer instead (`backend/extract/gemini_reader.py`, not
  here) — it reads declaration text directly off the photos. Either way,
  Rule 7/8 always get their boxes from Tesseract on the calibrated image
  (see `pipeline.py`'s marker-token fallback), regardless of which path
  read the declaration text.
- `measure` / `glyphs` — per-glyph pixel boxes x mm_per_pixel -> glyph_mm;
  Rule 7 height + width-ratio checks.
- `card` — the calibration card's own geometry, used to exclude its printed
  text from Rule 7 measurement.
- `quality` — blur/glare heuristics that gate readability (Rule 9).

Deterministic. No model measures anything. This is the differentiator.
