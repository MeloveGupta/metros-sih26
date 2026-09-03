# Architecture — SIH26034 Legal Metrology Compliance Scanner

> Python offline-first computer-vision pipeline. Scans a packaged-commodity
> label, measures declaration font height in **real millimetres**, validates
> every mandatory declaration against the Legal Metrology (Packaged
> Commodities) Rules, 2011, and emits an enforcement-ready compliance report.

---

## 1. Design principles

1. **LLM extracts, code decides.** Vision/NLP turns pixels into structured
   fields; a deterministic rule engine produces the pass/fail verdict. Verdicts
   are reproducible and cite the exact rule clause.
2. **Offline-first.** The full pipeline runs on-device with no internet. Any
   LLM API is an *optional* accelerator, never a dependency.
3. **The metric moat is geometry, not AI.** Font height in millimetres comes
   from an ArUco scale marker + pixel measurement — physics, deterministic,
   court-defensible. No model guesses a size.
4. **Rules are data, not code.** Thresholds and required declarations live in
   versioned YAML so an officer can amend them without a redeploy.
5. **Flat over nested.** Many shallow top-level modules; nesting kept ≤ 2 deep.

## 2. Pipeline

```
 capture  (product label + ArUco marker in frame)
    │
    ▼
[vision.scale]     detect ArUco  →  mm_per_pixel          (deterministic scale)
    │
    ▼
[vision.ocr]       PaddleOCR     →  text + per-char pixel boxes   (offline)
    │
    ▼
[vision.measure]   glyph_px × mm_per_pixel  →  glyph_mm   →  Rule 7 font check
    │
    ▼
[extract]          text  →  fields (MRP, net qty, mfg date, care info)
    │                        regex + spaCy NER  (Gemini optional fast-path)
    ▼
[rules.engine]     fields + measurements  →  verdict per clause + evidence crop
    │
    ▼
[reports]          PDF (WeasyPrint) + editable DOCX (python-docx)
    │
    ▼
[api] + [db] + [frontend]   repository, search, dashboard, RBAC
```

## 3. Why each choice

| Concern | Choice | Reason |
|---------|--------|--------|
| Scale recovery | OpenCV `cv2.aruco` | known-size marker → exact mm, deterministic |
| OCR + char boxes | PaddleOCR | offline, free, per-character boxes |
| Field parsing | regex + spaCy NER; Gemini optional | offline default, LLM swappable |
| Rule engine | plain Python + YAML catalog | deterministic, clause-cited, no-redeploy edits |
| API | FastAPI | async, Python (one language with CV), free OpenAPI |
| DB | PostgreSQL | records, history, search |
| Media | MinIO (S3 API) / local disk | evidence images |
| Frontend | React + Vite (PWA capture) | dashboard + in-field camera upload |
| Reports | WeasyPrint + python-docx | PDF *and* editable, both required by the PS |
| Auth | JWT + RBAC | officer / admin / auditor roles |
| Deploy | Docker Compose | one-command on-prem story |

**Why not pure-LLM:** a monocular photo has no absolute scale — the same glyph
is 2 mm or 20 mm depending on camera distance, and both render identical
pixels. No model recovers information the image does not contain. Rule 7
therefore needs a physical reference (ArUco), and enforcement needs a
reproducible number, which a language model cannot guarantee.

Note (Rule 7, Table-I as amended by GSR 629(E), w.e.f. 01-01-2018): the letter
height threshold is keyed to the **area of the principal display panel in cm²**,
not net weight. The pipeline therefore makes *two* scale-derived measurements —
panel area (cm²) to pick the band, and glyph height (mm) to check it. Real
thresholds live in `rules/lmpc-2011.yaml`; full text in `docs/lmpc-2011.pdf`.

**Why not Rust/Go for the core:** the OCR + OpenCV ecosystem is Python-first.
Go/Rust bindings are immature and would cost build time for no scoring benefit.
The vision core stays Python; a Rust (axum) or Go API gateway is a clean v2
wrapper around the Python service if the project grows.

## 4. Component responsibilities

- **vision/** — scale recovery, OCR, glyph→mm measurement. The differentiator.
- **extract/** — structured field extraction from OCR text.
- **rules/** — YAML rule catalog + deterministic engine; each verdict cites a clause.
- **reports/** — PDF + DOCX generation with embedded evidence crops.
- **api/** — FastAPI routes, auth, RBAC.
- **db/** — models, migrations, repository + search.
- **schemas/** — Pydantic request/response + internal DTOs.
- **frontend/** — React dashboard, upload, in-field capture (PWA).

## 5. Data model (core entities)

```
Product   (id, name, brand, category, source, created_by, created_at)
Scan      (id, product_id, image_ref, marker_mm, mm_per_pixel, status, ts)
Field     (id, scan_id, kind, raw_text, value, bbox, confidence)
Verdict   (id, scan_id, clause, result, measured, threshold, evidence_ref)
Report    (id, scan_id, pdf_ref, docx_ref, generated_at)
User      (id, name, email, role, pw_hash)
AuditLog  (id, user_id, action, target, reason, ts)
```

## 6. Build order (demo-first)

1. **vision/** — ArUco + PaddleOCR + glyph→mm on a real supermarket photo. *(the moat — ship first)*
2. **rules/** — YAML catalog + engine for MRP, net quantity, manufacturer, consumer-care, Rule 7 font.
3. **api/** — upload → verdict JSON.
4. **reports/** — PDF + DOCX with evidence crops.
5. **frontend/** — dashboard + repository search.
6. **auth + db + docker** — RBAC, persistence, one-command deploy.

## 7. Deployment

`docker-compose up` brings up: API (FastAPI + Uvicorn), PostgreSQL, MinIO,
frontend (static build). Runs fully offline; the LLM fast-path activates only
when an API key is present in the environment.
