# Deployment

Metros is a web app: the backend needs a database and a place to store
evidence photos/crops/reports. On this branch, the label reader is Google
Gemini's free tier (needs `GEMINI_API_KEY`, no GPU), falling back to
on-device Tesseract whenever the key is unset or a call fails. This doc
covers running it locally, via Docker Compose, and the production path:
**Vercel (frontend) + Render free tier, Docker (backend) + Supabase
(Postgres + Storage)**.

## System requirements

- Python 3.11–3.14 (backend), Node 20+ (frontend build)
- PostgreSQL 14+ in production (SQLite is fine for local dev — it's the
  default with no `DATABASE_URL` set). Supabase's managed Postgres is the
  production choice this doc walks through.
- Tesseract OCR binary on `PATH` for the label reader (`apt-get install
  tesseract-ocr` / `brew install tesseract`) — optional locally, always
  installed in the Docker image
- WeasyPrint's native libs (cairo, pango, gdk-pixbuf) for PDF rendering —
  optional; without them, `GET /scans/{id}/report.pdf` returns 503 (the DOCX
  download still works). Already installed in the Docker image.
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

`scripts/seed_users.py` is one way to create the first admin locally; the
other is setting `ADMIN_EMAIL`/`ADMIN_PASSWORD` before `make run` — the API
creates that admin at startup if no users exist yet (see
`backend/db/repository.py`'s `ensure_admin_user`, wired up in
`backend/api/main.py`). This is the *only* way to get a first login in
production, since there's no sign-up flow and no shell access to run
`scripts/seed_users.py` against a managed database.

Evidence photos/crops and generated PDF/DOCX reports are stored on local
disk (`UPLOADS_DIR`, default `data/uploads`) unless `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_BUCKET` are all set, in which case
they go to that Supabase Storage bucket instead — see `backend/core/storage.py`.
Local dev needs none of this; it's the production default (below).

## Docker Compose

```bash
cp .env.example .env    # fill in JWT_SECRET at minimum
docker compose up --build
```

Brings up `api` (FastAPI + Uvicorn), `db` (Postgres 17), and `frontend`
(static build served by nginx, proxying `/auth`, `/scan`, `/scans`, `/stats`,
`/users`, `/health` to `api`). `api` waits for a Postgres health check before
starting — no manual ordering needed. This path uses local disk (the
`data-uploads` volume) for evidence, not Supabase Storage — set
`ADMIN_EMAIL`/`ADMIN_PASSWORD` in `.env` before first boot to get a login,
or seed users the same way as local dev, just against the container's DB:

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

## Deploying: Vercel (frontend) + Render free (backend, Docker) + Supabase

No GPU needed anywhere in this path, and it fits entirely in free tiers.

**1. Supabase project — Postgres + Storage:**
- Create a project at [supabase.com](https://supabase.com/dashboard).
- **Database**: Project Settings → Database → **Connection pooler** (not the
  direct connection — the pooler handles many short-lived connections from a
  small, often cold-starting web service far better). Copy the URI, rewrite
  `postgresql://` to `postgresql+psycopg://` (SQLAlchemy's psycopg3 driver),
  and add `?sslmode=require`:
  ```
  postgresql+psycopg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
  ```
  This is `DATABASE_URL` below. Tables are created automatically on the
  API's first boot (`init_db()` in `backend/api/main.py` — no separate
  migration step).
- **Storage**: Storage → **New bucket** → name it (e.g. `metros-evidence`),
  and leave it **private** (do not toggle "Public bucket") — evidence photos
  are only ever served back out through this API's own authenticated
  endpoints (`GET /scans/{id}/images/{n}` etc.), never a public Supabase URL.
- Note down, from Project Settings → API: the **Project URL**
  (`SUPABASE_URL`) and the **service_role** key (`SUPABASE_SERVICE_ROLE_KEY`
  — not `anon`; this key bypasses row-level security and must never reach
  the frontend, only this backend).

**2. Backend (Render, free plan), from `docker/api.Dockerfile`:**
- Easiest: **New → Blueprint**, point it at this repo — `render.yaml` at the
  repo root already declares the service (Docker, `docker/api.Dockerfile`,
  free plan, health check at `/health`). Otherwise, **New → Web Service**,
  runtime **Docker**, Dockerfile path `docker/api.Dockerfile`, build context
  the repo root, plan **Free**.
- Fill in the env vars the Blueprint leaves blank (or add manually):

  | Variable | Value |
  |----------|-------|
  | `METROS_ENV` | `production` |
  | `JWT_SECRET` | a real random secret — `openssl rand -hex 32` |
  | `DATABASE_URL` | the Supabase pooler URL from step 1 |
  | `SUPABASE_URL` | from step 1 |
  | `SUPABASE_SERVICE_ROLE_KEY` | from step 1 |
  | `SUPABASE_BUCKET` | the bucket name from step 1 |
  | `ADMIN_EMAIL` / `ADMIN_PASSWORD` | your first login — created at startup |
  | `GEMINI_API_KEY` | from https://aistudio.google.com/apikey |
  | `ALLOWED_ORIGINS` | the Vercel frontend's origin — set after step 3 |

  Render injects `PORT` itself; the Dockerfile's `CMD` already reads it —
  don't set it manually.
- Deploy, then confirm `https://<your-service>.onrender.com/health` returns
  `{"status": "ok", ...}`. Note this URL — it's `VITE_API_URL` in step 3.
- **Free-plan notes**: the container spins down after ~15 minutes idle and
  cold-starts on the next request (a few seconds — this is normal, not a
  bug); memory is capped at 512 MB (see "Memory use" below for what one scan
  actually costs); there's no persistent disk, which is exactly why step 1's
  Supabase Storage bucket exists — without it, every evidence photo and
  generated report is lost on the next spin-down/redeploy.

**3. Frontend (Vercel), from the `frontend/` directory:**
- Import the repo, set the project root to `frontend/`, framework preset
  Vite (build command `npm run build`, output `dist`). `frontend/vercel.json`
  already adds the SPA rewrite (all routes → `index.html`) React Router-style
  apps need — without it, a hard refresh on any non-root route 404s.
- Set `VITE_API_URL` to the backend URL from step 2
  (`https://<your-service>.onrender.com`, no trailing slash).
- Deploy. Note Vercel's assigned domain (or your custom domain).

**4. Close the loop — set the backend's `ALLOWED_ORIGINS`** (Render →
your service → Environment) to the Vercel domain from step 3
(e.g. `https://metros.vercel.app`), comma-separated if you have more than
one (a preview + production domain, say), and redeploy the backend. Without
this, the browser blocks the frontend's requests (CORS) — `ALLOWED_ORIGINS`
is empty/fail-closed by default, and `backend/api/main.py` only attaches
CORS middleware at all when it's non-empty.

