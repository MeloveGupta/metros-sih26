# api

FastAPI app: auth (`/auth/token`, user management), scan (`/scan`, retail
pack or e-commerce listing), repository search + dashboard stats (`/scans`,
`/stats`), evidence (`/scans/{id}/images`, `/crops`), officer verification
(`/scans/{id}/review-items`, `/finalize`), and PDF/DOCX downloads. JWT auth +
RBAC (officer / admin / auditor) on by default; see the module docstring in
`main.py` for the full route list.
