# Changelog

All notable changes to prismdoc. Format loosely follows [Keep a Changelog]; versions are semver-ish
while pre-1.0 (the public API may still change).

## Unreleased

### Added
- **Verification-driven repair (closes the confident-but-wrong loop).** `RepairStage` now triggers on a
  verification mismatch (`field_verification == "label_mismatch"` / `field_column_verification ==
  "column_mismatch"`), not only on missing/low-confidence fields, and passes the model a corrective hint
  ("re-read this from the correct column, not a net/subtotal column"). End-to-end on real Docling invoices
  (n=12): of the net-as-total errors the verifier caught, repair fixed **10/10** back to the true gross.
  Stale-snapshot safe (a field repaired via a mismatch is not re-triggered on later rounds).
- **Semantic verification, slice 3 — column verification** (`TableColumnVerifyStage`) — parses the
  markdown table around a value, finds which **column** the value is in, and checks that column's header
  (`Record.field_column_verification`). Measured on real Docling invoice tables (n=12): a `total` fed the
  true **gross** verifies **12/12**; fed the **net** (wrong column) it flags `column_mismatch` **10/12** —
  catching the net-as-total confusion with **0 false alarms** on the true total. This is the columnar catch
  slice 1 structurally could not do. See [docs/VERIFICATION.md](docs/VERIFICATION.md).
- **Semantic verification, slice 1** (`LabelVerifyStage`, `prismdoc.stages.verify`) — using the source
  span the extractor cites per field (`Record.field_evidence`), verify it sits next to an expected label
  and not an anti-label (a `total` value on a `net` line), emitting `Record.field_verification`. Shipped as
  **infrastructure**, with an honest measured limitation: on real, flattened, *columnar* invoice summaries
  it 100% false-alarms on flattened OCR. Re-measuring with a **layout-preserving parse** (Docling) drops
  the false alarm from **43/43 → 0/12**: the flattened input was the cause, not the logic — so run the
  verifier on layout parse output. Resolving *which column* a number is in (net vs. gross in one summary
  row) still needs cell-level parsing (a later slice). See [docs/VERIFICATION.md](docs/VERIFICATION.md).

### Docs
- **Repositioned around document archetypes** (flat / visual / mixed / tabular / hierarchical) with honest
  per-archetype status; invoices/receipts are the proven anchor, not the whole scope.

## v0.5.1

### Fixed
- **Chunked and ensemble extraction now roll their sub-call cost into the parent ledger.** Each per-chunk
  / per-model extraction ran on a temporary document whose `CostLedger` was discarded, so the parent
  `doc.artifacts["cost"]` under-reported (or missed) the real spend. `CostLedger.merge()` +
  `merge_cost(parent, child)` fold every sub-call's tokens/USD/stage costs into the parent, and both
  stages now enforce `budget_usd` **across** their sub-calls (raising `BudgetExceededError` mid-loop).

## v0.5.0 — evidence, benchmarks & honest ablation

### Added
- **Evidence-first provenance (field lineage).** `ExtractStage(evidence=True)` has the model cite the
  exact source span it took each value from (`Record.field_evidence`); `ProvenanceStage` locates *that*
  span (word-boundary aware) instead of reverse-searching the bare value. This resolves the ambiguity
  when a value like `10.00` appears as subtotal / tax / total, and rejects hallucinated spans (falls back
  to value-search, never fabricates). `FieldProvenance` gains `evidence` and `method`
  (`evidence` | `value_search`). Fully backward compatible: no cited evidence → the old value-search path.
- **Per-module ablation across two domains** (`docs/ABLATION.md`) — measures the accuracy delta of each
  module (hybrid, repair, ensemble, cascade, strong) on SROIE receipts (n=60) and invoices (n=45) vs a
  cheap-model baseline, plus a rules-as-detector study. Deliberately honest findings: cascade/ensemble
  help where there is headroom, repair is ~neutral, the naïve deterministic **hybrid tier hurts** (−27 to
  −33 pts), a bigger model is not always better, and `sum_equals` is precise but only catches internal
  inconsistency (0 recall on self-consistent misreads).
