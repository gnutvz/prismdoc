# PRD — prismdoc

- **Role:** [PO] Product Owner (Claude)
- **Date:** 2026-07-17
- **Status:** Draft for Phase 1 (MVP)
- **Related:** [TECH_SPEC.md](TECH_SPEC.md), architecture diagram `prism-architecture.drawio`

---

## 1. Problem

When retailers / distributors onboard new products, they must process supplier data that arrives
in **messy, inconsistent formats**: PDF catalogs, Excel price lists, spec sheets, photos. Staff
**manually copy** this into their systems (PIM/ERP), normalizing names, attributes, prices, units.
This is:

- Labor-intensive and error-prone (many hours/month).
- Slow to get products onto shelves / marketplaces.
- Impossible to scale as the number of suppliers and SKUs grows.

## 2. Target users

- **Primary persona (Phase 1):** operations / purchasing / master-data team at a retailer or
  distributor with many suppliers and SKUs. They feel the pain directly.
- **Buyer:** head of operations / business owner (approves spend).
- **Technical user:** a developer who integrates prismdoc into internal systems (self-hosted microservice).

## 3. Value proposition

> "Drop in a supplier's messy catalog / spec sheet, get back a clean, structured, validated
> product table — ready to push straight into PIM/ERP."

Long-term differentiation (Phase 2+): **cost-aware** (much cheaper than calling a VLM on everything)
and **schema-driven** (returns validated JSON, not raw markdown), **self-hostable microservice**.

## 4. Goals / Non-goals (Phase 1)

**Goals**
- End-to-end pipeline: document in → structured product data out.
- Usable both as a **library** (`pip`) and a **microservice** (REST API + Docker).
- A convincing **retail demo**: one sample catalog/spec file → clean product table.
- **Pluggable** architecture (swap OCR/LLM engines) and **schema-driven** output.

**Non-goals (deferred to Phase 2+)**
- Cost-aware cascade router (Phase 1 runs a single straight path, no cost optimization yet).
- Full eval harness, review dashboard UI, ready-made ERP/PIM connectors.
- Multi-industry: Phase 1 focuses only on the **retail product-catalog** use case.

## 5. User stories + Acceptance criteria

### US-1: Extract products from one supplier document
**As** a master-data staff member, **I want** to provide a catalog/spec file (PDF/Excel/image)
and a target schema, **so that** I get back a structured list of products.

*Acceptance:*
- Input: file path + target schema (fields to extract).
- Output: JSON list of product records matching the schema, with confidence.
- Supports at least: PDF, images (png/jpg), Excel (xlsx).

### US-2: Call via API (microservice)
**As** an integrating developer, **I want** to POST a file to a REST API, **so that** I get the
extraction result.

*Acceptance:*
- `POST /extract` accepts file + schema, returns JSON result.
- Runs via `docker run` with no complex setup.
- Provides `GET /health`.

### US-3: Declare the pipeline via config
**As** a technical user, **I want** to declare pipeline steps in a YAML file, **so that** I can
swap engines/parameters without changing code.

*Acceptance:*
- One YAML file describes the pipeline (loader, parser, extractor, schema).
- Changing the parse/extract engine requires only a YAML edit.

### US-4: Retail demo
**As** the PO, **I want** a runnable demo on sample retail data, **so that** we can show value to customers.

*Acceptance:*
- Sample file(s) (catalog/spec) live in `examples/`.
- One command produces a clean product table (printed + saved as JSON/CSV).

## 6. Success metrics (Phase 1)

- Demo runs end-to-end on >= 1 real retail file, correct on >= 90% of key fields
  (name, price, SKU) across the test sample.
- Time from `docker run` to first result < 15 minutes for a newcomer.
- A new loader/engine can be added as a plugin without touching the core.

## 7. Phase 1 scope (summary)

**In:** Ingest (PDF/image/xlsx) → Parse/OCR (Docling) → Extract (schema-driven via LLM/VLM) →
Validate (Pydantic) → Normalize → Output (JSON/CSV) → REST API + Docker → YAML config → retail demo.

**Out:** cost-aware router, full eval harness, dashboard, ERP connectors, multi-industry.

## 8. Open questions (to resolve incrementally)

- Real retail sample files: to come from your industry contacts (Phase 0 validation) — need 3-5 real samples.
- The canonical target schema for retail products: which fields? (name, SKU, barcode, price, unit,
  brand, category, attributes...) — finalize once we have real samples.
- LLM/VLM provider for the demo: which API (cost, ZDR)? — decided in the Tech Spec.