**5. Log in** with `ADMIN_EMAIL`/`ADMIN_PASSWORD` from step 2 — the backend
created that admin on its first boot. Create officer/auditor accounts from
there via `POST /users` (admin-only) or the admin UI.

**Data note:** free-tier Gemini prompts may be used by Google to improve its
products; use demo packs only. Production would use a self-hosted open
model.

## Environment variables

| Variable | Default | Secret? | Notes |
|----------|---------|---------|-------|
| `METROS_ENV` | `development` | no | `production` refuses to start with `METROS_AUTH_DISABLED=1` or a known-weak/placeholder `JWT_SECRET` |
| `METROS_AUTH_DISABLED` | unset | no | `1` bypasses auth entirely — local dev only, refused in production |
| `DATABASE_URL` | `sqlite:///data/metroscan.db` | contains DB creds | Postgres: `postgresql+psycopg://...`; Supabase: the pooler URL with `?sslmode=require` (see step 1 above) |
| `UPLOADS_DIR` | `data/uploads` | no | local-disk fallback for evidence/reports when the `SUPABASE_*` vars below are unset |
| `SUPABASE_URL` | unset | no | Supabase project URL; all three `SUPABASE_*` vars must be set together to enable Storage |
| `SUPABASE_SERVICE_ROLE_KEY` | unset | **yes** | Storage → service_role key; bypasses RLS, backend-only, never sent to the frontend |
| `SUPABASE_BUCKET` | unset | no | a **private** Storage bucket name — files are only ever served via this API's authenticated endpoints |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | unset | `ADMIN_PASSWORD` yes | first-boot admin, created only if no users exist yet — the only way to get a login in production (no sign-up flow) |
| `JWT_SECRET` | `dev-insecure-secret` | **yes** | must be overridden for any real deployment — `dev-insecure-secret`, `change-me`, `changeme`, `secret` and empty are all refused in production as known-weak placeholders |
| `JWT_ALG` | `HS256` | no | |
| `JWT_EXPIRE_MINUTES` | `480` | no | |
| `MARKER_SIZE_MM` | `40.0` | no | must match `scripts/gen_calibration_card.py --marker-mm`, or every mm figure is wrong |
| `MAX_CORNER_JITTER_PX` | `2.0` | no | calibration-quality gate |
| `MAX_EXTRAPOLATION_SIDES` | `4.0` | no | how far from the marker a measurement is still trusted |
| `METROS_OCR_ENGINE` | `gemini` | no | `gemini` (this branch's default; needs `GEMINI_API_KEY`, falls back to `tesseract` when unset/failing) or `tesseract` |
| `GEMINI_API_KEY` | unset | **yes** | Google AI Studio key (https://aistudio.google.com/apikey); backend only, never logged/exposed |
| `METROS_GEMINI_MODEL` | `gemini-3.5-flash-lite` | no | primary model; override only if you know the exact current model ID (see `backend/extract/gemini_reader.py`'s module docstring) |
| `METROS_GEMINI_FALLBACK_MODEL` | `gemini-3.1-flash-lite` | no | tried once if the primary model's free-tier quota is exhausted |
| `ALLOWED_ORIGINS` | unset | no | comma-separated origins allowed to call the API cross-origin (backend only) — the Vercel frontend's URL in a split-origin deployment; empty = no cross-origin access |
| `VITE_API_URL` | unset | no | frontend only (build-time), the backend's URL for a split-origin deployment; empty = same-origin |

## Data storage and backup

- **Database**: the canonical `Report` (declarations, font analysis,
  evidence metadata, officer actions — everything) is one JSON blob per scan
  in `ScanRow.report_json`, with a few denormalized columns for search. No
  migration framework: on a schema change in development, delete
  `data/metroscan.db` and restart; in Postgres, back it up like any other
  DB — Supabase takes daily backups automatically on its paid plans, or use
  `pg_dump` against the direct (non-pooler) connection string yourself on
  the free plan.
- **Evidence files + generated reports**: original uploads, per-declaration
  crops, and rendered PDF/DOCX reports (cached after first render — see
  `GET /scans/{id}/report.pdf` in `backend/api/main.py`) all live under
  `UPLOADS_DIR` locally or the Supabase Storage bucket in production —
  back this up alongside the database either way, since
  `Evidence.images[].file` / `DeclarationFinding.evidence_crop` are paths
  into it, not embedded data. Supabase Storage itself is durable object
  storage (not the container's ephemeral filesystem), so nothing extra is
  needed beyond the bucket existing.

## Memory use

Render's free plan caps a service at **512 MB**. Measured locally (RSS via
`ps -o rss` on the actual `uvicorn` worker process, one worker, as the
Dockerfile runs it) on this branch, `METROS_OCR_ENGINE=gemini`:

| Point | RSS |
|-------|-----|
| Idle, right after startup (imports, no request served yet) | **119 MB** |
| Peak during the *first* `/scan` request — two 400×400px photos, ArUco calibration, first-ever `google-genai` SDK import + a live Gemini vision call, rule evaluation, crop + PDF/DOCX-cache writes | **198 MB** |
| Settled, after 4 total scans (checked for a climb — there isn't one) | **205 MB** |

That's **~307 MB of headroom** below the 512 MB cap even after the first
(most expensive) request. The first-request jump (119 → 198 MB) is almost
entirely the one-time cost of importing `google-genai` and its transitive
deps (`google-auth`, `grpc`/`httpx` machinery) — later scans reuse the
already-imported module and cost much less.

Headroom comes mostly from what's *not* loaded eagerly: WeasyPrint and the
`google-genai` SDK are both imported lazily, inside the function that
actually needs them (`backend/reports/render.py`, `backend/extract/gemini_reader.py`),
not at module import time — so a request that never touches PDF rendering
or Gemini never pays for either. One uvicorn worker (the Dockerfile's
default) avoids paying the base OpenCV/FastAPI import cost more than once;
a second worker would roughly double the idle 119 MB baseline for no
throughput benefit at this app's request volume.

Real production photos (phone cameras, several MB / ~3000–4000px, resized
to 1600px before the Gemini call — see `gemini_reader.py`) will push the
peak somewhat higher than this synthetic 400×400px test image; re-check
after any change that adds a new eagerly-loaded dependency, and if headroom
ever gets tight, Render's next paid tier doubles the cap.

## Rule catalog hot-update

`rules/lmpc-2011.yaml` is read by `load_catalog()` at request time, not at
process startup — editing it takes effect on the next scan with **no
redeploy or restart needed**. Always record `gazette` and `effective_from`
on any new/changed entry; never hardcode legal text in Python.

## Hosting notes

- Serve the frontend over **HTTPS** in production — some browser APIs the
  photo-capture inputs rely on (and any future live-camera work) are
  restricted to secure contexts, and it protects the JWT in transit either way
  (Vercel and Render both do this by default).
- Label reading needs a stable outbound path to Google's Gemini API when
  `METROS_OCR_ENGINE=gemini` (the default on this branch) and
  `GEMINI_API_KEY` is set — without either, the app still works via the
  Tesseract fallback (fully on-device, no outbound dependency), just without
  the primary vision reader. Gemini results are cached on local disk by the
  image set's combined SHA-256 (`data/gemini_cache/`) so retries/re-opens of
  the same photos don't count against the free-tier quota twice — this
  cache is a pure performance optimization (not user data), so it staying
  local-only and being lost on redeploy is fine.
- Render's free plan spins the container down after ~15 minutes idle; the
  next request cold-starts it (a several-second delay is expected, not a
  failure).
