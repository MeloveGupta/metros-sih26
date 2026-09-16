# Architecture — SIH26034 Legal Metrology Compliance Scanner

> Python computer-vision pipeline behind a web app. Scans a
> packaged-commodity label, measures declaration font height in **real
> millimetres**, validates every mandatory declaration against the Legal
> Metrology (Packaged Commodities) Rules, 2011, and emits an enforcement-ready
> compliance report.

---

## 1. Design principles

1. **OCR extracts, code decides.** Vision turns pixels into text; deterministic
   regex parsers turn text into structured fields; a deterministic rule engine
   produces the verdict. Verdicts are reproducible and cite the exact rule
   clause. No model ever decides compliance.
2. **Decision-support, not adjudication.** The system reports **potential**
   non-compliance with a confidence + evidence crop, flagged for officer
   verification — it makes no final legal finding and cannot verify actual
   contents/weight from an image. "Not detected in the image" ≠ "legally absent";
   low-confidence items route to mandatory officer review.
3. **Calibrated or nothing.** Millimetre measurement requires an in-plane
   known-size marker (ArUco/AprilTag) + perspective correction, and is reported
   as value ± uncertainty. Uncalibrated images get no mm verdict; barcode width
   is never used as the scale (magnification varies).
4. **Rules are versioned data.** Each rule stores clause + source URL + gazette +
   effective-from + applicability, so an officer amends it without a redeploy and
   reports cite only officially verified text.
5. **Local by default.** Declaration text is read from a photo by on-device
   Tesseract OCR + regex — no internet or API key required. A scan never
   silently returns nothing: with neither a photo nor pasted text readable,
   the report is `needs_officer_review`, never a silent empty pass.
6. **The moat is geometry, not AI.** Font height in mm comes from marker scale +
   pixel measurement — physics, deterministic. No model guesses a size.
7. **Flat over nested.** Many shallow top-level modules; nesting kept ≤ 2 deep.

## 2. Pipeline

```
 capture  (product label + ArUco marker in frame; or an e-commerce listing
           screenshot/pasted text, which skips calibration + Rule 7/8 entirely)
    │
    ▼
[vision.scale]     detect ArUco  →  mm_per_pixel          (deterministic scale)
    │
    ▼
[extract]          Tesseract OCR reads label photos on-device → regex parsers
                    turn text into fields (MRP, net qty, mfg date, care info)
    │
    ▼
[vision.measure]   glyph_px × mm_per_pixel  →  glyph_mm   →  Rule 7 font check
    │                        (always deterministic geometry, never the OCR engine)
    ▼
[rules.engine]     fields + measurements  →  verdict per clause + evidence crop
    │
    ▼
[reports]          PDF (WeasyPrint) + editable DOCX (python-docx), evidence
                    images/crops embedded inline
    │
    ▼
[api] + [db] + [frontend]   repository, search, dashboard, RBAC, officer
                             verification (review-items + finalize)
```

## 3. Why each choice

| Concern | Choice | Reason |
|---------|--------|--------|
| Scale recovery | OpenCV `cv2.aruco` | known-size marker → exact mm, deterministic |
| Field parsing | Tesseract OCR + regex | on-device, no internet or API key needed |
| Rule engine | plain Python + YAML catalog | deterministic, clause-cited, no-redeploy edits |
| API | FastAPI | async, Python (one language with CV), free OpenAPI |
| DB | PostgreSQL (SQLite for local dev) | records, history, search |
| Media | local disk (`data/uploads`, a Docker volume) | evidence images + crops |
| Frontend | React + Vite | scan form, history/search, officer dashboard |
| Reports | WeasyPrint + python-docx | PDF *and* editable, both required by the PS |
| Auth | JWT + RBAC | officer / admin / auditor roles |
| Deploy | Docker Compose | one-command deployment |

**Why not a pure vision-language model:** a monocular photo has no absolute scale — the same glyph
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

## 3b. Scale reference — the calibration card

The millimetre measurement needs a known-size object in the same plane as the
declaration. The chosen reference is a **printed ArUco calibration card**, not
any identity document.

- **Size:** ID-1 / CR80, 85.60 × 53.98 mm (ISO/IEC 7810) — fits a wallet.
- **Marker:** an ArUco tag of a fixed, printed side length (default 40 mm) →
  the detector recovers all four corners to sub-pixel accuracy and yields
  `mm_per_pixel`; a homography from the marker corners flattens perspective.
- **Why not Aadhaar / any ID card:** an enforcement tool stores every capture as
  evidence — embedding a national ID (photo + number, biometric-linked) in each
  record is a DPDP Act 2023 / Aadhaar-handling liability for zero benefit. A
  plain card also has no fiducial pattern, so its corners are fragile to detect
  on a cluttered shelf. The ArUco card gives the same "lay it next to the
  product" convenience with **no PII** and **robust auto-detection**.
- **Distribution:** ship a free printable A4 sheet (`scripts/gen_calibration_card.py`).
  An office prints once; officers carry the cut-out card. Zero ongoing cost.
- **Fallbacks (manual/low-confidence mode):** any blank ID-1 card by its known
  outline, a ₹5 coin (⌀ 23 mm), or a ruler. **Never** barcode width — EAN-13
  magnification varies. No reference in frame ⇒ no mm verdict.

## 4. Component responsibilities

- **vision/** — scale recovery, OCR, glyph→mm measurement. The differentiator.
- **extract/** — structured field extraction from OCR text.
- **rules/** — YAML rule catalog + deterministic engine; each verdict cites a clause.
- **reports/** — PDF + DOCX generation with embedded evidence crops.
- **api/** — FastAPI routes, auth, RBAC.
- **db/** — models, repository (search, stats, audit log). No migration
  framework; `create_all()` only (see `backend/db/README.md`).
- **schemas/** — Pydantic request/response + internal DTOs.
- **frontend/** — scan form, history/search, officer dashboard, report view.

## 5. Data model (core entities)

The canonical `Report` (declarations, font analysis, placement, readability,
evidence, officer actions — see `docs/report-spec.md`) is stored as one JSON
blob per scan, the single source of truth for the DB, API, and PDF/DOCX
renderers. A few fields are denormalized onto `ScanRow` for cheap search/stats
filtering, rather than a separate Field/Verdict table per finding:

```
ProductRow  (id, name, brand, category, source, created_at)
ScanRow     (id, product_id, ref_no, disposition, calibrated, sha256,
             finalized, has_rule7_flag, report_json, created_by, created_at)
User        (id, name, email, role, pw_hash)
AuditLog    (id, user_id, action, target, reason, created_at)
```

## 6. Build order (demo-first)

1. **vision/** — ArUco + OCR fallback + glyph→mm on a real supermarket photo. *(the moat — ship first)*
2. **rules/** — YAML catalog + engine for MRP, net quantity, manufacturer, consumer-care, Rule 7 font.
3. **api/** — upload → verdict JSON.
4. **reports/** — PDF + DOCX with evidence crops.
5. **frontend/** — dashboard + repository search.
6. **auth + db + docker** — RBAC, persistence, one-command deploy.

## 7. Deployment

`docker-compose up` brings up: API (FastAPI + Uvicorn), PostgreSQL, frontend
(static build via nginx). Evidence images/crops live on a Docker volume
(`data/uploads`), not an object-storage service. Label reading is on-device
(Tesseract OCR) — no outbound internet dependency. See
[`docs/deployment.md`](deployment.md) for the full env-var table and setup
steps.
