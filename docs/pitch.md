# SIH26034 — Pitch / PPT Master Doc

Everything needed to build the Smart India Hackathon idea-submission PPT.
Grounded in the actual Legal Metrology (Packaged Commodities) Rules, 2011
(`docs/lmpc-2011.pdf`) — every threshold below is real, not invented.

> **Product name (working):** **Metros** — millimetre-grade Legal Metrology
> compliance scanner. *(swap if the team prefers another name)*

---

## 0. The 15-second version

> **Not another OCR app — a calibrated measuring instrument.**
> Photograph a packaged product; Metros measures each mandatory declaration
> to the **millimetre (with a stated uncertainty)**, checks it against the Legal
> Metrology (Packaged Commodities) Rules, 2011, and produces a **clause-cited,
> evidence-backed record of *potential* non-compliance — flagged for officer
> verification, fully offline.**

> **Legal guardrail (say this, believe this):** the system is *decision-support*.
> It flags **potential** non-compliance with a confidence and an evidence crop;
> it does **not** make final legal findings and cannot verify actual
> contents/weight from an image. Authorised physical measurement remains
> necessary for any enforcement action. Never demo the word "VIOLATION" as a
> verdict — say **"flagged for officer verification."**

---

## 1. USP (Unique Selling Proposition)

**"We measure Legal Metrology declarations to the millimetre, deterministically,
offline — turning a photo into traceable, clause-cited evidence an officer can
verify and act on."**

Four pillars — each is something a generic "upload → GPT says compliant" demo
**cannot** claim:

| Pillar | What it means | Why judges (LM officers) care |
|--------|---------------|-------------------------------|
| **Metric, not guessed** | Letter height in real mm via ArUco scale — pure geometry | It's *measurement*, defensible in an inquiry |
| **Deterministic verdict** | Same image → same verdict, every time, citing the exact clause | Reproducible ⇒ admissible as evidence |
| **Rule-true** | Area-based Table-I (GSR 629(E), 2018), Rule 6, Rule 7(3) | Proves we read the gazette, not a blog summary |
| **Offline / on-prem** | No cloud, no data leaves the device | A govt tool can't ship product photos to a foreign API |

## 2. Hero feature (the one thing you demo live)

**A millimetre ruler for labels.**

Snap a product with an ArUco reference card in frame → the app boxes each
declaration, prints its letter height **in real millimetres with an uncertainty
interval**, and shows ✅ / ⚠️-flag against the exact Rule 7 threshold for that
panel's area. **We explicitly reject uncalibrated measurement** — no marker in
frame, no number.

**On-stage moment:**
> *"MRP letters measure 0.8 mm ± 0.2 mm. Rule 7 Table-I requires ≥ 1.0 mm for a
> 42 cm² panel → flagged as potential non-compliance for officer verification."*

No other team can put a trustworthy, uncertainty-bounded number on screen. That
single moment is the win — and the honest framing is exactly what a Legal
Metrology officer trusts.

## 3. The problem (slide: Problem Statement)

- Every packaged commodity in India must bear mandatory declarations (Rule 6)
  in a prescribed size and manner (Rule 7).
- Retail + supermarket + e-commerce volume is enormous; manual inspection by
  enforcement agencies is slow and inconsistent.
- Common violations — missing declarations, undersized fonts, malformed MRP —
  go undetected.
- **The hard, unautomated part:** checking Rule 7 requires measuring letter
  height in **millimetres from an image** — no existing app does this.

## 4. The solution (slide: Proposed Solution)

An offline-first web/mobile app:

1. **Capture** — photograph the label with an ArUco scale card in frame.
2. **Measure scale** — detect the marker → `mm_per_pixel` (deterministic).
3. **Read** — OCR extracts declaration text + per-character pixel boxes.
4. **Measure** — panel area (cm²) *and* glyph height (mm) from the scale.
5. **Validate** — deterministic rule engine checks Rule 6 presence/format and
   Rule 7 height, citing each clause.
