# Ablation: does each module actually help? (two domains)

The [benchmark](BENCHMARK.md) shows the pipeline works end to end. This study asks the harder question a
reviewer should ask: **when you turn each module on, does accuracy actually go up — and does the answer
hold across document types?** We ablate five modules on two domains with real ground truth, measuring the
change in field accuracy versus a **cheap-model-only baseline**.

The honest headline: **not every module helps, and the effect is domain-dependent.** One module
(the naïve deterministic tier) actively hurts. This page reports what we measured, including the parts
that are unflattering.

## Setup

- **Domains:** SROIE receipts (n=60; fields company/date/address/total) and invoices
  (n=45, `mychen76/invoices-and-receipts_ocr_v1`, genuine invoices only; fields invoice_no/date/total).
- **Baseline:** cheap model (`gemini-3-flash`) extraction only.
- **Backend:** models via CLI subscriptions (cheap = gemini-flash, strong = claude-opus, third = claude-sonnet).
- **Metric:** type-aware field accuracy (`prismdoc.eval.metrics.values_match`), averaged over fields × docs.
- **Estimated cost** uses reference API token prices (the CLI runs were free); read it as relative, not a bill.

## Per-module result

![Per-module ablation across two domains](img/ablation.png)

| Module (vs cheap) | SROIE receipts (n=60) | Invoices (n=45) | Reading |
|---|---|---|---|
| **cheap** (baseline) | 80.0% | 98.5% | cheap already nails clean invoices |
| **+hybrid** (deterministic tier) | **−26.7** | **−32.6** | the generic "first number/date" matcher grabs the wrong token — **hurts badly** |
| **+repair** (re-prompt failed fields) | −0.4 | 0.0 | ~neutral: most errors are confident-but-wrong, which repair doesn't target |
| **ensemble** (3-model vote) | **+5.0** | 0.0 | helps on receipts; nothing to gain at the invoice ceiling |
| **cascade** (escalate low-grounding) | +1.2 | 0.0 | lifts receipts at ~1/5 of strong's cost; on invoices grounding is high so it never escalates (spends ~nothing) |
| **strong** (ceiling) | +7.1 | −13.3 | strong is the ceiling on receipts — but **worse** on invoices (see below) |

### What each row actually means

- **Hybrid hurts — and this confirms a known weakness.** The default deterministic matchers take the
  *first* number/date in the document. On a receipt, "total" grabs the `18` from a date; on an invoice
  it grabs a line-item figure. Field accuracy for `total` collapses to **0%** under hybrid in both
  domains. The lesson is exactly the caveat in the README: the generic matchers are candidate generators,
  not extractors — only anchored/labelled regex (`Total\s*[:\-]\s*([0-9.,]+)`) is safe. As a blind
  drop-in, hybrid is a net negative here.
- **Repair is ~neutral.** RepairStage re-prompts only *missing / low-confidence* fields. On these sets
  the cheap model's mistakes are mostly **confident and wrong** (a plausible-but-incorrect value), which
  never trips the low-confidence trigger — so there is nothing for repair to fix. It neither helps nor
  hurts.
- **Ensemble helps where there is headroom.** On receipts (cheap = 80%), 3-model majority lifts company
  73→78, address 60→72, date 92→95 for **+5.0** overall. On invoices the cheap model is already at 98.5%,
  so the vote changes nothing. Ensemble buys accuracy at **3× the cost** — worth it only when the
  baseline has room.
- **Cascade is the cost story, not the accuracy story.** It escalated 12/60 receipts (grounding < 0.75)
  for **+1.2** at roughly a fifth of all-strong cost; on invoices grounding was high on every doc, so it
  escalated **0** and spent essentially the cheap price for near-ceiling accuracy. Cascade adapts spend
  to the document.
- **"Strong" is not universally better.** On invoices, asked for a single `total` field, the strong model
  systematically returned the **net (pre-VAT) subtotal** while the cheap model returned the **gross
  total** — dropping strong's `total` accuracy to 58% and its overall to −13.3. Bigger model, different
  (and here worse) disambiguation of an ambiguous field name.

## Rules as a detector (invoices, n=45)

Business rules are not an accuracy lifter — they are an error *detector*. We extracted net/vat/gross and
ran `sum_equals(total_net + total_vat = total_gross, tol=0.05)`:

| Model | gross wrong vs truth | rule caught | false alarms |
|---|---|---|---|
| cheap | 12/45 | **0/12** | 0/33 |
| strong | 1/45 | 0/1 | 0/44 |

The rule is **precise** (zero false alarms — when the extracted gross is right, net+vat=gross always
holds) but caught **none** of the truth-errors. Why: when the model misreads the total it usually pulls a
**self-consistent triple from the wrong row** — e.g. net 8,400 + vat 840 = gross 9,240, arithmetically
valid but from a line item, not the invoice summary whose true total is 57,483. `sum_equals` verifies
*internal arithmetic consistency*, not agreement with truth, so a wrong-but-consistent extraction passes.
Honest takeaway: cross-field rules catch inconsistency (transcription/arithmetic slips), **not**
whole-record misreads — pair them with grounding/provenance, don't treat them as a correctness oracle.

## Bottom line

| Module | Verdict on this evidence |
|---|---|
| cascade | Keep — adapts cost to the doc, small accuracy lift, never worse |
| ensemble | Keep for high-headroom domains; skip near the ceiling (3× cost for ~0) |
| repair | Neutral here — value depends on errors being *flagged*, not confident-wrong |
| hybrid (generic matchers) | **Off by default** — only use with anchored, labelled regex |
| rules | A precise consistency check, not a truth oracle — recall depends on the error type |

## Honest caveats

- Two domains, modest n (60 / 45), CLI-backed models, estimated prices, relaxed/type-aware matching.
- The invoice `seller` field is excluded from scoring: its ground truth bundles company name + full
  address, so a correct name never matches. The other fields are clean.
- Results are for *these* datasets, prompts, and schema. The point is the **method** (turn each module on,
  measure the delta on real GT across domains) and the **direction** of each effect — not the exact points.
- This ablates modules mostly in isolation against a shared baseline; it does not measure every
  interaction (e.g. cascade + ensemble together).
