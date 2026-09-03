# SIH26034 — Software System to Check Compliance of Packaged Commodities

**Problem Statement ID:** 26034
**Title:** Software System to check compliance of Packaged Commodities under Legal Metrology (Packaged Commodities) Rules, 2011 by scanning products, images and labels.
**Organization:** Ministry of Consumer Affairs, Food & Public Distribution
**Department:** Department of Consumer Affairs (DoCA)
**Category:** Software · **Theme:** Miscellaneous
**Submitted ideas:** 1/500 · **Deadline for idea submission:** 20 September 2026
**Dataset / reference:** [consumeraffairs.gov.in — Legal Metrology Act](https://consumeraffairs.gov.in/pages/legal-metrology-act) and the Legal Metrology (Packaged Commodities) Rules, 2011

---

## Background

Packaged commodities are widely sold through retail stores, supermarkets and e-commerce platforms across India. Under the Legal Metrology Act, 2009 and the Legal Metrology (Packaged Commodities) Rules, 2011, every packaged commodity must bear mandatory declarations — name and address of manufacturer/packer/importer, net quantity, Maximum Retail Price (MRP), month and year of manufacture/packing/import, consumer-care details and other prescribed declarations — in a specified format and manner. These declarations ensure transparency, fair trade practices and consumer protection.

Due to the large volume and variety of packaged products, manual inspection and compliance checking by enforcement agencies is time-consuming and resource-intensive. Non-compliance — missing declarations, incorrect font sizes, improper MRP declarations, and similar — is frequently observed. There is scope to develop a system that scans product labels, package images and product listings to identify violations under the Rules, by automatically detecting, extracting and validating mandatory declarations.

## Description

Develop a software application that scans packaged commodity labels, product images and product information to automatically assess compliance with the Legal Metrology (Packaged Commodities) Rules, 2011.

The system should:
- Scan and analyze images of packaged commodities.
- Detect mandatory declarations prescribed under Legal Metrology rules.
- Check correctness, completeness and placement of declarations.
- Identify missing or non-compliant declarations.
- Check readability and font-size requirements.
- Generate compliance reports and violation summaries.
- Maintain a repository of scanned products and compliance history.
- Provide dashboards for enforcement officials.

## Expected Solution

- User-friendly web and/or mobile-based software application.
- Automated extraction and validation of mandatory declarations.
- Rule-based compliance checking for Legal Metrology (Packaged Commodities) Rules, 2011.
- Generation of digital compliance reports in PDF and editable formats.
- Dashboard for monitoring inspections, violations and product compliance details.
- Search and retrieval of previously scanned products and reports.
- Technical documentation describing software architecture and deployment framework.

## Key Functional Requirements

- Image upload and product scanning functionality.
- Extraction of declarations from labels/packaging and detection of mandatory declarations.
- Font-size and readability analysis.
- Detection of missing, misleading or non-standard declarations.
- Generation of compliance/non-compliance reports.
- Attachment of photographs and supporting evidence.
- Repository of scanned products and inspection history.
- Role-based user access and secure authentication.
- Dashboard for monitoring compliance status and enforcement activities.
- Export of reports to PDF and editable formats.

## Mandatory declarations to check

| Declaration | Check |
|-------------|-------|
| Manufacturer/packer/importer name & address | present + format |
| Common/generic name of commodity | present |
| Net quantity (standard unit) | present + unit valid |
| MRP — "₹ … incl. of all taxes" | present + format |
| Month & year of manufacture/pack/import | present + date format |
| Consumer-care details (name, phone/email) | present + format |
| Country of origin (imports) | present |
| Font size / letter height | **measured mm vs. rule threshold** |
| Placement (principal display panel) | region check |

---

## Why this problem (strategy)

- **Low competition.** Reads like paperwork/forms, so most teams skip it. Fewer teams → better odds. (1/500 submitted at time of writing.)
- **Real difficulty is hard computer vision, not forms.** Rule 7 sets minimum letter heights. Checking it means measuring true font height **in millimetres from a photo** — needs scale recovery (reference marker / known package dimension / DPI), not just OCR. That is the technical moat.
- **No dataset dependency.** No provided dataset to wait on — shoot our own label images in any supermarket. Full control over train/eval data.
- **Domain-expert judges.** Judges are Legal Metrology officers. They instantly spot a team that read the Rules vs. one that built a generic OCR demo. Rule-accurate verdicts win.
- **Verdict:** top pick.

## The hard part — font height in millimetres

Rule 7 compliance = letter height ≥ a threshold that scales with net quantity / package size. From an image:

1. Recover real-world scale — reference object of known size in frame, known package dimensions, or capture DPI.
2. Detect glyph bounding boxes (character-level).
3. Convert pixel glyph height → millimetres using the scale.
4. Compare against the net-quantity-dependent threshold from the Rules.

This is the differentiator. A generic OCR app cannot do it; modelling scale recovery + character-level measurement proves real rule comprehension.

## Notes

- Youtube link: (none provided)
- Contact info: (none provided)
- Neighbouring PS from same ministry: SIH26035 (NAWI test reports, OIML R-76), SIH26036 (online verification of weighing/measuring instruments).
