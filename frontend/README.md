# frontend

React + Vite app for Metros: officer sign-in, a tabbed Scan / History /
Dashboard layout, product scan (photos or an e-commerce listing screenshot/
pasted text, plus an optional product name and category), a report view with
per-declaration statuses, Rule 7 font measurements (mm ± uncertainty), an
evidence thumbnail strip, server-driven officer verification, and PDF/DOCX
downloads.

## Develop

```bash
npm install
npm run dev        # http://localhost:5173, proxies API routes to :8000
```

Start the backend first (`uvicorn backend.api.main:app --reload`). The dev
server proxies `/auth`, `/scan`, `/scans`, `/stats`, `/users`, `/health` to it
(see `vite.config.js`).

## Build

```bash
npm run build      # -> dist/ (served by docker/frontend.Dockerfile)
```

## Photo capture

`ScanForm` uses two plain file inputs, not a live `getUserMedia` camera feed:
one with `capture="environment"` (opens the rear camera directly on mobile,
single shot at a time) and one plain multi-select (`multiple`, gallery
picker). Both upload the original files to `POST /scan` — no in-browser
re-encoding.

## Auth

The JWT from `/auth/token` is kept in a React module variable (`api.js`), not
`localStorage` or `sessionStorage` — a page reload requires signing in again.
Every authenticated request (including PDF/DOCX/evidence-image downloads,
which need the `Authorization` header) goes through `api.js`'s fetch helpers.
