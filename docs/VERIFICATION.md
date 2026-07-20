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

## Slice 3 (measured — it works) — column/cell verification

Layout fixed the false alarm but not the net-as-total *catch*: in `| total | 7,50 | 0,75 | 8,25 |` all
three figures share one `total` label, so a value taken from the net column would also "verify".
Distinguishing them is a **column** question. `TableColumnVerifyStage` parses the markdown table into cells,
finds which **column** the value sits in, and checks that column's **header**: a `total` value under a
`gross`/`total` header is `column_verified`; under a `net`/`subtotal`/`vat` header it is `column_mismatch`.

Measured on **real Docling invoice tables** (n=12) by feeding each invoice's ground-truth **gross** (the
true total) and **net** (the wrong column):

| Fed value | Should be | Result |
|---|---|---|
| GROSS (true total) | `column_verified` | **12/12** |
| NET (wrong column) | `column_mismatch` | **10/12** |
| both correct (gross verified **and** net flagged) | | **10/12** |

**The verifier catches the net-as-total confusion 10/12 while never mis-flagging the true total (12/12
verified, 0 false alarms).** The 2 misses are `column_no_label` (the net value's column header did not map
to a reject label) — a weak signal, never a false `verified`. This is the semantic-verification win: it
checks the value came from the *right column*, not merely that the number is present.

## The arc, in one table

| Approach | False alarm on true total | Catches net-as-total |
|---|---|---|
| Label window, flattened OCR | 100% (43/43) | — (unusable) |
| Label window + layout parse | 0% (0/12) | no (same row, one label) |
| **Column verifier + layout parse** | **0% (0/12)** | **yes — 10/12** |

The lesson mirrors the reviewer's thesis: region/record correctness needs **layout + positional
(column) structure**, not text presence. On flattened OCR none of this is recoverable; on a
layout-preserving parse the column header is exactly the missing signal.

## Closing the loop — verification as a repair trigger

Detection is only useful if it drives a fix. `RepairStage` previously re-prompted only *missing* or
*low-confidence* fields — never a **confident-but-wrong** value (the exact class the column check catches).
It now also triggers on a verification mismatch (`label_mismatch` / `column_mismatch`) and passes the model
a corrective **hint** ("your previous value looks like it came from a *net*/*subtotal* column — re-read it
from the correct column").

End-to-end demo on real Docling invoices (n=12): inject the net-as-total error, run the column verifier,
then repair:

| Stage | Result |
|---|---|
| verifier caught the injected error (`column_mismatch`) | **10/12** |
| repair fixed it back to the true gross total | **10/12** |
| **fixed among caught** | **10/10 (100%)** |

Every error the verifier caught, repair corrected — the 2 misses were `column_no_label` (never flagged, so
never repaired, never wrongly "fixed"). This is the answer to "repair doesn't help confident-but-wrong":
it does, once a semantic signal tells it *which* field is wrong and *why*.

## Extended to subtotal and tax

`DEFAULT_COLUMN_LABELS` now also covers `subtotal` (the net / pre-tax column) and `tax` (VAT/GST). Measured
the same way on real Docling tables (n=12), feeding each field its correct column value vs. the gross:

| Field | correct value → `column_verified` | gross (wrong col) → `column_mismatch` | both |
|---|---|---|---|
| subtotal (net) | 10/12 | 12/12 | 10/12 |
| tax (VAT) | **12/12** | 12/12 | **12/12** |

Tax discriminates perfectly; subtotal's 2 misses are `column_no_label` (never a false verify — the same
two invoices whose net cell has an unmapped header).

## Feeding verification into confidence

A verification mismatch also **caps per-field confidence** (`ConfidenceStage`): a `label_mismatch` /
`column_mismatch` field is scored `0.2` (below the `0.5` flag threshold, and below the `0.4` "ungrounded"
score — a grounded-but-wrong-column value is worse than an ungrounded one), *after* any calibration, with
reason `verification_mismatch`. So a confident-but-wrong grounded value (net-as-total would otherwise be
`0.9`) is now scored low and flagged, feeding the same `low_confidence` list repair consumes.

Measured end-to-end on the 12 Docling invoices (inject `total = net` vs `total = gross`, run column verify
+ confidence):

| Injected value | Confidence | Flagged low-confidence |
|---|---|---|
| gross (correct total) | **0.9** (grounded, verified) | **0/12** |
| net (confident-but-wrong) | **0.2** (mismatch cap) | **10/12** |

Clean separation: the correct total keeps high confidence and is never flagged, while the wrong-column
value is capped and flagged. The 2 net misses are the `column_no_label` invoices (verifier didn't catch,
so confidence isn't capped — never a false flag on the correct value).

*This measurement first surfaced a separate bug:* the grounding matcher `value_in_text` did not match
**locale number formats** (`8,25`, `57 483,07`, `1.767,34`), so on European invoices even the correct gross
scored ungrounded and everything was flagged. Fixed — `value_in_text` is now locale-aware (US + EU) — which
is what unlocked the clean split above. A good reminder that measuring the wiring end-to-end found a real
bug the unit tests didn't.

## How often does this fire in practice? (honest organic-error measurement)

The catch/repair/confidence numbers above use **injected** net-as-total errors. The fair question is: how
often does that error happen **organically**, and does the loop lift real accuracy? We ran the full
pipeline (`extract → verify → confidence → repair`) with a real cheap model on data where models actually
err — no injection:

| Dataset | Field(s) | Accuracy before → after | Organic wrong-place errors |
|---|---|---|---|
| Invoices, single-item | total / subtotal / tax | 36/36 → 36/36 | 0 |
| Invoices, multi-item (4–7 rows) | total / subtotal / tax | 42/42 → 42/42 | 0 |
| SROIE receipts | total | 39/40 → 39/40 | 1 (a `RM 3.90` vs `3.9` ground-truth-formatting artifact — the prediction was right) |

**Honest conclusion: the wrong-place error verification catches is rare organically.** On clean,
well-labelled financial documents the model reads `total` / `net` / `gross` from the right column almost
every time; the net-as-total confusion mainly shows up under **adversarial** conditions (an ambiguous
schema + flattened OCR + a strong model over-reasoning — where we measured ~42% net-as-total). And the
fields models *do* get wrong on receipts (company, address) are OCR/reading errors, which label/column
verification is not designed to catch.

So what is the verification layer worth? **High-precision, low-recall insurance for one specific,
high-stakes error class** — financial column confusion (net vs. gross = wrong money). When it occurs it is
caught and fixed (12/12 gross verified, 10/12 net flagged, 10/10 caught errors repaired, 0 false alarms),
but it fires rarely, and it does not address OCR/text-field errors. That is the honest scope: a cheap
guardrail against a catastrophic-but-rare mistake, not a general accuracy lifter. The bigger organic
accuracy gains live in the parse/OCR layer and in the harder text fields — a separate line of work.

## What's still open

- The column verifier needs a **layout-preserving parser** (Docling/table output); on flattened OCR it
  falls back to `no_table`/`value_not_in_table` (honest, not a false verify).
- Default column labels cover `total`, `subtotal`, `tax`; other fields need their own `expect_col`/`reject_col`.
- The 2 `column_no_label` misses want richer header handling (multi-item tables, merged summary blocks);
  a `Net price` (unit-price) column can also match `subtotal`'s `net` label — tighten per-domain if needed.
