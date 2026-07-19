# Case study: mixed-modality documents (text + image + diagram)

The clearest place a **composed** pipeline beats any single tool is a document that mixes modalities —
running text next to embedded **images, charts, and diagrams**. A text-only extractor never sees what's
inside a figure; running a VLM over every page is expensive and inconsistent on dense text. prismdoc's
answer is to **route**: text goes to the text path, each figure is pulled out and sent to a VLM, and the
results are **merged back** into the structure at the figure's location.

This is a **qualitative** case study on real data — not a quantitative benchmark (a scored
`composed vs single` comparison needs a labeled mixed-modality dataset like InfographicVQA/DocVQA; the
pipeline is ready for it, the bottleneck is ground truth).

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

- Qualitative case study (a handful of figures), not a scored benchmark.
- VLM was run via a CLI subscription (free) — for a service, wire a multimodal API in the figure processor.
- A quantitative "composed beats single by X%" number needs a labeled mixed-modality dataset
  (InfographicVQA / DocVQA); that's the next step, and the pipeline is ready for it.
