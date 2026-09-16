# SIH26034 — Legal Metrology Compliance Scanner (Metros)

Scan a packaged-commodity label and auto-check it against the **Legal Metrology
(Packaged Commodities) Rules, 2011**. Measures declaration font height in real
**millimetres** (ArUco scale card), validates every mandatory declaration, and
generates a detailed, clause-cited compliance report.

Metros is a web app with a server-side backend (see "OCR / label reading"
below for how a label photo is turned into text), and reports are stored
server-side. It reports **potential** non-compliance for officer
verification, with a measurement uncertainty on every millimetre figure —
decision-support, not a final legal finding.

- **Problem statement:** [`docs/problem-statement.md`](docs/problem-statement.md)
- **Architecture:** [`docs/architecture.md`](docs/architecture.md)
- **Report structure:** [`docs/report-spec.md`](docs/report-spec.md)
- **Deployment:** [`docs/deployment.md`](docs/deployment.md)
- **Pitch / PPT master:** [`docs/pitch.md`](docs/pitch.md)

## Stack

Python · OpenCV (`cv2.aruco`) · Tesseract OCR · FastAPI · SQLAlchemy · React +
Vite · WeasyPrint + python-docx · Docker Compose.

## Layout

```
docs/        problem statement, architecture, report spec, deployment, pitch, LMPC 2011 PDF
backend/
  core/      settings (env-sourced), typed errors, startup safety checks
  schemas/   canonical Report model (pydantic)
  vision/    scale recovery (ArUco -> mm/px + homography), measurement, OCR engines
  extract/   deterministic regex parsers (Rule 6 declarations)
  rules/     YAML catalog loader + deterministic engine
  reports/   JSON / HTML / PDF / DOCX renderer
  db/        SQLAlchemy models + repository (search, stats, audit log)
  api/       FastAPI endpoints + JWT/RBAC
  pipeline.py  image + OCR -> Report
  cli.py     single-scan runner (no server)
frontend/    React app (sign-in, scan, history, dashboard, report view)
rules/       lmpc-2011.yaml (rule catalog)
scripts/     calibration-card generator, user seeding
docker/      Dockerfiles
tests/       pytest suite (some tests require Tesseract installed; one
             requires PaddleOCR-VL, see below)
```

## Quick start (local)

```bash
make install            # venv + backend deps (works on Python 3.11–3.14+)
make test               # run the pytest suite
make card               # -> out/calibration_card.png (print at 100%)
make seed               # default users: officer@metroscan.gov / officer, admin@metroscan.gov / admin
make run                # API on http://localhost:8000  (docs at /docs)
make frontend-dev       # React app on http://localhost:5173
```

## Auth

Auth is **on by default** — sign in via `/auth/token` (seeded by
`make seed` / `scripts/seed_users.py`) to get a JWT; officer/admin/auditor
roles gate every route. For local dev without touching auth, set
`METROS_AUTH_DISABLED=1` — this is refused at startup when
`METROS_ENV=production`.

## OCR / label reading

Three ways to read a label:
- **Paste the text** — the UI's label-text field / CLI's `--label-file`; works
  everywhere, no extra install, and skips OCR entirely.
- **PaddleOCR-VL** (default, `METROS_OCR_ENGINE=paddleocr_vl`) — this
  experimental branch only, see the section below.
- **Tesseract OCR** — `make install-ocr`; the automatic fallback (or set
  `METROS_OCR_ENGINE=tesseract` to use it directly), so a scan never
  silently returns nothing.

Scale, panel-area, and letter-height measurement (Rule 7) are always done in
code (OpenCV geometry) — no model ever measures or decides compliance;
extraction is always the deterministic regex parsers in `backend/extract/`.

Single scan without the server:

```bash
python -m backend.cli scan photo.jpg \
    --label-file label.txt --marker-mm 40 --panel-cm2 250 --out-dir out/
```

## Docker

```bash
docker compose up --build     # api + frontend + postgres
```

See [`docs/deployment.md`](docs/deployment.md) for env vars, data storage, and
the rule-catalog hot-update process.

## Experimental branch: PaddleOCR-VL (local only)

`experiment/paddleocr-vl` (this branch) replaces nothing else about the app
-- it's a drop-in alternate label reader, entirely local, no internet or API
key. **It is never merged into `main`.**

### Setup

Built and tested against: Ubuntu 24.04, NVIDIA RTX 4070 Laptop (8GB VRAM,
compute capability 8.6), driver 580.173.02, CUDA 13.0.

```bash
make install-paddle     # paddlepaddle-gpu + paddleocr[doc-parser] -- see
                         # requirements-paddle.txt for the exact pinned
                         # commands (and the CPU-only alternative)
```

`METROS_OCR_ENGINE` (in `.env` or the environment) picks the engine:

```bash
METROS_OCR_ENGINE=paddleocr_vl   # default -- falls back to Tesseract automatically
METROS_OCR_ENGINE=tesseract      # use Tesseract directly, no PaddleOCR-VL attempt
```

`GET /health` reports which engine is configured and whether it's actually
available right now (`ocr_engine`, `ocr_engine_available`). Every report
also carries `extraction.backend_used` (which engine actually read that
scan) and, if PaddleOCR-VL fell back to Tesseract, a warning saying why.

Run the one real-model integration test (skipped by default, see
`tests/test_paddle_integration.py`):

```bash
pytest -m paddle
```

Compare engines on your own photos:

```bash
python scripts/compare_readers.py --photos-dir path/to/photos \
    --ground-truth path/to/ground_truth.json --out-csv out/compare.csv
```

### Known limitations

- **No documented VRAM minimum.** Neither PaddleOCR-VL doc page states one
  -- behavior on a smaller GPU, or CPU-only, is unverified by this branch.
- **No line/word boxes from PaddleOCR-VL.** Its documented output is
  block/paragraph-level, too coarse for Rule 7 (letter height) and Rule 8
  (placement). Those keep using Tesseract on the calibrated image via the
  existing marker-token fallback in `backend/pipeline.py` — unchanged from
  before this branch, and true regardless of which engine reads the
  declaration text.
- **No published speed benchmark**, on any hardware, from either doc page.
  Real numbers come from this branch's own model-load/per-image logging
  (`backend/vision/paddle_vl.py`) and `scripts/compare_readers.py` — not
  from PaddlePaddle's documentation.
- **CUDA 13.0 against a cu126-built wheel** (`requirements-paddle.txt`) is
  an assumption (NVIDIA driver backward compatibility), not a documented,
  tested combination.
- **Local testing only.** No Docker/Vercel/deployment changes were made or
  are planned on this branch.

## The moat — Rule 7 in millimetres

Letter-height compliance keys off the **area of the principal display panel
(cm²)** (Table-I, GSR 629(E), w.e.f. 01-01-2018). Metros recovers scale from a
printed ArUco card, measures panel area **and** glyph height in mm (each with an
uncertainty), and flags heights below the band minimum. No calibration marker ⇒
no millimetre verdict. See [`docs/architecture.md`](docs/architecture.md).
