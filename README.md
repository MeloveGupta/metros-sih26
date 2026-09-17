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
tests/       pytest suite (some tests require Tesseract installed)
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

Two ways to read a label:
- **Paste the text** — the UI's label-text field / CLI's `--label-file`; works
  everywhere, no extra install, and skips OCR entirely.
- **Tesseract OCR** — `make install-ocr`; used automatically to read text from
  photos when no label text is pasted, so a scan never silently returns
  nothing.

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

## Experimental branch: hosted PaddleOCR API

`experiment/paddleocr-vl` (this branch) is switching from local PaddleOCR-VL
inference (which needed a GPU and never returned a result within 5 minutes
in testing — see prior commits) to PaddleOCR's official hosted API, so the
backend can run on ordinary Render/Railway-style hosting with no GPU. Work
in progress; this section is rewritten once the hosted reader lands.
**Never merged into `main`.**

## The moat — Rule 7 in millimetres

Letter-height compliance keys off the **area of the principal display panel
(cm²)** (Table-I, GSR 629(E), w.e.f. 01-01-2018). Metros recovers scale from a
printed ArUco card, measures panel area **and** glyph height in mm (each with an
uncertainty), and flags heights below the band minimum. No calibration marker ⇒
no millimetre verdict. See [`docs/architecture.md`](docs/architecture.md).
