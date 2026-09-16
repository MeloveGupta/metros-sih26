# Deployment

Metros is a web app: the backend needs a database and local disk for
evidence storage. Label reading (Tesseract OCR) runs on-device, with no
outbound internet dependency. This doc covers running it locally and via
Docker Compose.

## System requirements

- Python 3.11–3.14 (backend), Node 20+ (frontend build)
- PostgreSQL 14+ in production (SQLite is fine for local dev — it's the
  default with no `DATABASE_URL` set)
- Tesseract OCR binary on `PATH` for the deterministic fallback reader
  (`apt-get install tesseract-ocr` / `brew install tesseract`) — optional
  locally, always installed in the Docker image
- WeasyPrint's native libs (cairo, pango, gdk-pixbuf) for PDF rendering —
  optional; without them, `GET /scans/{id}/report.pdf` returns 503 (the DOCX
  download still works)
- Docker + Docker Compose, if deploying that way

## Local setup

```bash
make install            # venv + backend deps
make install-ocr        # optional: Tesseract OCR (pytesseract)
make card                # -> out/calibration_card.png (print at 100%)
make seed                # seed officer@metroscan.gov / officer, admin@metroscan.gov / admin
make run                 # API on :8000
make frontend-dev        # frontend on :5173, proxies API calls to :8000
```

`scripts/seed_users.py` is the only way to create the first admin — there is
no sign-up flow. Re-run it with `--email --password --name --role` for
additional users, or use `POST /users` (admin-only) once one exists.

## Docker Compose

```bash
cp .env.example .env    # fill in JWT_SECRET at minimum
docker compose up --build
```

Brings up `api` (FastAPI + Uvicorn), `db` (Postgres 17), and `frontend`
(static build served by nginx, proxying `/auth`, `/scan`, `/scans`, `/stats`,
`/users`, `/health` to `api`). `api` waits for a Postgres health check before
starting — no manual ordering needed. Seed users the same way as local dev,
just against the container's DB:

```bash
docker compose exec api python -c "
from backend.api.security import hash_password
from backend.db.repository import create_user, get_user_by_email, init_db, make_engine, session_factory
engine = make_engine(); init_db(engine)
with session_factory(engine)() as s:
    create_user(s, email='admin@metroscan.gov', name='Admin', role='admin',
                pw_hash=hash_password('change-me'))
"
```

(`scripts/seed_users.py` itself isn't copied into the image — only
`backend/` and `rules/` are, to keep the image small.)

## Environment variables

| Variable | Default | Secret? | Notes |
|----------|---------|---------|-------|
| `METROS_ENV` | `development` | no | `production` refuses to start with `METROS_AUTH_DISABLED=1` or the default `JWT_SECRET` |
| `METROS_AUTH_DISABLED` | unset | no | `1` bypasses auth entirely — local dev only, refused in production |
| `DATABASE_URL` | `sqlite:///data/metroscan.db` | contains DB creds in Compose | Postgres in Compose: `postgresql+psycopg://...` |
| `UPLOADS_DIR` | `data/uploads` | no | evidence images + crops; a Docker volume in Compose |
| `JWT_SECRET` | `dev-insecure-secret` | **yes** | must be overridden for any real deployment — the default is publicly known |
| `JWT_ALG` | `HS256` | no | |
| `JWT_EXPIRE_MINUTES` | `480` | no | |
| `MARKER_SIZE_MM` | `40.0` | no | must match `scripts/gen_calibration_card.py --marker-mm`, or every mm figure is wrong |
| `MAX_CORNER_JITTER_PX` | `2.0` | no | calibration-quality gate |
| `MAX_EXTRAPOLATION_SIDES` | `4.0` | no | how far from the marker a measurement is still trusted |

## Data storage and backup

- **Database**: the canonical `Report` (declarations, font analysis,
  evidence metadata, officer actions — everything) is one JSON blob per scan
  in `ScanRow.report_json`, with a few denormalized columns for search. No
  migration framework: on a schema change in development, delete
  `data/metroscan.db` and restart; in Postgres, back it up like any other DB.
- **Evidence files**: original uploads and per-declaration crops live under
  `UPLOADS_DIR` (`data/uploads` by default, a named Docker volume in
  Compose) — back this up alongside the database, since `Evidence.images[].file`
  and `DeclarationFinding.evidence_crop` are paths into it, not embedded data.

## Rule catalog hot-update

`rules/lmpc-2011.yaml` is read by `load_catalog()` at request time, not at
process startup — editing it takes effect on the next scan with **no
redeploy or restart needed**. Always record `gazette` and `effective_from`
on any new/changed entry; never hardcode legal text in Python.

## Hosting notes

- Serve the frontend over **HTTPS** in production — some browser APIs the
  photo-capture inputs rely on (and any future live-camera work) are
  restricted to secure contexts, and it protects the JWT in transit either way.
- Label reading (Tesseract OCR) is entirely on-device — no outbound network
  dependency for it, unlike some earlier revisions of this app.
- `data/uploads` grows with every scan (originals + crops are never deleted)
  — plan storage and backups accordingly for real inspection volume.
