# Changelog

All notable changes to prismdoc. Format loosely follows [Keep a Changelog]; versions are semver-ish
while pre-1.0 (the public API may still change).

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
