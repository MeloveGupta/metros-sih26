# SIH26034 — Pitch / PPT Master Doc

Everything needed to build the Smart India Hackathon idea-submission PPT.
Grounded in the actual Legal Metrology (Packaged Commodities) Rules, 2011
(`docs/lmpc-2011.pdf`) — every threshold below is real, not invented.

> **Product name (working):** **MetroScan** — millimetre-grade Legal Metrology
> compliance scanner. *(swap if the team prefers another name)*

---

## 0. The 15-second version

> **Not another OCR app — a calibrated measuring instrument.**
> Photograph a packaged product; MetroScan measures each mandatory declaration
> to the **millimetre**, checks it against the Legal Metrology (Packaged
> Commodities) Rules, 2011, and produces a **clause-cited, court-ready
> compliance report — fully offline.**

---

## 1. USP (Unique Selling Proposition)

**"We verify Legal Metrology compliance to the millimetre, deterministically,
offline, with evidence an enforcement officer can act on."**

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
declaration, prints its letter height **in real millimetres**, and stamps
✅/❌ against the exact Rule 7 threshold for that panel's area.

**On-stage moment:**
> *"MRP letters measure 0.8 mm. Rule 7 Table-I requires 1.0 mm for a 42 cm²
> panel. → VIOLATION."*

No other team can put a trustworthy number on screen. That single moment is the win.

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

**(b) Glyph height (mm)** vs. that band — plus Rule 7(3): absolute 1 mm floor
(2 mm molded), width ≥ ⅓ height (except `1, i, I, l`).

> A monocular photo has **no absolute scale** — the same letter is 2 mm or 20 mm
> depending on camera distance, and both render identical pixels. No AI recovers
> information the image doesn't contain. The ArUco marker injects the missing
> scale; the rest is deterministic geometry. **This is why an LLM cannot do it
> and we can.**

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

**Platform features:** image upload + in-field capture, compliance/non-compliance
reports (PDF + DOCX) with evidence photos, searchable product/inspection
repository, role-based access (officer/admin/auditor), enforcement dashboard,
immutable audit log.

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
  - *False positives erode trust* → every verdict cites clause + evidence crop;
    human override is logged.

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

**Positioning line to repeat:** *measurement-grade, not AI-guessed.* Never say
"AI figures out the font size."

## 11. References (slide: Research & References)

- Legal Metrology Act, 2009; Legal Metrology (Packaged Commodities) Rules, 2011
  (consolidated with amendments) — Dept of Consumer Affairs. In-repo:
  `docs/lmpc-2011.pdf`.
- Key amendment: GSR 629(E), 23-06-2017 (w.e.f. 01-01-2018) — Rule 6 & Rule 7
  Table-I as used above.
- [consumeraffairs.gov.in — Legal Metrology Act & Rules](https://consumeraffairs.gov.in/pages/legal-metrology-act)
- ArUco markers — OpenCV `cv2.aruco` (camera-independent metric scale recovery).
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
2. Overlay: each declaration boxed, letter height in mm labeled.
3. One field fails Rule 7 → red, with clause + measured-vs-required.
4. Tap → PDF report generates, evidence crop embedded.
5. Close: *"A millimetre-accurate, clause-cited violation an officer can act on — offline."*
