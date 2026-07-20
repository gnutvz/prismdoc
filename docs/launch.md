# prismdoc — launch post (draft)

Marketing/launch copy for Show HN + a blog. Not product docs; adapt freely before posting.

## Show HN — title options

1. Show HN: prismdoc – cost-aware document extraction, benchmarked honestly on real datasets
2. Show HN: A document pipeline where the figure→VLM path beats text-only by 49 points (measured)
3. Show HN: prismdoc – only pay for a big model when the cheap one isn't good enough
4. Show HN: I measured when text-only extraction fails on infographics (it's most of the time)
5. Show HN: I ablated my own document pipeline and reported the module that makes it worse

## Show HN — body

I kept seeing document-extraction demos that are "PDF → GPT → JSON" and call it done. That works in a
notebook and falls over in production. So I built **prismdoc**: an OSS orchestration layer that treats
extraction as a real pipeline — ingest → parse/OCR → extract → validate → normalize — with two ideas I
could actually **measure on public datasets with ground truth**, not a self-made demo.

**1. Cost-aware cascade.** Run a cheap tier first, escalate the hard cases to an expensive model only
when the cheap answer looks shaky (grounding-based). On 200 real SROIE receipts: a cheap model
(gemini-flash) already gets **80.1%** field accuracy at ~free; sending everything to Opus gets
**87.2%** — +7 points, but **~154× the cost**. The cascade buys the points in between by escalating only
the shakiest docs.

**2. Route figures to a VLM, merge back.** On documents that mix text with charts and diagrams,
text-only extraction is blind to what's inside a figure. prismdoc pulls each figure out, replaces it
with a placeholder, sends it to a VLM, and merges the result back. Measured on **InfographicVQA** (200
distinct infographics, real ground truth): answering from **OCR text alone = 35.5%**, but the
**figure→VLM path = 84.5%** — a **+49-point** gap, stable at +47.5 to +49.0 across n=40, 80, and 200.
Even with the *full* OCR text in hand, text alone answers barely a third — because the answers live in
layout and chart values that raw text drops.

**3. I ablated my own modules — and reported the one that hurts.** Across two domains (receipts +
invoices) I measured each module against a cheap-model baseline: cascade and ensemble help where there's
headroom, repair is ~neutral, and the naïve deterministic tier *hurts* by 27–33 points (its generic
matcher grabs the wrong number). A bigger model isn't always better either — on invoices Opus read the
pre-tax subtotal as the total. I'd rather ship that table than a rosy one.

It also does the boring-but-necessary things: evidence-first provenance (the model cites the exact source
span, so a value like `10.00` resolves to the right line instead of the first match), cross-field
business rules (catches `subtotal + tax ≠ total`), a $0 deterministic tier (regex for simple fields),
model ensemble/disagreement, per-request cost ledger. And benchmarking embarrassed me productively — my
confidence heuristic was miscalibrated ("ungrounded" values were right 66% of the time, not the 40% my
heuristic implied); it's now measured and calibratable.

It's a **focused, stateless microservice**, not a platform: queues, storage, multi-tenancy, dashboards
are yours to wire around it. No lock-in; the offline path needs no API key.

Honest caveats: SROIE is n≈158 with estimated API prices; the InfographicVQA gap uses a relaxed match
(not official ANLS) and isolates the figure→VLM contribution. Feedback very welcome — especially on the
scorer, the calibration, and the mixed-modality metric.

Repo (MIT): https://github.com/gnutvz/prismdoc

## Show HN — first comment (post immediately after submitting)

Author here — a few things I'd flag before you ask:

- The models ran via CLI subscriptions (Claude / Cursor), so the **USD figures are estimated** at
  reference API prices, not billed. Treat them as relative, not a quote.
- The mixed-modality number uses a **relaxed normalized match, not official ANLS**, and isolates the
  figure→VLM gain — it's not the full route-and-merge on multi-region docs (that part is a qualitative
  case study in the repo).
- It's deliberately a **stateless microservice, not a platform** — no queues / storage / multi-tenancy
  baked in. That's a scope decision, not a missing TODO; you wire it into your own infra.
