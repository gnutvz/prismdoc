# Benchmark — SROIE OCR-recall

Offline quality signal for the **parse / OCR layer**, using public scanned receipts
from **SROIE** (ICDAR 2019 Scanned Receipts OCR and Information Extraction).

## What OCR-recall measures

Given a receipt image and ground-truth entity values (`company`, `date`, `address`,
`total`, …), OCR-recall asks:

> After Ingest + Parse, does the OCR / markdown text **contain** each ground-truth
> value?

This needs **no LLM and no API credentials** — only the optional `docling` extra for
real OCR at run time.

### Two metrics

| Metric | What it checks | When to use |
|--------|----------------|-------------|
| **exact** | Normalized substring match (case / whitespace; numeric totals are decimal-tolerant, e.g. `12.5` ↔ `12.50`) | Strict readout. Under-reports long multi-line fields (`address`, `company`) when OCR is near-complete but not verbatim. |
| **token** | Token-overlap recall: fraction of the field’s significant tokens (length > 2, split on whitespace/punctuation) that appear in the OCR text | Fair readout for multi-token fields. **Primary** metric for comparing OCR quality. |

Exact match alone can show `address` recall near 0.0 even when most address tokens are present in the OCR text. Token-overlap is the primary table column for that reason; exact remains available as a strict diagnostic.

## What this does **not** measure

OCR-recall is **not** end-to-end extraction accuracy. A model may still fail to map
OCR text into schema fields even when every value is present in the text.

Treat OCR-recall as an **upper bound** on what extraction can get right: if the value
is missing from OCR text, no extractor can recover it without hallucination. Measuring
true extraction quality (model vs ground truth) is the next step after this harness.

## Dataset: SROIE

SROIE provides scanned receipt images plus entity annotations (company, date, address,
total). Obtain the public release from the ICDAR 2019 SROIE competition materials
(or a mirror such as the community GitHub / Kaggle mirrors of the same files).

You need, for each receipt:

- the image file, and
- the four entity string values.

## Build a manifest

The harness is **dataset-format-agnostic**. Point it at a JSON **manifest**: a list of
objects with relative image paths and field ground truth.

```json
[
  {
    "image": "images/X51005200938.jpg",
    "fields": {
      "company": "BOOK TA .K (TAMAN DAYA) SDN BHD",
      "date": "25/12/2018",
      "address": "NO.53 55,57 & 59, JALAN SAGU 18...",
      "total": "9.00"
    }
  }
]
```

Image paths are resolved relative to the manifest file's directory. Any labeled
receipt set can use the same shape.

## Run

```bash
pip install 'prismdoc[docling]'
prismdoc-bench --manifest path/to/sroie_manifest.json
# or
python -m prismdoc.bench.sroie --manifest path/to/sroie_manifest.json --limit 50
```

Output is a per-field table (`field | exact | token`) plus overall exact and overall
token means across samples. Prefer **token** as the primary readout.

## Results

Real run — **20 SROIE receipts**, Docling (RapidOCR / PP-OCRv4, CPU), 2026-07-17.

| Field   | exact | token |
|---------|-------|-------|
| company | 0.40  | 0.84  |
| date    | 0.95  | 0.55  |
| address | 0.00  | 0.75  |
| total   | 0.95  | 0.10  |

### How to read this (metric depends on field shape)

A single "overall" number is misleading here because it averages across field types — use the metric
that fits each field:

| Field   | Right metric | Recall | Read |
|---------|--------------|--------|------|
| date    | exact | **0.95** | short/atomic → verbatim match is fair |
| total   | exact | **0.95** | short number → verbatim match is fair (token drops it: too short) |
| company | token | **0.84** | multi-token → OCR captures most of it, not verbatim |
| address | token | **0.75** | long multi-line → exact reads 0.00 but OCR has ~3/4 of the tokens |

**Takeaway:** on real scanned receipts, Docling OCR recovers the key fields well — ~95% for atomic
fields (date, total) and ~75–84% token-recall for long fields (company, address). The remaining work
is *extraction*: mapping this OCR text into exact schema values (where the LLM + grounding confidence +
eval come in). This is the parse-layer upper bound; end-to-end extraction accuracy (model vs ground
truth, with the accuracy-vs-USD frontier) is the next benchmark and needs a model.
