# scripts

Dev + data-capture helpers.

## gen_calibration_card.py — printable ArUco scale reference

Generates the MetroScan **calibration card**: an A4 sheet with a centred
ID-1 / CR80 (85.60 x 53.98 mm) card outline and a known-size ArUco marker. Print
at 100%, cut out, and place it in the same plane as a product declaration so the
vision pipeline can recover `mm_per_pixel` (and a homography for perspective) —
with **no PII**, unlike using an Aadhaar or other identity card.

```bash
python scripts/gen_calibration_card.py                       # out/calibration_card.png (A4, 300 dpi, 40 mm marker)
python scripts/gen_calibration_card.py --dpi 600 --marker-mm 40
python scripts/gen_calibration_card.py --dict DICT_5X5_50 --marker-id 7 --out out/card.png
```

Print at **100% / actual size** (no fit-to-page), then verify the printed marker
side with a ruler before trusting any measurement. Deps: `opencv-contrib-python`,
`numpy` (in `requirements.txt`).

## Planned

- Batch-ingest supermarket photos into the repository.
- Seed database with sample products / synthetic inspection history.
- Field capture-protocol helper (guided planar shot).
