# Changelog

All notable changes to prismdoc. Format loosely follows [Keep a Changelog]; versions are semver-ish
while pre-1.0 (the public API may still change).

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
  cheap model 79.5% at ~free vs strong model 86.5% at ~154× cost; grounding-based escalation is a
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
