# prismdoc — launch post (draft)

Marketing/launch copy for Show HN + a blog. Not product docs; adapt freely before posting.

## Show HN — title options

1. Show HN: Prismdoc – cost-aware document extraction, benchmarked honestly on real receipts
2. Show HN: A document-extraction pipeline that only pays for a big model when it has to
3. Show HN: Prismdoc – I measured when a cheap LLM is "good enough" for receipts (frontier chart)

## Show HN — body

I kept seeing document-extraction demos that are "PDF → GPT → JSON" and call it done. That works in a
notebook and falls over in production. So I built prismdoc: an OSS orchestration layer that treats
extraction as a real pipeline — ingest → parse/OCR → extract → validate → normalize — and, crucially,
is **cost-aware**: it runs a cheap tier first and only escalates the hard cases to an expensive model.

The part I care about most is that I benchmarked it honestly on 200 real SROIE receipts instead of a
self-made demo. The headline chart (accuracy vs. estimated $): a cheap model (gemini-flash) already
gets **79.5%** field accuracy at ~free; sending everything to Opus gets **86.5%** — +7 points, but
**~154× the cost**. The cascade lets you buy the points in between by escalating only the shakiest
docs (grounding-based).

Benchmarking also embarrassed me productively: my per-field confidence heuristic was miscalibrated —
"ungrounded" values (not found verbatim in the OCR) were actually correct 66% of the time, not the
40% my heuristic implied. That's now measured and calibratable.

It also does the boring-but-necessary things: field provenance (page/bbox/source), cross-field
business rules (catches `subtotal + tax ≠ total`), a $0 deterministic tier (regex for simple fields),
model ensemble/disagreement, and a per-request cost ledger.

Honest caveats: n=200 is preliminary, costs are estimated at API prices (I ran it via CLI
subscriptions), single dataset. Feedback very welcome — especially on the scorer and calibration.

Repo (MIT): https://github.com/gnutvz/prismdoc

---

## Blog version

### When is a cheap LLM good enough? Benchmarking cost-aware document extraction on real receipts

#### 1. The problem with "PDF → GPT → JSON"

Most extraction demos send the whole document to one big model and call it done. In production you
care about three things that demo ignores: cost, reliability, and auditability. prismdoc is an
attempt to build the pipeline *around* the model, not just call the model.

#### 2. The idea: a cost-aware cascade

Run the cheap tier first (deterministic regex → cheap model), score the result, and escalate to a
stronger model *only* when the cheap answer looks shaky. The escalation signal here is **grounding**:
does the extracted value actually appear in the OCR text?

#### 3. The chart that matters

![Cost-aware cascade frontier](img/frontier.png)

On 200 real SROIE receipts:

- cheap-only: **79.5%** at ~$0.02 / batch
- opus-only: **86.5%** at ~$2.54 / batch (**~154×**)
- escalating the 14% lowest-grounding docs recovers the first easy gains; the last points cost the most.

The gap between those two endpoints is the money a cost-aware router lets you *not* spend.

#### 4. Benchmarking made me fix my own confidence

![Confidence calibration](img/calibration.png)

My confidence heuristic said "grounded = 0.9, ungrounded = 0.4". Measured against ground truth:
grounded is right **83%** (not 90%), and ungrounded is right **66%** (not 40%) — a value not found
verbatim in the OCR is often just reformatted, not wrong. Confidence is now a heuristic you can
*calibrate* against measured accuracy, not a number I made up.

#### 5. The boring, necessary parts

Provenance (page/bbox/source text), cross-field business rules (`subtotal+tax=total`), a $0
deterministic tier (regex/date/currency), adaptive field-retry (re-prompt only the failed fields),
model ensemble with disagreement flags, a real per-request token/USD cost ledger, and metrics.

#### 6. What it deliberately is NOT

A focused, stateless microservice — not a platform. Queues, storage, multi-tenancy, dashboards are
your infrastructure to wire around it. No lock-in.

#### 7. Honest caveats

n=200 preliminary; costs estimated at reference API prices; single dataset (SROIE receipts). Next:
CORD/invoices, larger n, held-out calibration.

Repo (MIT): https://github.com/gnutvz/prismdoc
