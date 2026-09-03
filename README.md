# SIH26034 — Legal Metrology Compliance Scanner

Scan a packaged-commodity label and auto-check it against the **Legal Metrology
(Packaged Commodities) Rules, 2011**. Measures declaration font height in real
millimetres (ArUco scale marker), validates every mandatory declaration, and
generates enforcement-ready compliance reports.

- **Problem statement:** [`docs/problem-statement.md`](docs/problem-statement.md)
- **Architecture:** [`docs/architecture.md`](docs/architecture.md)

## Stack

Python · OpenCV (`cv2.aruco`) · PaddleOCR · spaCy · FastAPI · PostgreSQL ·
MinIO · React + Vite · WeasyPrint + python-docx · Docker Compose.

## Layout

```
docs/        problem statement + architecture
backend/     FastAPI app — vision, extract, rules, reports, api, db
frontend/    React dashboard + in-field capture (PWA)
rules/       YAML rule catalog (LMPC Rules 2011)
scripts/     dev + data-capture helpers
docker/      Dockerfiles
tests/       test suite
```

## Quick start

```bash
docker compose up          # API + PostgreSQL + MinIO + frontend
```

Runs fully offline. An LLM fast-path activates only when an API key is present
in the environment.

## Build order

1. `backend/vision` — ArUco + PaddleOCR + glyph→mm measurement *(the moat)*
2. `rules/` + `backend/rules` — YAML catalog + deterministic engine
3. `backend/api` — upload → verdict
4. `backend/reports` — PDF + DOCX
5. `frontend/` — dashboard + search
6. auth + db + docker
