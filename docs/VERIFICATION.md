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

## Slice 2 (next) — layout-aware region verification

The real fix is not a smarter text heuristic; it is verifying against **layout**:

- run a layout-preserving parser (Docling → blocks with bbox);
- check the value's block/bbox falls in the **expected region/column** (e.g. under the `gross`/`total`
  header, inside the summary block), not merely near a label in the character stream.

First step (in progress): re-measure slice 1 on **layout-preserving OCR** to isolate how much of the
false-alarm was the flattened input vs. the fundamental columnar limitation — then build bbox/column-region
checks where text alone cannot decide.
