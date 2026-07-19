# Case study: mixed-modality documents (text + image + diagram)

The clearest place a **composed** pipeline beats any single tool is a document that mixes modalities —
running text next to embedded **images, charts, and diagrams**. A text-only extractor never sees what's
inside a figure; running a VLM over every page is expensive and inconsistent on dense text. prismdoc's
answer is to **route**: text goes to the text path, each figure is pulled out and sent to a VLM, and the
results are **merged back** into the structure at the figure's location.

Two pieces of evidence follow: a **quantitative benchmark** measuring how much the figure→VLM path
recovers over text alone (on labeled InfographicVQA), and a **qualitative case study** showing the
round-trip on a real multi-figure paper.

## Quantitative result: text-only vs the figure→VLM path

On **InfographicVQA** (validation split, real ground-truth answers, 200 distinct infographics streamed
from Hugging Face), we compare two ways of answering the same questions:

- **Text-only** — the infographic's OCR text (Amazon Textract, shipped with the dataset) → LLM.
- **Visual** — the infographic image → VLM. This is prismdoc's `figures.process` path.

![Text-only vs figure→VLM path](img/mixed_modality.png)

| Path | Accuracy (n=200) |
|------|------------------|
| Text-only (OCR → LLM) | **35.5%** (71/200) |
| Visual (figure → VLM) | **84.5%** (169/200) |
| **Gap recovered by the figure→VLM path** | **+49.0 points** |

The gap stayed within **+47.5 to +49.0 points across n=40, 80, and 200** — stable, not a small-sample
artifact. The lesson: even when text-only sees the *complete* OCR text, it answers barely a third of
infographic questions, because the answers live in **layout, chart values, and spatial relationships**
that raw text loses. Routing the figure region to a VLM is what recovers them — the reason prismdoc's
`figures.*` sub-pipeline exists.

Reproduce: `python -m prismdoc.bench.infovqa --n 200 --out /tmp/infovqa` (see
`src/prismdoc/bench/infovqa.py`). Scoring is a relaxed normalized match, not official ANLS — a coarse
readout of the gap, not a leaderboard number. A single infographic is one image, so this isolates the
**figure→VLM contribution**; the case study below shows the full route-and-merge on a multi-region
document.

## The document

A real 27-page arXiv paper ([InfographicVQA, 2104.12756](https://arxiv.org/abs/2104.12756)). Page 1
embeds an example infographic ("Who Employs the Most Delivery Workers?") whose data appears **only in
the image**, not in the surrounding prose. prismdoc's `FigureExtractStage` pulled **26 figures** from
the PDF; the parsed text is ~140k characters.

## Text-only vs composed (the actual output)

Around the figure on page 1:

**Text-only** — the figure is an opaque placeholder; its data is absent:

```
...most infographics contain numerical data, we collect questions ... [[FIGURE:fig_0_0]] ## Page 1
Dataset Images ... # Images # Questions Answer type ...
```

**Composed** — `figures.extract → figures.process (VLM) → figures.merge` reads the figure and merges
its content back in place:

```
...most infographics contain numerical data, we collect questions ...
This infographic titled "Who Employs the Most Delivery Workers?" shows Canadian delivery-worker
headcounts (Canada Post 53,000, UPS 12,000, Amazon 10,000, FedEx 7,500, DHL 3,500) alongside a map...
## Page 1 Dataset Images ...
```

The composed structure now contains **`Canada Post 53,000, UPS 12,000, Amazon 10,000, FedEx 7,500,
DHL 3,500`** — data that lived only in the embedded image and that a text-only extractor drops entirely.

Three figures the VLM resolved in this run:

| figure | size | VLM-extracted content (abridged) |
|--------|------|----------------------------------|
| `fig_0_0` | 955×799 | Infographic "Who Employs the Most Delivery Workers?" — Canada Post 53,000, UPS 12,000, Amazon 10,000, FedEx 7,500, DHL 3,500 |
| `fig_3_0` | 3608×3632 | Sunburst chart of question-starter n-grams ("what is/percentage of", "how many/much", "which country/is") |
| `fig_4_0` | 6968×5226 | Word cloud dominated by numerals + country names (Germany, India, China…) |

## Why no single tool wins

- **Text-only** (PyMuPDF/OCR): fast and cheap, but blind to everything inside a figure — misses the
  delivery-worker numbers completely.
- **VLM over the whole page/PDF**: can see figures, but is costly on a 27-page document and less reliable
  on dense body text and tables than a dedicated text path.
- **Composed (prismdoc)**: text path for text, VLM only for the figure regions, merged back at the
  placeholder — the complete result, and you pay the VLM only for the figures.

## Reproduce

The pipeline is `ingest → parse → figures.extract → figures.process(<vlm>) → figures.merge`. The default
`FigureProcessor` is an offline stub; plug a VLM processor (any multimodal model — here Claude via the
CLI). The figure→placeholder→process→merge round-trip is the built-in `figures.*` stages; only the
processor is swapped.

## Honest caveats

- The **+49.0-point** number (n=200) is on InfographicVQA with a **relaxed** match (normalized
  gold-in-prediction), not official ANLS — read it as a coarse measure of the gap, not a leaderboard score.
- A single infographic is one image, so the benchmark isolates the **figure→VLM path's** contribution.
  It does not, by itself, measure the full route-text-and-figures-separately-then-merge behavior — the
  case study above shows that on a multi-region document, but qualitatively.
- The VLM was run via a CLI subscription (free) — for a service, wire a multimodal API into the figure
  processor (the `answer_fn` / `FigureProcessor` interface is the seam).
