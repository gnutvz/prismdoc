# Benchmark — SROIE OCR-recall

Offline quality signal for the **parse / OCR layer**, using public scanned receipts
from **SROIE** (ICDAR 2019 Scanned Receipts OCR and Information Extraction).

## What OCR-recall measures

Given a receipt image and ground-truth entity values (`company`, `date`, `address`,
`total`, …), OCR-recall asks:

> After Ingest + Parse, does the OCR / markdown text **contain** each ground-truth
> value?

Matching is normalized (case / whitespace) and, for numeric totals, decimal-tolerant
(`12.5` counts as present when the text has `12.50`).

This needs **no LLM and no API credentials** — only the optional `docling` extra for
real OCR at run time.

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

Output is a per-field recall table plus overall mean recall across samples.

## Results

*Placeholder — Tech Leader fills with a real SROIE run.*

| Field   | OCR-recall | n |
|---------|------------|---|
| company | —          | — |
| date    | —          | — |
| address | —          | — |
| total   | —          | — |
| **overall** | —      | — |

Notes / date of run / Docling version: _
