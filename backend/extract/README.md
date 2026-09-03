# extract

OCR text -> structured declaration fields (MRP, net quantity, mfg date,
manufacturer, consumer-care). regex + spaCy NER offline; optional Gemini
fast-path when GEMINI_API_KEY is set. Extraction only — never verdicts.
