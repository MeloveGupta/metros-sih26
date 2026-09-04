# reports

Detailed compliance / potential-non-compliance reports. Renders three
synchronized outputs from one canonical JSON record:

- **JSON** - canonical object (also stored in DB / served by API).
- **PDF** - WeasyPrint from one Jinja2 HTML template; watermarked
  "Decision-support - not a final legal finding".
- **DOCX** - python-docx, same section order, for officer annotation.

Full field-by-field structure - cover, executive summary, chain of custody,
calibration basis, per-declaration Rule 6 findings, Rule 7 font/placement
analysis (mm ± uncertainty), legal basis, officer verification, limitations,
appendix - is specified in [`docs/report-spec.md`](../../docs/report-spec.md).

Rules: every mm/cm² value prints as `value ± uncertainty`; every automated claim
cites its rule clause and links to an evidence crop; status vocabulary uses
`potential_non_compliance`, never "violation".
