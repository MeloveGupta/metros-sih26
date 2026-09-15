# vision — the moat

Scale recovery + OCR fallback + glyph-to-millimetre measurement.

- `scale` — detect the ArUco calibration card, compute mm_per_pixel + a
  corner-jitter quality signal via independent re-detections.
- `ocr` — Tesseract is the primary OCR fallback (used when the AI reader has
  no key or its call fails); PaddleOCR is a last-resort fallback if Tesseract
  isn't installed. The AI reader (Claude) reads images directly and doesn't
  go through this module at all.
- `measure` / `glyphs` — per-glyph pixel boxes x mm_per_pixel -> glyph_mm;
  Rule 7 height + width-ratio checks.
- `card` — the calibration card's own geometry, used to exclude its printed
  text from Rule 7 measurement.
- `quality` — blur/glare heuristics that gate readability (Rule 9).

Deterministic. No LLM measures anything. This is the differentiator.
