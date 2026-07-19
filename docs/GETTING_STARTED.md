# Getting started with prismdoc

Hands-on setup, configuration, and running prismdoc three ways (library, CLI, service). For what
prismdoc is and why it exists, see the [README](../README.md); for measured results, see
[BENCHMARK.md](BENCHMARK.md).

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                 # core
pip install -e ".[dev]"          # + tests/lint
pip install -e ".[docling]"      # + OCR fallback (Docling/RapidOCR)
pip install -e ".[llm]"          # + LLM extraction (litellm; Bedrock/OpenAI/local)
pip install -e ".[api]"          # + FastAPI serving
```

The core path is fully offline and needs no API key. LLM extraction and OCR fallback are opt-in extras.

## Quickstart (fully offline, no API key)

Structured extraction on a retail spreadsheet, end to end:

```bash
python examples/retail/make_sample.py
python -m prismdoc.cli \
  --config examples/retail/demo.yaml \
  --input examples/retail/sample_catalog.xlsx \
  --csv out.csv
```

```
records: 5   |   validation: valid=5 invalid=0 errors=0
name                   | sku      | price | currency | unit   | brand       | category
-----------------------+----------+-------+----------+--------+-------------+----------
Arabica Coffee Beans   | SKU-1001 | 12.5  | USD      | kg     | Acme        | Beverages
...
```

## Declaring a cost-aware cascade

Run the cheap tier, score the result, and escalate **only** if it's below your threshold. The decision
and score are recorded on the document (`artifacts["router"]`) so you can see where money was spent.

```yaml
pipeline:
  - ingest.default
  - cascade:
      primary:  parse.passthrough   # cheap, free
      fallback: parse.docling       # stronger, costs compute
      scorer:   text_length
      threshold: 20
  - extract.table
  - validate.default
  - normalize.default
```

Real behaviour (offline, from `examples/retail/pipeline_cascade.yaml`):

| Input | Cheap tier score | Decision |
|---|---|---|
| Text invoice (PDF) | 2074 | `tier=primary` — passthrough is enough, **no OCR spent** |
| Scanned receipt (JPG) | 9 | `tier=fallback` — escalates to **Docling OCR** |

The same pattern applies to extraction (cheap model → strong model) via injectable LLM clients.

## Structured extraction with an LLM

The `extract.default` stage is schema-driven and provider-agnostic via
[litellm](https://github.com/BerriAI/litellm). The LLM client is injectable, so the pipeline is fully
testable offline with a mock; a live run needs `pip install prismdoc[llm]` and provider credentials.

```yaml
schema:
  fields:
    - {name: name, type: string, required: true}
    - {name: price, type: number}
pipeline:
  - ingest.default
  - parse.default
  - extract.default: {model: "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"}
  - validate.default
  - normalize.default
```

Set credentials via your provider's usual env vars (e.g. AWS creds/region for Bedrock, `OPENAI_API_KEY`
for OpenAI).

## The figure / diagram sub-pipeline

Documents with embedded images/diagrams are handled on a side path — extracted, replaced with a
`[[FIGURE:id]]` placeholder, processed by a different method (OCR/VLM), then merged back:

```
parse markdown:  "...text... [[FIGURE:fig_0_0]] ...text..."
                              │
   figures extracted ─────────┘   process (OCR / VLM / stub)   ──► merge result back
                                                                    into the placeholder
```

Declared via `figures.extract → figures.process → figures.merge` (see
`examples/retail/pipeline_figures.yaml`). The processor is pluggable — the default is an offline stub;
an OCR/VLM processor slots in without changing the round-trip. Why this matters (measured): see
[mixed-modality.md](mixed-modality.md).

## Run as a service

```bash
pip install -e ".[api,llm]"
uvicorn prismdoc.api.app:app --port 8000
# or:
docker compose up --build
```

```bash
curl -F "file=@invoice.pdf" http://localhost:8000/extract
curl http://localhost:8000/health
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

### Eval harness (offline)

After generating the retail sample, the harness runs end-to-end against ground truth:

```bash
python examples/retail/make_sample.py
python -m prismdoc.eval --dataset examples/eval/retail_dataset.json
# or: prismdoc-eval --dataset examples/eval/retail_dataset.json
```

The retail dataset is a **smoke case**, not a quality claim: the table extractor reads a file produced by
the same script that wrote the ground-truth rows — a tautology that proves the harness wiring works.
Real-world accuracy (and the cost trade-off) is measured by the threshold-sweep frontier below, not by
this smoke run.

### Threshold sweep (accuracy vs USD frontier)

Sweep cascade thresholds to emit the accuracy-vs-USD frontier. Requires a dataset whose `config_path` is
a cascade pipeline (e.g. retarget `examples/eval/retail_dataset.json` at
`examples/retail/pipeline_cascade.yaml`):

```bash
python -m prismdoc.eval.sweep \
  --dataset path/to/cascade_dataset.json \
  --thresholds 0,10,20,50,100 \
  --out frontier.csv \
  --plot frontier.png   # optional; needs pip install 'prismdoc[viz]'
# or: prismdoc-sweep --dataset ... --thresholds ... --out frontier.csv
```

Writes `threshold,accuracy,total_usd,escalations` and prints a table. `--plot` is skipped cleanly when
matplotlib is not installed.

### Mixed-modality benchmark (figure→VLM)

Reproduce the InfographicVQA number from [BENCHMARK.md](BENCHMARK.md):

```bash
python -m prismdoc.bench.infovqa --n 200 --out /tmp/infovqa
```

Streams InfographicVQA (validation, real ground truth) from Hugging Face and compares text-only (OCR →
LLM) against the figure→VLM path. Point `--model-cmd` at any multimodal CLI, or import `run(...)` with a
custom `answer_fn` to wire `litellm`.

## Project layout

```
src/prismdoc/
  models.py        # Document, Page, Block, Record, TraceEntry (Pydantic)
  schema.py        # FieldSpec, TargetSchema
  pipeline.py      # sequential runner + trace
  registry.py      # plugin registry
  config.py        # load_pipeline / build_pipeline (YAML)
  cli.py           # `prismdoc` CLI
  eval/            # offline per-field accuracy harness
  bench/           # public benchmarks (SROIE OCR-recall, InfographicVQA)
  stages/
    ingest.py      # PDF / image / xlsx loaders
    parse.py       # passthrough + Docling OCR
    cascade.py     # cost-aware cascade + scorers
    figures.py     # figure extract / process / merge
    extract.py     # schema-driven LLM extraction (litellm)
    validate.py    # schema validation + coercion
    normalize.py   # cleanup + dedup
    table_extract.py  # offline spreadsheet extractor
  api/app.py       # FastAPI service
examples/retail/   # sample generator + demo pipelines
docs/              # PRD, tech spec, benchmarks, diagrams
```
