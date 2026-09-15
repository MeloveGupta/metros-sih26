# extract

OCR text -> structured declaration fields (MRP, net quantity, mfg date,
manufacturer, consumer-care). Claude (Anthropic API) is the default reader
when `ANTHROPIC_API_KEY` is set; deterministic regex parsers are the fallback
when no key is set or a Claude call fails. Extraction only — never verdicts.
