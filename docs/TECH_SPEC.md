# Tech Spec — prismdoc

- **Role:** [TL] Technical Leader (Claude)
- **Date:** 2026-07-17
- **Status:** Draft for Phase 1
- **Related:** [PRD.md](PRD.md), `prism-architecture.drawio`

---

## 1. Architecture overview

prismdoc is an **orchestration layer** on top of existing extraction engines. A document flows
through a chain of pluggable **Stages**; each Stage can be swapped for a different engine:

```
Document IN  ->  Ingest  ->  Parse/OCR  ->  [Router*]  ->  Extract  ->  Validate  ->  Normalize  ->  JSON OUT
```

`Router` marked `*` = **Phase 2** (cost-aware cascade). Phase 1 runs a single straight path, but
the Stage interface must be designed so the Router can slot in later without breaking the core.

## 2. Core abstractions

This is the **most important** part — everything else plugs into it.

### 2.1 Document model (data carrier)
A single object flowing through the pipeline; each Stage enriches it:
- `source`: source metadata (path, mime, page count...).
- `pages`: list of pages; each has `text`, `blocks/layout` (bbox), `image_ref` (optional).
- `artifacts`: intermediate results (e.g. parsed markdown).
- `records`: structured extraction results (after Extract).
- `confidence`: confidence per field/record.
- `trace`: log of each Stage run + duration + cost (for Observability/Phase 2).

### 2.2 Stage interface (ABC)
```python
class Stage(ABC):
    name: str
    @abstractmethod
    def run(self, doc: Document, ctx: Context) -> Document: ...
```
- Logic is stateless; configuration via the constructor.
- Takes a `Document`, returns an enriched `Document`. No side effects beyond `doc`/`ctx`.
- Each Stage declares required inputs / produced outputs (so a pipeline can be validated).

### 2.3 Pipeline runner
- Takes a list of `Stage`, runs them sequentially, records `trace`.
- Catches per-Stage errors, attaching context (which Stage, which document).
- (Phase 2) supports branching (router) — Phase 1 is linear only.

### 2.4 Plugin registry
- Registers engines by key (e.g. `parser.docling`, `extractor.litellm`).
- Lets the YAML config select an engine without hardcoding.

### 2.5 Config-as-YAML
- One YAML file declares: the Stages + engines + parameters + target schema.
- A loader reads the YAML, uses the registry, and builds the pipeline.

## 3. Tech stack (locked)

- Python 3.11+
- **Pydantic v2** — Document model, target schema, validation.
- **FastAPI** — REST serving (US-2).
- **litellm** — one interface for many LLM/VLM providers (foundation for Phase 2 cost-routing).
- **Docling** — default parse/OCR engine for Phase 1 (pluggable).
- **PyMuPDF / Pillow / openpyxl** — loaders (PDF / image / xlsx).
- **PyYAML** — config.
- **pytest** — tests; **ruff** — lint; **mypy** — types (recommended).
- **Docker + docker-compose** — deployment.

## 4. Module layout (planned)

```
src/prismdoc/
  models.py            # Document, Page, Block, Record, Source, Context (Pydantic)
  stages/
    base.py            # Stage ABC
    ingest.py          # loaders: pdf / image / xlsx
    parse.py           # Docling adapter (+ Parser interface)
    extract.py         # schema-driven extractor via litellm
    validate.py        # Pydantic validation + business rules
    normalize.py       # units / dates / currency / dedup
  pipeline.py          # Pipeline runner + trace
  registry.py          # plugin registry
  config.py            # load pipeline from YAML
  schema.py            # define / load target schema (fields)
  api/
    app.py             # FastAPI: POST /extract, GET /health
  cli.py               # CLI entrypoint (run one file through the pipeline)
tests/                 # tests per module
examples/
  retail/              # retail sample files + demo pipeline.yaml
Dockerfile
docker-compose.yml
```

## 5. Phase 1 design (concrete)

- **Path:** `Ingest -> Parse(Docling) -> Extract(litellm, schema-driven) -> Validate -> Normalize -> Output`.
- **No Router yet** — but `Pipeline` and `Stage` are designed so the Router (Phase 2) can be inserted
  without changing the core.
- **Extract in Phase 1:** using text/layout from Parse + an LLM (schema-driven) is enough for the demo;
  VLM is optional.
- **Target schema:** e.g. retail product: `name, sku, price, currency, unit, brand, category, attributes`.
- **Output:** JSON + CSV.

## 6. Extensibility (Phase 2 slots reserved)

- The Router slots between Parse and Extract: reads Parse `confidence`, decides whether to escalate to a VLM.
- The eval harness reads `records` + ground-truth and reports per-field accuracy.
- Everything goes through the registry + YAML, so adding an engine = adding a plugin, not editing the core.

## 7. Definition of Done (standard for every ticket)

- Code has **type hints**; passes `ruff` (no errors) and ideally `mypy`.
- Has **tests** for the main units; `pytest` PASSES.
- No scope creep beyond the ticket.
- Public API has short docstrings.
- New dependencies: declared in `pyproject.toml`, justified in the report.
- Ticket report: files changed, test commands + results, open doubts.

## 8. Build order

Implementation proceeds from the **core contracts** (Document model, Stage, Pipeline, Registry)
outward, since every Stage depends on that contract: core → ingest → parse → extract → validate →
normalize → config → serving → demo, then the cost-aware cascade and figure sub-pipeline. See the
roadmap in the [README](../README.md).
