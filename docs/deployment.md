# Deployment

Metros is a web app: the backend needs a database and local disk (or a
persistent volume) for evidence storage. The label reader is on-device
Tesseract for now (a vision-based Gemini reader, no GPU needed, is being
added back on this experimental branch). This doc covers running it
locally, via Docker Compose, and deploying to Vercel (frontend) + Render or
Railway (backend).

## System requirements

- Python 3.11–3.14 (backend), Node 20+ (frontend build)
- PostgreSQL 14+ in production (SQLite is fine for local dev — it's the
  default with no `DATABASE_URL` set)
- Tesseract OCR binary on `PATH` for the label reader (`apt-get install
  tesseract-ocr` / `brew install tesseract`) — optional locally, always
  installed in the Docker image
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

## Deploying: Vercel (frontend) + Render or Railway (backend)

No GPU needed anywhere in this path.

**1. Backend (Render or Railway), from `docker/api.Dockerfile`:**
- Create a new **Web Service** from this repo, Dockerfile path
  `docker/api.Dockerfile`, build context the repo root.
- Add a **PostgreSQL** database (Render's or Railway's managed Postgres
  add-on) and set `DATABASE_URL` to the connection string it gives you
  (`postgresql+psycopg://...` — note the `+psycopg`, this app uses
  SQLAlchemy's psycopg3 driver, not the bare `postgresql://` string the
  dashboard shows by default).
- Attach a **persistent disk** (Render "Disks" / Railway volumes) mounted
  at, e.g., `/app/data`, and set `UPLOADS_DIR=/app/data/uploads` — without
  this, evidence images are lost on every redeploy/restart (the container
  filesystem is ephemeral).
- Set the env vars from the table below. Both platforms inject `PORT`
  automatically; the Dockerfile's `CMD` already reads it.
- Note the backend's public URL (e.g. `https://metros-api.onrender.com`) —
  you need it for the frontend's `VITE_API_URL` and this service's own
  `ALLOWED_ORIGINS`.

**2. Frontend (Vercel), from the `frontend/` directory:**
- Import the repo, set the project root to `frontend/`, framework preset
  Vite (build command `npm run build`, output `dist`).
- Set `VITE_API_URL` to the backend URL from step 1
  (`https://metros-api.onrender.com`, no trailing slash).
- Deploy. Note Vercel's assigned domain (or your custom domain).

**3. Close the loop — set the backend's `ALLOWED_ORIGINS`** to the Vercel
domain from step 2 (e.g. `https://metros.vercel.app`), comma-separated if
you have more than one (a preview + production domain, say), and redeploy
the backend. Without this, the browser blocks the frontend's requests
(CORS) — `ALLOWED_ORIGINS` is empty/fail-closed by default.

**Data note:** with `METROS_OCR_ENGINE=tesseract`, label reading is entirely
on-device — no photos leave the deployment for this. (This section is
updated again once the Gemini vision reader lands on this branch.)

## Environment variables

| Variable | Default | Secret? | Notes |
|----------|---------|---------|-------|
| `METROS_ENV` | `development` | no | `production` refuses to start with `METROS_AUTH_DISABLED=1` or the default `JWT_SECRET` |
| `METROS_AUTH_DISABLED` | unset | no | `1` bypasses auth entirely — local dev only, refused in production |
| `DATABASE_URL` | `sqlite:///data/metroscan.db` | contains DB creds in Compose/Render/Railway | Postgres: `postgresql+psycopg://...` |
| `UPLOADS_DIR` | `data/uploads` | no | evidence images + crops; point this at a persistent disk/volume in production |
| `JWT_SECRET` | `dev-insecure-secret` | **yes** | must be overridden for any real deployment — the default is publicly known |
| `JWT_ALG` | `HS256` | no | |
| `JWT_EXPIRE_MINUTES` | `480` | no | |
| `MARKER_SIZE_MM` | `40.0` | no | must match `scripts/gen_calibration_card.py --marker-mm`, or every mm figure is wrong |
| `MAX_CORNER_JITTER_PX` | `2.0` | no | calibration-quality gate |
| `MAX_EXTRAPOLATION_SIDES` | `4.0` | no | how far from the marker a measurement is still trusted |
| `METROS_OCR_ENGINE` | `tesseract` | no | `tesseract` (only engine right now) |
| `ALLOWED_ORIGINS` | unset | no | comma-separated origins allowed to call the API cross-origin (backend only) — the Vercel frontend's URL in a split-origin deployment; empty = no cross-origin access |
| `VITE_API_URL` | unset | no | frontend only (build-time), the backend's URL for a split-origin deployment; empty = same-origin |

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
  restricted to secure contexts, and it protects the JWT in transit either way
  (Vercel and Render/Railway both do this by default).
- Label reading (Tesseract) is entirely on-device right now — no outbound
  network dependency for it.
- `data/uploads` grows with every scan (originals + crops are never
  deleted) — plan storage and backups accordingly for real inspection
  volume, and make sure it's on the persistent disk/volume in production,
  not the container's ephemeral filesystem.
