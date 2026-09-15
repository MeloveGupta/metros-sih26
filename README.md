# SIH26034 — Legal Metrology Compliance Scanner (Metros)

Scan a packaged-commodity label and auto-check it against the **Legal Metrology
(Packaged Commodities) Rules, 2011**. Measures declaration font height in real
**millimetres** (ArUco scale card), validates every mandatory declaration, and
generates a detailed, clause-cited compliance report.

Metros is an **online web app**: label photos are sent to Anthropic's API for
the AI reader (see "OCR / label reading" below), and reports are stored server
-side. It reports **potential** non-compliance for officer verification, with
a measurement uncertainty on every millimetre figure — decision-support, not
a final legal finding.

- **Problem statement:** [`docs/problem-statement.md`](docs/problem-statement.md)
- **Architecture:** [`docs/architecture.md`](docs/architecture.md)
- **Report structure:** [`docs/report-spec.md`](docs/report-spec.md)
- **Deployment:** [`docs/deployment.md`](docs/deployment.md)
- **Pitch / PPT master:** [`docs/pitch.md`](docs/pitch.md)

## Stack

Python · OpenCV (`cv2.aruco`) · Claude (Anthropic API) + Tesseract OCR fallback
· FastAPI · SQLAlchemy · React + Vite · WeasyPrint + python-docx · Docker
Compose.

## Layout

```
docs/        problem statement, architecture, report spec, deployment, pitch, LMPC 2011 PDF
backend/
  core/      settings (env-sourced), typed errors, startup safety checks
  schemas/   canonical Report model (pydantic)
  vision/    scale recovery (ArUco -> mm/px + homography), measurement, OCR fallback
  extract/   AI reader (Claude) + deterministic regex parsers (Rule 6 declarations)
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
tests/       pytest suite (153 tests; some require Tesseract installed)
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

Three ways to read a label, tried in this order:
- **AI reader (default)** — `make install-llm` + `ANTHROPIC_API_KEY` in `.env`.
  Reads label photos directly via the Claude API (Messages API, vision).
  Requires an API key; there is no OAuth/subscription-token path.
- **Paste the text** — the UI's label-text field / CLI's `--label-file`; works
  everywhere, no extra install, and skips OCR entirely.
- **Tesseract OCR fallback** — `make install-ocr`; used automatically when no
  API key is set or the Claude call fails, so a scan never silently returns
  nothing.

Scale, panel-area, and letter-height measurement (Rule 7) are always done in
code (OpenCV geometry) — the AI reader never measures or decides compliance.

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

## The moat — Rule 7 in millimetres

Letter-height compliance keys off the **area of the principal display panel
(cm²)** (Table-I, GSR 629(E), w.e.f. 01-01-2018). Metros recovers scale from a
printed ArUco card, measures panel area **and** glyph height in mm (each with an
uncertainty), and flags heights below the band minimum. No calibration marker ⇒
no millimetre verdict. See [`docs/architecture.md`](docs/architecture.md).
