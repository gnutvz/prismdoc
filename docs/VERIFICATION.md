# Semantic verification — did the value come from the *right place*?

Grounding, evidence-first provenance, and `sum_equals` all prove a value is *present* and *self-consistent*.
None prove it came from the **right label / region / record**. The clearest failure (from
[ABLATION.md](ABLATION.md)): a strong model read an invoice's **net** (pre-VAT) figure as `total` — the
value exists, `net + VAT = gross` even holds, but it was taken from the wrong column.

`LabelVerifyStage` (`prismdoc.stages.verify`) is **slice 1** of a verification layer: using the source
span the extractor cites per field (`Record.field_evidence`), it checks whether that span sits next to a
**label the field should have** and not next to an **anti-label** (a `total` value on a `net` line). Status
per field: `verified | label_mismatch | no_label | unlocated | no_evidence`.

## Measured result — an honest negative

We ran evidence-mode extraction on **45 real invoices** and applied the verifier to the `total` field:

| | wrong `total` (net-as-gross) | correct `total` |
|---|---|---|
| flagged `label_mismatch` | caught | **43/43 (100% false alarm)** |

The verifier flagged **everything**, including correct extractions — useless as-is. **Why**, exactly:

- The invoice summary is a **table with columns** `[net worth | VAT | gross worth]` and a `Total` row.
  The value `8.25` is the *gross* cell.
- The OCR here is **flattened** (no line structure), so the window before the value sweeps up the column
  **headers**. Real captured window for a *correct* total:
  ```
  ...vat net worth gross worth 7,50 10% 0,75 8,25 total $7,50 $0,75 $8,25   → reject fired: [net, vat]
  ```
- Worse, in the `Total $7,50 $0,75 $8,25` row **all three numbers share one label** (`Total`). Only
  **column position** distinguishes net / VAT / gross — there is no inline text label next to `8.25` that
  says "gross".

## What this proves (it is not a dead end)

Text-window label proximity — and even "nearest text label wins" — **cannot** resolve a columnar summary,
because the disambiguating signal is **position within a row/column**, not text adjacency. This is exactly
the deeper point a reviewer raised: region verification needs **layout/positional structure** (block/bbox,
column alignment), not just text. It also maps onto the **Tabular** archetype we mark *Partial* in the
[README](../README.md): an invoice's summary block is a tabular sub-problem.

`LabelVerifyStage` remains correct for its designed case — a label on the **same line** as its value, with
**layout-preserving** parse output (its unit tests cover exactly that). It is shipped as infrastructure
(the `field_verification` signal), not as a solved invoice check.

## Slice 2 (measured) — layout parse kills the false alarm

We re-ran the **exact same slice-1 verifier** but replaced the flattened `ocr_words` with a
**layout-preserving parse** (Docling), on 12 invoices:

| Parse input | False alarm (correct `total` flagged) |
|---|---|
| Flattened `ocr_words` | **43/43 = 100%** |
| **Docling (layout)** | **0/12 = 0%** |

So the 100% false alarm was the **flattened input, not the verifier logic**. With layout, the value's line
becomes a table row — `| total | $7,50 | $0,75 | $8,25 |` — where the `total` label is inline and the
`net`/`VAT` column *headers* sit on a separate row, outside the window. No anti-label bleeds in, so correct
totals verify. **Takeaway: run `LabelVerifyStage` on layout-preserving parse output, never on flattened OCR.**

## Slice 3 (next) — column/cell awareness for tabular summaries

Layout fixes the false alarm but **not** the original net-as-total catch. Look at the verified window:
`| total | $7,50 | $0,75 | $8,25 |` — all three figures (net / VAT / gross) share the one `total` label, so
a `total` value taken from the *net* column would also "verify". Distinguishing them is a **column**
question: parse the summary into cells and check the value sits under the `gross`/`total` **column header**.
That is tabular-archetype work (the summary block is a table), and the honest next slice.

(Note: on the cleaner Docling text the model also made *far fewer* total errors than on the flattened OCR,
so recall could not be measured on this subset — better parse both reduces the error and is required for
the verifier. The columnar limitation above is structural, visible directly in the windows.)