- **Mixed-modality benchmark** (`prismdoc.bench.infovqa`) — quantifies the figure→VLM path on
  InfographicVQA (validation, 200 distinct infographics, real ground truth from Hugging Face). Answering
  from OCR text alone scores **35.5%**; the figure→VLM path scores **84.5%** — a **+49.0-point** gap,
  stable at +47.5 to +49.0 across n=40, 80, and 200. Chart + methodology in `docs/BENCHMARK.md` and
  `docs/mixed-modality.md`; reproduce with `python -m prismdoc.bench.infovqa`.
- **Hybrid deterministic + LLM extraction** (`HybridExtractStage`) — extract simple fields (regex /
  number / currency / date / email) deterministically for free — deterministic and cheap, though NOT a
  correctness guarantee (a regex can be consistently wrong); the LLM handles only the remaining fields.
  Adds a `$0` deterministic tier below the LLM cascade.

### Fixed
- **Repair no longer re-repairs an already-fixed low-confidence field.** The `low_confidence` artifact is
  a pre-repair snapshot that RepairStage never recomputed, so with `max_rounds > 1` a field corrected in
  round 1 was re-selected (and re-prompted) every later round. RepairStage now tracks fields already
  repaired via that stale signal and excludes them; a genuinely-missing field is still retried.
- **Rule engine distinguishes "cannot evaluate" from "violation".** A rule that can't run (a field is
  missing or non-numeric) was counted as a violation, inflating the violation rate. Those now go to a
  separate `rule_uneval` bucket; `artifacts["rules"]` reports `violations` and `cannot_evaluate`
  separately. `rule_violations` now contains only rules that actually ran and failed.

## v0.4.0 — reliability & auditability

Addresses a full external code review. Every item was built as a ticket, reviewed, and verified.

### Added
- **Confidence calibration** — `ConfidenceStage(calibration=...)` maps the raw grounding heuristic to
  measured accuracy. Measured on 200 SROIE receipts: grounded `0.9`→`0.83`, ungrounded `0.4`→`0.66`
  ("ungrounded" ≠ "wrong"), ECE ≈ 0.10. Reliability diagram in `docs/BENCHMARK.md`.
- **Business-rule / cross-field validation** (`RuleValidateStage`) — `sum_equals`, `in_set`, `range`,
  `non_negative`. Catches semantic/mapping errors (e.g. `subtotal + tax ≠ total`) that grounding misses.
- **Field provenance** (`ProvenanceStage`) — per-field page index, bounding box, and source text.
- **Adaptive field retry** (`RepairStage`) — re-prompt only the failed fields, merge back; bounded rounds.
- **Composite cascade scorer** — `char_validity` (alphanumeric ratio, catches long OCR garbage),
  `text_sufficiency`, `field_coverage`, `grounding_ratio`, combined via `make_composite`.
- **Observability signals** — `document_metrics` / `aggregate_metrics` (per-stage latency, escalation /
  violation rates, low-confidence, tokens, cost); returned by `POST /extract`.
- **Long-document chunking** (`ChunkedExtractStage`) — chunk → extract → merge/dedup.
- **Model ensemble** (`EnsembleExtractStage`) — per-field majority vote + disagreement flags.

### Benchmark
- **Cost-aware cascade frontier on 200 real SROIE receipts** (`docs/BENCHMARK.md`, `docs/img/frontier.png`):
  cheap model 80.1% at ~free vs strong model 87.2% at ~154× cost (n=158, measured via the real
  CascadeStage); grounding-based escalation is a
  monotone Pareto curve. Backends run free via CLI subscriptions; USD estimated.

## v0.3.0

### Added
- Stateless LLM client (fixed a cross-request `last_usage` race); cached, strict API runtime.
- Bounded input + **pre-flight** budget (refuses before spending); API 413 on oversize/too-many-pages.
- Honest **cost ledger** (Pydantic, litellm pricing, unknown model = unpriced, unmetered marked; in API).
- **Grounding-based confidence** (catches hallucinated values); removed the backwards fallback scale.
- Type-aware eval comparison + **threshold-sweep accuracy/USD frontier** tool (`prismdoc-sweep`).
- Structured output via `response_format` (regex scrape as fallback).
- Retry hardening (Exception not BaseException, transient whitelist, jitter, attempts surfaced).
- Public **SROIE benchmark** harness: OCR-recall + multi-model extraction accuracy.

## v0.2.0
- Cost-aware cascade, figure sub-pipeline, eval harness; "focused microservice" scope doc.

## v0.1.0
- Core pipeline (ingest → parse → extract → validate → normalize), YAML config, CLI, FastAPI + Docker.
