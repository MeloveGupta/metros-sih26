# SIH26034 — Legal Metrology Compliance Scanner (MetroScan)

Scan a packaged-commodity label and auto-check it against the **Legal Metrology
(Packaged Commodities) Rules, 2011**. Measures declaration font height in real
**millimetres** (ArUco scale card), validates every mandatory declaration, and
generates a detailed, clause-cited compliance report — offline.

It reports **potential** non-compliance for officer verification, with a
measurement uncertainty on every millimetre figure. It is decision-support, not
a final legal finding.

- **Problem statement:** [`docs/problem-statement.md`](docs/problem-statement.md)
- **Architecture:** [`docs/architecture.md`](docs/architecture.md)
- **Report structure:** [`docs/report-spec.md`](docs/report-spec.md)
- **Pitch / PPT master:** [`docs/pitch.md`](docs/pitch.md)

## Stack

Python · OpenCV (`cv2.aruco`) · PaddleOCR · FastAPI · SQLAlchemy · React + Vite ·
WeasyPrint + python-docx · Docker Compose.

## Layout

```
docs/        problem statement, architecture, report spec, pitch, LMPC 2011 PDF
backend/
  core/      config + typed errors
  schemas/   canonical Report model (pydantic)
  vision/    scale recovery (ArUco -> mm/px + homography), measurement, OCR
  extract/   offline regex field parsers (Rule 6 declarations)
  rules/     YAML catalog loader + deterministic engine
  reports/   JSON / HTML / PDF / DOCX renderer
  db/        SQLAlchemy models + repository
  api/       FastAPI endpoints + JWT/RBAC
  pipeline.py  image + OCR -> Report
  cli.py     offline single-scan runner
frontend/    React dashboard (sign-in, scan, report view)
rules/       lmpc-2011.yaml (rule catalog)
scripts/     calibration-card generator, user seeding
docker/      Dockerfiles
tests/       pytest suite (38 tests)
```

## Quick start (local)

```bash
make install            # venv + CORE backend deps (works on Python 3.11–3.14+)
make test               # 42 tests
make card               # -> out/calibration_card.png (print at 100%)
make seed               # default users: officer@metroscan.gov / officer
make run                # API on http://localhost:8000  (docs at /docs)
make frontend-dev       # React app on http://localhost:5173
```

**OCR is optional and not in core deps** (PaddleOCR has no wheels on newer
Python). Read label text three ways:
- paste it (`--label-text` / the UI's label field) — works everywhere, no extra install;
- `make install-llm` + `ant auth login` — Claude extractor, no API key;
- `make install-ocr` — on-device PaddleOCR (needs Python ≈3.12; use a separate venv).

Offline single scan without the server:

```bash
python -m backend.cli scan photo.jpg \
    --label-file label.txt --marker-mm 40 --panel-cm2 250 --out-dir out/
```

## Docker

```bash
docker compose up --build     # api + frontend + postgres + minio
```

## The moat — Rule 7 in millimetres

Letter-height compliance keys off the **area of the principal display panel
(cm²)** (Table-I, GSR 629(E), w.e.f. 01-01-2018). MetroScan recovers scale from a
printed ArUco card, measures panel area **and** glyph height in mm (each with an
uncertainty), and flags heights below the band minimum. No calibration marker ⇒
no millimetre verdict. See [`docs/architecture.md`](docs/architecture.md).
