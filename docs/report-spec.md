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
| `not_assessable` | Image quality / calibration insufficient to decide |
| `not_applicable` | Rule does not apply to this commodity/category |
| `officer_confirmed` / `officer_overridden` | Human disposition recorded |

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
  (retail / e-commerce), SKU/barcode value (as text only).

## 2. Executive summary

- Counts by status (checked / compliant / potential_non_compliance /
  not_detected / not_assessable / not_applicable).
- Overall confidence (aggregate) and a one-line plain-language summary.
- **Required officer actions** list (every item needing verification, ranked).
- Thumbnail of the annotated capture (boxes + status colors).

## 3. Evidence & chain of custody

- Original image: filename, SHA-256 hash, capture timestamp, resolution, EXIF
  (camera, focal length, orientation) — **original preserved unmodified**.
- Transformation log: every processing step applied (undistort, homography,
  crop) with parameters, so any derived image is reproducible from the original.
- Integrity note (verbatim): *"The SHA-256 hash proves the file is unaltered
  after capture. It does not by itself prove the subject or location, and is not
  a statement of court-admissibility."*
- Personal/location data handling per **DPDP Act, 2023** (minimized, encrypted,
  role-gated); access to this record is audit-logged.

## 4. Calibration & measurement basis (the moat, shown transparently)

- Scale reference: type (ArUco calibration card), dictionary + marker id,
  **printed marker side (mm)**, detection confidence.
- Recovered `mm_per_pixel`; homography reprojection residual (px) as a quality
  metric; **calibration verdict** (`calibrated` / `rejected — uncalibrated`).
- If rejected: no mm figures are reported; all Rule 7 items become
  `not_assessable` with the reason.
- Global uncertainty model: how the ± interval on each mm figure is derived
  (marker size tolerance + residual + glyph-edge localization).

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

## 6. Font-size & placement analysis (Rule 7) — detailed

- **Principal display panel area:** measured cm² **± uncertainty**, and the
  selected Table-I band with its min-height threshold (normal vs molded).
- Per measured declaration: glyph/numeral height **mm ± uncertainty**,
  measured width/height ratio (Rule 7(3)), threshold, and status. Show the
  measured-vs-required comparison explicitly.
- Absolute floor check (1 mm; 2 mm molded) and width-ratio exceptions
  (`1, i, I, l`).
- Placement check: is the declaration on the principal display panel where
  required (Rule 9-family), with the region highlighted.
- Every Rule 7 figure links back to §4 calibration; if `not_assessable`, say why.

## 7. Legal basis

- Statutory authority: **Section 18, Legal Metrology Act, 2009** (prescribed
  declarations required for pre-packaged commodities).
- Table of every clause cited in this report → source URL, gazette no., effective
  date, applicability. Only officially verified text (no blog/LLM summaries).
- Amendment provenance where relevant (e.g. Rule 7 Table-I via GSR 629(E),
  w.e.f. 01-01-2018).

## 8. Officer verification & disposition

- Per-item officer action: confirm / override / defer, with a mandatory reason
  on override, officer ID, and timestamp — all appended, never editing the
  automated record (append-only).
- Final human disposition summary; space for physical-measurement results if the
  officer takes a caliper reading.
- Signature block (name, designation, jurisdiction, date).

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
  "product": { "name": "...", "brand": "...", "category": "...",
               "batch": "...", "source": "retail", "barcode_text": "..." },
  "evidence": {
    "original": { "file": "...", "sha256": "...", "captured_at": "...",
                  "width": 0, "height": 0, "exif": {} },
    "transformations": [ { "op": "homography", "params": {} } ]
  },
  "calibration": {
    "reference": "aruco_card", "dict": "DICT_4X4_50", "marker_id": 0,
    "marker_mm": 40.0, "detection_confidence": 0.0,
    "mm_per_pixel": 0.0, "homography_residual_px": 0.0,
    "verdict": "calibrated"
  },
  "summary": { "checked": 0, "compliant": 0, "potential_non_compliance": 0,
               "not_detected": 0, "not_assessable": 0, "not_applicable": 0,
               "overall_confidence": 0.0, "required_actions": [] },
  "declarations": [
    {
      "id": "mrp", "label": "Retail sale price (MRP)",
      "clause": "Rule 6(1)(e)",
      "clause_ref": { "source_url": "...", "gazette": "...", "effective_from": "..." },
      "extracted": "MRP ₹ 45.00 incl. of all taxes",
      "bbox": [0,0,0,0], "panel": "PDP", "ocr_confidence": 0.0,
      "format_check": { "pass": true, "pattern": "MRP ₹ x.xx (incl. of all taxes)" },
      "status": "compliant", "evidence_crop": "...", "note": "..."
    }
  ],
  "font_analysis": {
    "panel_area_cm2": { "value": 0.0, "uncertainty": 0.0 },
    "table_i_band": { "area_band": "100<=A<500", "min_height_mm": 2.5,
                      "min_height_mm_molded": 4.0 },
    "items": [
      { "declaration_id": "mrp",
        "height_mm": { "value": 0.0, "uncertainty": 0.0 },
        "width_ratio": 0.0, "threshold_mm": 2.5,
        "status": "potential_non_compliance", "reason": "..." }
    ]
  },
  "legal_basis": { "statute": "Section 18, Legal Metrology Act, 2009",
                   "clauses": [] },
  "officer_actions": [],
  "limitations": "…verbatim block…"
}
```

## Rendering rules

- One HTML/Jinja2 template → PDF (WeasyPrint) and the DOCX shares the same
  section order (python-docx).
- Status color-coding consistent across annotated image, summary, and tables.
- Every mm/cm² value prints as `value ± uncertainty unit`; never a bare number.
- Every automated claim shows its clause and links to an evidence crop.
- Watermark on PDF: "Decision-support — not a final legal finding."
