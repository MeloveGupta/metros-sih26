# Report Specification — MetroScan Compliance Report

The report is the product's deliverable and the thing Legal Metrology officers
judge. It must be **detailed, traceable, and legally careful**: it reports
**potential** non-compliance with evidence, confidence, and measurement
uncertainty — never a final legal finding. Every automated statement is
attributable to a rule clause and a piece of evidence, and every measurement
carries an error bound.

Three synchronized outputs, one source of truth:
- **JSON** — machine-readable record (the canonical object; DB + API).
- **PDF** — fixed, watermarked, print/sign (WeasyPrint from one HTML template).
- **DOCX** — editable, same content (python-docx), for officer annotation.

The PDF and DOCX are rendered from the JSON so the three never diverge.

---

## Report status vocabulary (use everywhere, never "violation")

| Status | Meaning |
|--------|---------|
| `compliant` | Declaration present and satisfies the checked rule(s) |
| `potential_non_compliance` | Automated check flags a likely issue — **officer must verify** |
| `not_detected` | Not found in the submitted image (≠ legally absent) |
| `not_assessable` | Image quality / calibration / confirmation insufficient to decide |
| `not_applicable` | Rule does not apply to this commodity/category/scan source |
| `needs_officer_review` | Report-level disposition when every gap is `not_assessable` (nothing flagged outright, but nothing could be confirmed either) |
| `officer_confirmed` / `officer_overridden` | Human disposition recorded |

`Report.disposition` (the automated rollup) is never edited by an officer
action — `Report.final_disposition` (`potential_non_compliance_confirmed_by_officer`
/ `verified_compliant_by_officer`), set on finalize, is the separate,
append-only officer verdict.

---

## 1. Cover / header

- Report ID (UUID) and human ref no.; page x of y on every page.
- Generated-at (ISO 8601, IST); app version; rule-catalog version + hash.
- **Disposition banner:** "Decision-support — potential non-compliance flagged
  for officer verification. Not a final legal finding. Physical verification
  required for enforcement."
- Inspection context: officer name + ID + role; office/jurisdiction; optional
  geolocation + accuracy; capture device/model.
- Product identity: name, brand, category, batch/lot (if read), source
  (`retail_pack` / `ecommerce_listing`), SKU/barcode value (as text only).
