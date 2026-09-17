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

Three ways to read a label:
- **Paste the text** — the UI's label-text field / CLI's `--label-file`; works
  everywhere, no extra install, and skips OCR entirely.
- **Tesseract OCR** — `make install-ocr`; used automatically to read text from
  photos when no label text is pasted, so a scan never silently returns
  nothing.
- **Gemini vision** (this experimental branch only, see below) — reads
  declarations straight off the photos when `METROS_OCR_ENGINE=gemini` and
  `GEMINI_API_KEY` is set; falls back to Tesseract otherwise.

Scale, panel-area, and letter-height measurement (Rule 7) are always done in
code (OpenCV geometry) — no model ever measures or decides compliance.
Extraction is the deterministic regex parsers in `backend/extract/` by
default; on this branch, Gemini's own extracted values still pass through
the same deterministic format validators and reconciliation logic before
anything is scored.

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

## Experimental branch: Gemini free tier

`experiment/paddleocr-vl` (this branch) reads labels via Google Gemini's
free tier (Google AI Studio) instead of PaddleOCR's hosted API — no GPU, no
Anthropic/Baidu/Hugging Face dependency. **Never merged into `main`.**

Gemini reads declarations straight off the photos (vision), bypassing OCR +
regex for what it can read; a deterministic rule engine and format
validators still have the final say — Gemini only extracts, it never
decides compliance, and its own `format_pass` claim is never trusted (see
`backend/extract/dispatch.py`'s reconciliation functions).

**Setup:**
```bash
pip install -r requirements.txt   # includes google-genai
```
Get a key at https://aistudio.google.com/apikey, then set in `.env`:
```
METROS_OCR_ENGINE=gemini          # default on this branch
GEMINI_API_KEY=...
METROS_GEMINI_MODEL=              # optional override, default gemini-3.1-flash-lite
METROS_GEMINI_FALLBACK_MODEL=     # optional override, default gemini-3.8-flash
```
Without a key configured, `METROS_OCR_ENGINE=gemini` transparently falls
back to the Tesseract path — a scan is never silently empty.

**`/health`** reports `gemini_api_key_configured` (bool, never the key
itself) plus the two configured model IDs.

**Free-tier limits:** no fixed numbers are published by Google — rate
limits depend on your Google AI Studio project's usage tier. Check your
project's live quota at https://aistudio.google.com. On a 429/quota error,
Metros backs off once with jitter and retries the same model, then tries
the fallback model once, then falls back to Tesseract + regex with a
visible warning (`extraction.warnings`) — never a hard failure.

**Tests:**
```bash
make test                    # mocked Gemini tests run by default, no key needed
GEMINI_API_KEY=... pytest -m gemini_live   # one real 2-photo scan
python scripts/compare_readers.py --photos-dir ... --ground-truth ...  # includes a gemini:<model-id> engine
```

**Known limitations:**
- The Interactions API (`client.interactions.create`) exposes token usage
  via `interaction.usage.total_{input,output,thought}_tokens`, confirmed
  against the installed `google-genai` SDK's own type stubs — read
  defensively (`getattr(..., None)`) in case a future SDK version reshapes
  `Usage`.
- `max_output_tokens` is a hard cutoff (including Gemini's own "thinking"
  tokens); a very verbose/malformed model response can still fail JSON
  parsing and trigger the OCR/regex fallback.
- One request per scan (all photos together) keeps this within the free
  tier's per-request limits, but a scan with many photos still counts as
  one request's worth of image tokens, which can be substantial.
- Results are cached on disk by the combined image-set SHA-256
  (`data/gemini_cache/`) so re-scanning the same photos never spends quota
  twice — but this means editing `rules/lmpc-2011.yaml`'s declaration list
  and re-scanning the *same* photos won't re-query Gemini either; delete the
  cache file (or the whole `data/gemini_cache/` directory) to force a fresh
  read.

**Data note:** free-tier prompts may be used by Google to improve its
products; use demo packs only. Production would use a self-hosted open
model.

## The moat — Rule 7 in millimetres

Letter-height compliance keys off the **area of the principal display panel
(cm²)** (Table-I, GSR 629(E), w.e.f. 01-01-2018). Metros recovers scale from a
printed ArUco card, measures panel area **and** glyph height in mm (each with an
uncertainty), and flags heights below the band minimum. No calibration marker ⇒
no millimetre verdict. See [`docs/architecture.md`](docs/architecture.md).
