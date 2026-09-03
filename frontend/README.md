# frontend

React + Vite dashboard for MetroScan: officer sign-in, product scan (image +
optional label text + marker size), and a report view with per-declaration
statuses, Rule 7 font measurements (mm ± uncertainty), calibration verdict, and
a DOCX download.

## Develop

```bash
npm install
npm run dev        # http://localhost:5173, proxies /scan,/auth,/scans to :8000
```

Start the backend first (`uvicorn backend.api.main:app --reload`). The dev
server proxies API routes to it (see `vite.config.js`).

## Build

```bash
npm run build      # -> dist/ (served by docker/frontend.Dockerfile)
```

The token is kept in `localStorage`; every read/write is guarded so a private
window or blocked storage degrades gracefully.