6. **Report** — PDF + editable DOCX with embedded evidence crops; stored in a
   searchable repository; dashboard for officers.

**Design law: LLM extracts, code decides.** AI reads the text; **geometry
measures the size**; a deterministic engine issues the verdict.

## 5. The differentiator — why it's hard (slide: Uniqueness)

Rule 7 compliance is a **two-measurement geometry problem**, both from one scale:

**(a) Panel area → threshold band** (Rule 7 Table-I, GSR 629(E), w.e.f. 01-01-2018):

| Principal display panel area A (cm²) | Min letter height (mm) | If molded (mm) |
|--------------------------------------|------------------------|----------------|
| A < 50                               | 1.0                    | 2.0            |
| 50 ≤ A < 100                         | 1.5                    | 3.0            |
| 100 ≤ A < 500                        | 2.5                    | 4.0            |
| 500 ≤ A < 2500                       | 4.0                    | 6.0            |
| A ≥ 2500                             | 6.0                    | 6.0            |

**(b) Glyph height (mm ± uncertainty)** vs. that band — plus Rule 7(3): absolute
1 mm floor (2 mm molded), width ≥ ⅓ height (except `1, i, I, l`).

> A monocular photo has **no absolute scale** — the same letter is 2 mm or 20 mm
> depending on camera distance, and both render identical pixels. No AI recovers
> information the image doesn't contain. A known-size **ArUco/AprilTag card** (in
> the same plane as the declaration) injects the missing scale; **homography /
> perspective correction** flattens the panel; the rest is deterministic
> geometry with a reported error bound. **This is why an LLM cannot do it and we
> can.**

**Two honesty rules that build trust (not weaken the pitch):**
- **Reject uncalibrated measurement.** No marker in frame → no mm number.
- **Do not use barcode width as the scale** — EAN-13 magnification/printing
  varies (it is *not* universally 37.29 mm). Only a purpose-printed reference
  card or ruler is trustworthy.
- Reliable for **guided, planar** captures; degraded for curved / shiny /
  crumpled / transparent / steeply angled packs → report low confidence, require
  officer review, never auto-conclude.

## 6. What we check (slide: Features)

**Rule 6 — mandatory declarations**

| Declaration | Clause | Check |
|-------------|--------|-------|
| Manufacturer/packer/importer name & address | 6(1)(a) | present + format |
| Country of origin (imports) | 6(1)(aa) | present if imported |
| Common/generic name | 6(1)(b) | present |
| Net quantity (standard unit) | 6(1)(c) | present + unit |
| Month & year of mfg/pack | 6(1)(d) | present + format |
| Best-before / use-by (perishables) | 6(1)(da) | present + format |
| MRP — "₹ … incl. of all taxes" | 6(1)(e) | present + format |
| Consumer-care (name, addr, phone, email) | 6(2) | present + format |

**Rule 7 — size & placement:** letter height (mm) vs. area band, 1 mm floor,
width ratio, principal-display-panel placement.

**Platform features:** image upload + in-field capture, **detailed** compliance
reports (JSON + PDF + DOCX) with per-declaration findings, mm ± uncertainty,
evidence crops, chain of custody and clause citations — full structure in
[`docs/report-spec.md`](report-spec.md) — searchable product/inspection
repository, role-based access (officer/admin/auditor), enforcement dashboard,
immutable audit log.

## 6b. Feature shortlist — what we build, in order (slide: Scope)

Ranked by impact × uniqueness × feasibility. All sit on one small baseline
(image capture, OCR, field extraction, versioned rules, evidence crops, officer
confirmation, repository, PDF/DOCX export).

| # | Feature | Role | Verdict |
|---|---------|------|---------|
| 1 | **Calibrated font-height assistance** | the hero; mm ± uncertainty vs Rule 7 | **Must have** |
| 2 | **Evidence-first inspection record** | image → OCR → field → rule → officer decision, one traceable record (SHA-256 hash, timestamp, optional geo) | **Must have** |
| 3 | **Batch history & change detection** | spot label drift across scans of the same SKU/batch | Strongly recommended |
| 4 | **Transparent risk-based prioritisation** | explainable "inspect these first, and why" queue | Strongly recommended |
| 5 | E-commerce listing comparison | check online listing images vs checklist | Optional (after core) |