- Reader banner: which backend actually produced this report
  (`extraction.backend_used`: `llm` / `ocr_regex` / `label_text`), any AI-reader
  error, and warnings (e.g. blurry/glare image, no category specified, or —
  for an e-commerce listing — that Rule 7/8 aren't assessed at all).

## 2. Executive summary

- Counts by status (checked / compliant / potential_non_compliance /
  not_detected / not_assessable / not_applicable).
- `compliance_ratio` — compliant ÷ assessable (excludes not_assessable and
  not_applicable from the denominator) — and a one-line plain-language summary.
- **Required officer actions** list (every item needing verification, ranked).
- Thumbnail of the annotated capture (boxes + status colors).

## 3. Evidence & chain of custody

- Every uploaded image (`evidence.images`, role-tagged `front` / `back` /
  `other` / `listing`): filename (as stored, not the original re-encoded),
  SHA-256 hash of the **uploaded bytes** (not a re-encoding), capture
  timestamp, resolution, EXIF — **originals preserved unmodified**.
- Per-declaration evidence crops (`evidence_crop`, a real saved PNG for every
  finding whose bbox was known), plus a transformation log (crop op +
  parameters) so any derived image is reproducible from the original.
- Integrity note (verbatim): *"The SHA-256 hash proves the file is unaltered
  after capture. It does not by itself prove the subject or location, and is not
  a statement of court-admissibility."* This report is not itself a claim of
  legal admissibility.
- Personal/location data handling per **DPDP Act, 2023** (minimized, encrypted,
  role-gated); access to this record is audit-logged.

## 4. Calibration & measurement basis (the moat, shown transparently)

- Scale reference: type (ArUco calibration card), dictionary + marker id,
  **printed marker side (mm)**, detection confidence.
- Recovered `mm_per_pixel`; **corner jitter (px)** — the max corner
  displacement across independent re-detections of the same marker (plain
  grayscale, CLAHE-enhanced, upscaled) — as the calibration-quality signal;
  **calibration verdict** (`calibrated` / `rejected_uncalibrated`).
- If rejected — or for an e-commerce listing, where calibration is skipped
  entirely rather than attempted and failed — no mm figures are reported; Rule
  7 items are empty, not `not_assessable`, with the reason on the calibration
  block itself.
- A measurement beyond `MAX_EXTRAPOLATION_SIDES` marker-side-lengths from the
  marker centre is `not_assessable` rather than trusted — homography error
  grows with distance from the reference.
- Global uncertainty model: how the ± interval on each mm figure is derived
  (marker size tolerance + corner jitter, scaled by extrapolation distance +
  glyph-edge localization).

## 5. Declaration findings (Rule 6) — one row per declaration

For **each** mandatory declaration:

| Field | Content |
|-------|---------|
| Declaration | e.g. "Retail sale price (MRP)" |
| Clause | e.g. Rule 6(1)(e) — with source + gazette + effective date |
| Extracted value | the parsed text (e.g. "MRP ₹ 45.00 incl. of all taxes") |
| Location | bounding box on the image + panel (PDP / other) |
| OCR confidence | per-field confidence |
| Format check | pass / fail against the rule's accepted patterns (list the pattern) |
| Status | from the vocabulary above |
| Evidence crop | cropped image of exactly this declaration |
| Note | plain-language reason; what an officer should verify |

Special handling notes carried in the row where relevant: MRP accepted wordings
(Rule 6 illustrations), net-quantity unit normalization, month/year formats,
country-of-origin only if imported, consumer-care contact completeness (Rule 6(2)).

## 6. Font-size analysis (Rule 7) — detailed

- **Principal display panel area:** measured cm² **± uncertainty** (from the
  scale + a token polygon) or officer-entered dimensions (rectangular /
  cylindrical / other, per Rule 7(4) as substituted by GSR 629(E)), and the
  selected Table-I band with its min-height threshold (normal vs molded).
- Per measured declaration: glyph/numeral height **mm ± uncertainty** (the
  median of individual glyph boxes, not the whole word's bounding box),
  per-glyph width/height ratio (Rule 7(3)) with the worst offending character,
  threshold, and status. Show the measured-vs-required comparison explicitly.
- No absolute-floor check exists independent of the band: GSR 629(E) (w.e.f.
  01-01-2018) folded the old 1 mm / 2 mm floor into Table-I band 1's own
  minimum. When the panel area isn't known at all, the measurement is checked
  against band 1's minimum only if it's clearly below it; otherwise it's
  `not_assessable`, "panel size not captured, Table-I band not selected" —
  never silently passed.
- Width-ratio exceptions: `1`, `i`, `I`, `l` are excluded from the ⅓-width
  check (their natural width is narrower than their height).
- Skipped entirely (empty list, not `not_assessable`) for an e-commerce
  listing — there is no physical panel to measure.
- Every Rule 7 figure links back to §4 calibration; if `not_assessable`, say why.

## 6b. Placement (Rule 8) and readability (Rule 9)

- **Clear space** around the net-quantity declaration (Rule 8(1) proviso): a
  pixel-space geometric check — no OCR text may intrude within one numeral
  height above/below or two numeral heights either side of it. `not_assessable`
  if no net-quantity bounding box was found.
- **Grouping** of declarations on the principal display panel (Rule 2(h),
  8(1)): always routed to officer review (`not_assessable`) — no automated
  layout judgment is attempted.
- **Contrast** (Rule 9(1)(b)): a WCAG-style luminance ratio between the MRP/
  net-quantity text and its background, skipped when the officer marks the
  pack `molded`.
- **Image quality**: blur (variance of Laplacian) and glare (saturated-pixel
  fraction) heuristics gate `not_detected` findings to `not_assessable`
  instead, with a "retake photo" warning, rather than reporting a false
  absence.
- **Language** (Rule 9(4)): Devanagari or Latin script detected in the
  extracted text; neither present routes to officer review.
- **Stickers** (Rule 6(3)): a static officer-review checklist item — sticker
  detection is not automated.
- Both Rule 8 and Rule 9's image-derived checks (contrast, image quality) are
  skipped for an e-commerce listing, same as Rule 7; language/stickers still
  apply since they're about the declared text/content, not the physical print.

## 7. Legal basis

- Statutory authority: **Section 18, Legal Metrology Act, 2009** (prescribed
  declarations required for pre-packaged commodities).
- Table of every clause cited in this report → source URL, gazette no., effective
  date, applicability. Only officially verified text (no blog/LLM summaries).
- Amendment provenance where relevant (e.g. Rule 7 Table-I via GSR 629(E),
  w.e.f. 01-01-2018).

## 8. Officer verification & disposition

- `GET /scans/{id}/review-items` is the canonical list of what needs a human
  decision: every declaration, font, and placement finding whose status is
  `potential_non_compliance`, `not_detected`, or `not_assessable`.
- `POST /scans/{id}/finalize` requires a decision (`verified_compliant` or
  `confirmed_issue`, the latter needing a note) for **every** review item —
  a partial submission is rejected (400, naming what's missing). Already-
  finalized reports refuse a second finalize (409).
- Each decision is appended to `officer_actions` (declaration id, verdict,
  note, officer id from the JWT, timestamp) and to the audit log —
  append-only, never editing the automated `declarations`/`font_analysis`.
- `final_disposition` is set alongside: any `confirmed_issue` action yields
  `potential_non_compliance_confirmed_by_officer`, otherwise
  `verified_compliant_by_officer`. The automated `disposition` is untouched.
- PDF/DOCX downloads are gated (409) until the report is finalized, unless
  there were zero review items to begin with.
- Signature block on the rendered report (name, designation, jurisdiction, date).

## 9. Limitations & confidence statement (verbatim block)

- Decision-support scope; "not detected" ≠ "legally absent".
- Measurement reliable only for guided/planar captures with valid calibration;
  degraded for curved/shiny/crumpled/transparent/angled packages.
- Physical verification required before any enforcement action.

## 10. Appendix

- Full raw OCR dump (text + boxes + confidences).
- Rule-catalog snapshot used (ids + versions + hashes) for reproducibility.
- Processing log / timings; app + model versions.
- Glossary (PDP, ArUco, mm_per_pixel, homography, confidence, uncertainty).

---

## JSON skeleton (canonical record)

```json
{
  "report_id": "uuid",
  "ref_no": "MS-2026-000123",
  "generated_at": "2026-09-03T20:15:00+05:30",
  "app_version": "0.1.0",
  "rule_catalog": { "version": "2011 (amended 2022)", "hash": "sha256:..." },
  "disposition": "potential_non_compliance",
  "inspection": {
    "officer": { "id": "...", "name": "...", "role": "officer" },
    "jurisdiction": "...", "geo": { "lat": 0, "lon": 0, "accuracy_m": 0 },
    "device": "..."
  },
  "product": { "name": "...", "brand": "...", "category": "food",
               "batch": "...", "source": "retail_pack", "barcode_text": "..." },
  "evidence": {
    "images": [
      { "file": "<report_id>/0_front.png", "sha256": "sha256:...",
        "role": "front", "captured_at": "...", "width": 0, "height": 0, "exif": {} }
    ],
    "transformations": [ { "op": "crop", "params": { "declaration_id": "mrp", "bbox": [0,0,0,0] } } ],
    "integrity_note": "…verbatim block…"
  },
  "calibration": {
    "reference": "aruco_card", "dict": "DICT_4X4_50", "marker_id": 0,
    "marker_mm": 40.0, "detection_confidence": 0.0,
    "mm_per_pixel": 0.0, "corner_jitter_px": 0.0,
    "verdict": "calibrated", "reason": null
  },
  "extraction": { "backend_used": "llm", "llm_error": null, "warnings": [] },
  "summary": { "checked": 0, "compliant": 0, "potential_non_compliance": 0,
               "not_detected": 0, "not_assessable": 0, "not_applicable": 0,
               "compliance_ratio": 0.0, "required_actions": [] },
  "declarations": [
    {
      "id": "mrp", "label": "Retail sale price (MRP)",
      "clause_ref": { "clause": "Rule 6(1)(e)", "source_url": "...",
                      "gazette": "...", "effective_from": "..." },
      "extracted": "MRP ₹ 45.00 incl. of all taxes",
      "bbox": [0,0,0,0], "panel": "PDP", "ocr_confidence": 0.0,
      "format_check": { "passed": true, "pattern": "MRP ₹ x.xx (incl. of all taxes)" },
      "status": "compliant", "evidence_crop": "<report_id>/crops/mrp.png", "note": "..."
    }
  ],
  "placement": [
    { "id": "placement_clear_space", "label": "Clear space around the net quantity declaration",
      "clause_ref": { "clause": "Rule 8(1), proviso" },
      "status": "not_assessable", "note": "..." }
  ],
  "readability": [
    { "id": "contrast_mrp", "label": "Contrast — MRP",
      "clause_ref": { "clause": "Rule 9(1)(b)" }, "status": "compliant", "note": "..." }
  ],
  "font_analysis": {
    "panel_area_cm2": { "value": 0.0, "uncertainty": 0.0 },
    "panel_input": { "shape": "rectangular", "height_cm": 0.0, "width_cm": 0.0,
                     "clause": "Rule 7(4)" },
    "table_i_band": { "area_band": "100<=A<500", "min_height_mm": 2.5,
                      "min_height_mm_molded": 4.0 },
    "items": [
      { "declaration_id": "mrp",
        "height_mm": { "value": 0.0, "uncertainty": 0.0 },
        "width_ratio": 0.0, "width_ratio_char": "R", "threshold_mm": 2.5,
        "status": "potential_non_compliance", "reason": "..." }
    ]
  },
  "legal_basis": { "statute": "Section 18, Legal Metrology Act, 2009" },
  "officer_actions": [],
  "finalized_at": null, "finalized_by": null, "final_disposition": null,
  "limitations": "…verbatim block…"
}
```

## Rendering rules

- One HTML/Jinja2 template → PDF (WeasyPrint) and the DOCX shares the same
  section order (python-docx).
- Status color-coding consistent across annotated image, summary, and tables.
- Every mm/cm² value prints as `value ± uncertainty unit`; never a bare number.
- Every automated claim shows its clause; an evidence crop is attached when a
  bounding box was found for that declaration (not guaranteed for every one).
- Header banner on every render (PDF, DOCX, HTML): "Decision-support.
  Potential non-compliance flagged for officer verification."