- The **ablation** is the part I'm most proud of and most nervous about: it shows one of my own modules
  (the deterministic tier) actively *hurting*. If the methodology is wrong, tell me — I'd rather fix it
  than hide it.

Benchmarks, methodology, and honest caveats are all in the repo (`docs/BENCHMARK.md`, `docs/ABLATION.md`).
Feedback very welcome — especially on the scorer, the calibration, and the mixed-modality metric.

---

## Blog version

### Two things I could measure about document extraction (and one that embarrassed me)

#### 1. The problem with "PDF → GPT → JSON"

Most extraction demos send the whole document to one big model and call it done. In production you care
about three things that demo ignores: **cost, reliability, and auditability**. prismdoc is an attempt to
build the pipeline *around* the model, not just call the model — and to back the design choices with
numbers on public datasets, not vibes.

#### 2. Claim one: a cost-aware cascade captures most of the accuracy for a fraction of the cost

Run the cheap tier first (deterministic regex → cheap model), score the result, and escalate to a
stronger model *only* when the cheap answer looks shaky. The escalation signal is **grounding**: does
the extracted value actually appear in the OCR text?

![Cost-aware cascade frontier](img/frontier.png)

On 200 real SROIE receipts:

- cheap-only: **80.1%** at ~free
- opus-only: **87.2%** at **~154×** the cost
- escalating only the lowest-grounding docs recovers the first easy gains; the last points cost the most.

The gap between those two endpoints is the money a cost-aware router lets you *not* spend.

#### 3. Claim two: on mixed-modality docs, the figure→VLM path recovers what text can't

A text-only extractor never sees what's inside a chart or diagram. Running a VLM over every page is
expensive and shaky on dense text. So prismdoc **routes**: text→text, each figure→VLM, then merges the
figure result back into the structure at the placeholder.

How much does that routing actually buy you? I measured it on **InfographicVQA** (validation split, real
ground-truth answers, 200 distinct infographics streamed from Hugging Face). Same questions, two ways to
answer:

- **text-only**: the infographic's OCR text → LLM
- **visual**: the infographic image → VLM (prismdoc's figure path)

![Text-only vs figure→VLM path](img/mixed_modality.png)

| Path | Accuracy (n=200) |
|------|------------------|
| Text-only (OCR → LLM) | **35.5%** |
| Visual (figure → VLM) | **84.5%** |
| **Gap** | **+49.0 points** |

The gap stayed within **+47.5 to +49.0 points across n=40, 80, and 200** — stable, not a small-sample
fluke. The lesson is blunt: even when text-only sees the *complete* OCR text, it answers barely a third
of infographic questions, because the answers live in layout, chart values, and spatial relationships
that raw text loses. Routing the figure to a VLM is what recovers them. Reproduce it yourself with
`python -m prismdoc.bench.infovqa`.

#### 4. The one that embarrassed me: my confidence was miscalibrated

![Confidence calibration](img/calibration.png)

My confidence heuristic said "grounded = 0.9, ungrounded = 0.4". Measured against ground truth: grounded
is right **83%** (not 90%), and ungrounded is right **66%** (not 40%) — a value not found verbatim in the
OCR is often just reformatted, not wrong. Confidence is now a heuristic you can *calibrate* against
measured accuracy, not a number I made up.

#### 5. The boring, necessary parts

Provenance (page/bbox/source text), cross-field business rules (`subtotal+tax=total`), a $0 deterministic
tier (regex/date/currency), adaptive field-retry (re-prompt only the failed fields), model ensemble with
disagreement flags, a real per-request token/USD cost ledger, and metrics.

#### 6. What it deliberately is NOT

A focused, stateless microservice — not a platform. Queues, storage, multi-tenancy, dashboards are your
infrastructure to wire around it. No lock-in.

#### 7. Honest caveats

SROIE cascade numbers are n≈158 with costs estimated at reference API prices. The InfographicVQA gap
uses a relaxed normalized match (not official ANLS) and — since an infographic is a single image —
isolates the figure→VLM contribution rather than the full route-and-merge on multi-region documents
(shown qualitatively in the repo). Single dataset per claim. Next: CORD/invoices, larger n, held-out
calibration, a scored multi-region composed benchmark.

Repo (MIT): https://github.com/gnutvz/prismdoc