**Defer / avoid (say why if asked):**
- *Live e-commerce scraping* — CAPTCHA / anti-bot / platform terms break a live
  demo; use authorised static sample listings instead. **Defer.**
- *Multi-view 3D reconstruction* — high risk on curved/shiny packs. **Avoid now.**
- *Counterfeit detection* — needs forensic genuine/fake data; anomaly ≠ proof. **Avoid now.**
- *Blockchain* — hashing + audit log + RBAC is simpler and more relevant. **Avoid.**
- *LLM-driven legal decisions* — verdicts must be deterministic + versioned; an
  LLM may assist search/explanation but must never decide compliance. **Avoid.**

**Evidence-first note (Feature 2):** a SHA-256 hash proves the file is unaltered
*after capture* — it does **not** prove the photo is of the claimed product or
place, and a hash alone is not court-admissibility. Preserve originals, keep
transformation metadata, follow DPDP Act 2023 for any personal/location data.

## 7. Technical approach (slide: Technical Approach)

**Pipeline**
```
capture (product + ArUco marker)
  → [OpenCV aruco]   marker → mm_per_pixel
  → [PaddleOCR]      text + per-char pixel boxes
  → [CV]             panel area cm² + glyph mm  (Rule 7)
  → [regex/NER]      text → fields (MRP, net qty, dates, care)
  → [rule engine]    verdict per clause + evidence crop
  → [reports]        PDF + editable DOCX
  → API + DB + dashboard
```

**Stack**

| Layer | Choice |
|-------|--------|
| Language | Python (one language across CV + rules + API) |
| Scale / mm | OpenCV `cv2.aruco` |
| OCR + char boxes | PaddleOCR (offline, free) |
| Field parsing | regex + spaCy NER (optional Gemini fast-path) |
| Rule engine | Python + YAML catalog (`rules/lmpc-2011.yaml`) |
| API | FastAPI |
| DB / storage | PostgreSQL + MinIO |
| Frontend | React + Vite (PWA capture) |
| Reports | WeasyPrint (PDF) + python-docx (editable) |
| Auth | JWT + RBAC |
| Deploy | Docker Compose (offline/on-prem) |

*Why Python, not Rust/Go: the OCR + OpenCV ecosystem is Python-first; the vision
core stays Python, and a Rust/Go API gateway is a clean v2 wrapper if it scales.*

## 8. Feasibility & viability (slide: Feasibility)

- **Feasible now:** ArUco (OpenCV built-in), PaddleOCR, YAML rules — all mature,
  all offline. No model training required to demo.
- **No dataset dependency:** shoot our own label images in any supermarket; full
  control over test data (a scoring advantage — most PS depend on a given set).
- **Risks & mitigations:**
  - *Scale needs a reference* → standard capture protocol: print an ArUco card,
    keep it in frame. Fallback: known package dimension.
  - *Curved / reflective labels* → image preprocessing + flag low-confidence for
    manual review; never auto-fail silently.
  - *Rules change* → thresholds live in YAML; an officer edits without redeploy.
  - *False positives erode trust* → every flag cites clause + evidence crop +
    confidence; human override is logged; mm always carries an uncertainty band.
  - *"Not detected in image" ≠ "legally absent"* → the engine separates
    detection from legal conclusion and routes low-confidence items to mandatory
    officer review.
  - *Privacy* → DPDP Act 2023: encrypt originals/metadata, minimise
    location/personnel data, role-gated access, audit every export.

## 9. Impact & benefits (slide: Impact)

- **Enforcement:** inspect far more products, consistently, with defensible
  evidence — frees officer time from manual measuring.
