# vision — the moat

Scale recovery + OCR + glyph-to-millimetre measurement.

- `scale` — detect ArUco marker, compute mm_per_pixel.
- `ocr` — PaddleOCR text + per-character pixel boxes.
- `measure` — glyph_px x mm_per_pixel -> glyph_mm; Rule 7 check.

Deterministic. No LLM. This is the differentiator — build first.