- **Consumers:** faster detection of MRP/quantity/labeling fraud → fair trade.
- **Industry:** manufacturers can self-check pre-market (pre-compliance).
- **Government:** auditable inspection history, dashboards, trend analytics by
  brand/region/violation type.
- **Scalable to e-commerce:** batch-scan online product listing images.

## 10. Why we win (judge lens)

Judges are Legal Metrology officers. They instantly separate a team that read
the Rules from one that wrapped an OCR API. We show:
- a **millimetre number** they trust,
- the **exact clause** (Rule 7 Table-I, GSR 629(E)) behind it,
- a report they could **actually file**,
- running **offline**, respecting data sovereignty.

**Positioning line to repeat:** *measurement-grade decision-support, not
AI-guessed verdicts.* Never say "AI figures out the font size," and never say
"VIOLATION" as a final finding — say **"potential non-compliance, flagged for
officer verification."**

**Team positioning statement (put on a slide):**
> *"Rather than another OCR pass/fail tool, our platform helps an officer capture
> traceable evidence, identify potential declaration and font-size issues using
> calibrated measurement with stated uncertainty, compare declarations across
> batches, and focus limited inspections where documented history indicates
> higher risk."*

## 11. References (slide: Research & References)

- **Section 18, Legal Metrology Act, 2009** — the statutory hook: prohibits
  manufacture, packing, import, sale, distribution, delivery or display for sale
  of pre-packaged commodities unless prescribed declarations are made in the
  prescribed manner. (Report headers/rule metadata cite this as source authority.)
- Legal Metrology (Packaged Commodities) Rules, 2011 (consolidated with
  amendments) — Dept of Consumer Affairs. In-repo: `docs/lmpc-2011.pdf`.
- Key amendment: GSR 629(E), 23-06-2017 (w.e.f. 01-01-2018) — Rule 6 & Rule 7
  Table-I as used above. Rule 6(10)/6(10A) — e-commerce display & country of origin.
- **Verify before coding** (confirm latest consolidated text, schedules,
  exemptions, commencement dates from official notifications):
  - [Dept of Consumer Affairs](https://consumeraffairs.nic.in/)
  - [Indian Institute of Legal Metrology — Acts & Rules](https://iilm.gov.in/more/act-rules)
  - [Gazette of India / e-Gazette](https://egazette.nic.in/)
  - [MeitY — DPDP Act 2023 & privacy](https://www.meity.gov.in/)
  - [GS1 barcode standards](https://www.gs1.org/standards/barcodes)
- **DPDP Act, 2023** — governs any personal / location / officer data the tool
  stores; encrypt, minimise, role-gate.
- ArUco / AprilTag markers — OpenCV `cv2.aruco` (camera-independent metric scale
  recovery) + homography for perspective correction.
- PaddleOCR — open-source OCR with character-level bounding boxes.

---

## Appendix — slide-by-slide map (SIH idea format)

| # | Slide | Pull from |
|---|-------|-----------|
| 1 | Title / team / PS SIH26034 | header |
| 2 | Problem Statement | §3 |
| 3 | Proposed Solution + USP | §1, §4 |
| 4 | Hero feature / demo shot | §2 |
| 5 | Technical Approach (pipeline + stack) | §7 |
| 6 | Uniqueness — the mm moat + Table-I | §5 |
| 7 | Features / what we check | §6 |
| 8 | Feasibility & Viability | §8 |
| 9 | Impact & Benefits | §9 |
| 10 | Why we win / References | §10, §11 |

## Appendix — 60-second demo script

1. Real product + ArUco card in frame — snap.
2. Overlay: each declaration boxed, letter height in **mm ± uncertainty** labeled.
3. One field below the Rule 7 threshold → ⚠️ flagged, with clause + measured-vs-required.
4. Tap → PDF report generates, evidence crop + confidence + "officer verification required" embedded.
5. Close: *"A calibrated, clause-cited flag of potential non-compliance an officer can verify and act on — offline."*
