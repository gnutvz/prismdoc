# Extending prismdoc (the plugin surface)

prismdoc is document **middleware**: the engines (OCR, parsing, table extraction, LLM/VLM) are
commodities you plug in, and the value is the pipeline around them. Everything is extensible through a few
small interfaces and one registry — nothing here is closed. This page is the map.

## The model

- A pipeline is an ordered list of **`Stage`s**, each `Stage.run(doc, ctx) -> Document`.
- Components are looked up from a **registry** by string key, and whole pipelines are declared in **YAML**
  (`config.build_pipeline`). Register a factory under a key and it is usable from YAML like any built-in.
- Register your factories in your app startup, or add them to `config._ensure_plugins()` if you fork.

```python
from prismdoc import registry
registry.register("parse.myengine", lambda: ParseStage(parser=MyParser()))
# then in YAML:  pipeline: [ingest.default, parse.myengine, extract.default, ...]
```

## Extension points at a glance

| You want to add… | Implement | Register as | Interface |
|---|---|---|---|
| A **parser engine** (Textract, Azure DI, Unstructured…) | `Parser.parse(doc) -> str` | `parser.x` / `parse.x` | `prismdoc.stages.parse.Parser` |
| A **model/LLM provider** | `LLMClient.complete(prompt, *, response_format) -> Completion` | injected into `ExtractStage` | `prismdoc.stages.extract.LLMClient` |
| A **figure/VLM processor** | `FigureProcessor.process(figure) -> str` | injected into `FigureProcessStage` | `prismdoc.stages.figures.FigureProcessor` |
| An **input loader** (new file type) | `Loader.load(source) -> list[Page]` | `loader.x` | `prismdoc.stages.ingest.Loader` |
| A **business rule** | a rule factory | `register_rule("name", fn)` | `prismdoc.stages.rules` |
| A **cascade scorer** | `Scorer = Callable[[Document], float]` | `register_scorer("name", fn)` | `prismdoc.stages.cascade` |
| A **verifier / labels** | config, or subclass `Stage` | `verify.x` | `prismdoc.stages.verify` |
| Anything else | `Stage.run(doc, ctx) -> Document` | `your.key` | `prismdoc.stages.base.Stage` |

## Add a parser provider (the middleware demo)

The parser is a swappable provider — `parse.docling`, `parse.pdfplumber`, `parse.passthrough` ship today.
A cloud provider is the same one method:

```python
from prismdoc.stages.parse import Parser, ParseStage
from prismdoc.models import Document
from prismdoc import registry

class TextractParser(Parser):
    """AWS Textract adapter — returns markdown (text + | tables |) the pipeline consumes."""
    def parse(self, doc: Document) -> str:
        import boto3  # your dependency, not prismdoc's
        client = boto3.client("textract")
        resp = client.analyze_document(...)   # your call
        return _blocks_to_markdown(resp)       # emit GFM tables so verify.column works

registry.register("parser.textract", TextractParser)
registry.register("parse.textract", lambda: ParseStage(parser=TextractParser()))
```

Now `parse.docling → parse.textract` in YAML swaps the engine; **the rest of the pipeline
(`verify → repair → normalize → evaluate`) is unchanged.** That is the whole point.

## Add a model provider (LLM/VLM)

`ExtractStage` (and repair/ensemble) take any `LLMClient`. The default is litellm-backed; a local model,
a CLI, or a hosted API is one method:

```python
from prismdoc.stages.extract import LLMClient, Completion, ExtractStage

class MyClient(LLMClient):
    def complete(self, prompt: str, *, response_format=None) -> Completion:
        text = my_model(prompt)                 # your call
        return Completion(text=text, usage=None)

stage = ExtractStage(schema, client=MyClient())
```

`Completion(text, usage=None, model=None)` — return `usage={"prompt_tokens", "completion_tokens"}` and the
cost ledger accounts it automatically; return `None` and it is recorded as unmetered (honest, not faked).

## Add a figure processor (VLM for figures)

```python
from prismdoc.stages.figures import FigureProcessor, Figure, FigureProcessStage

class VlmProcessor(FigureProcessor):
    def process(self, figure: Figure) -> str:
        return call_vlm(figure.image_b64)       # replaces the [[FIGURE:id]] placeholder

stage = FigureProcessStage(VlmProcessor())
```

## Add an input loader

```python
from prismdoc.stages.ingest import Loader
from prismdoc.models import Source, Page
from prismdoc import registry

class HtmlLoader(Loader):
    name = "html"
    extensions = (".html", ".htm")
    def load(self, source: Source) -> list[Page]:
        return [Page(index=0, text=read_html(source.path))]

registry.register("loader.html", HtmlLoader)
```

## Add a business rule

A rule is a factory returning a check `dict[fields] -> str | None` (None = pass, str = the violation detail):

```python
from prismdoc.stages.rules import register_rule

def before_after(field_before, field_after):
    def check(fields):
        a, b = fields.get(field_before), fields.get(field_after)
        if a is not None and b is not None and a > b:
            return f"{field_before} ({a}) is after {field_after} ({b})"
        return None
    return check

register_rule("date_order", before_after)
# YAML:  rules.default: {rules: [{type: date_order, field_before: start, field_after: end}]}
```

## Add a cascade scorer

A scorer maps a document to a quality score the cascade compares against its threshold:

```python
from prismdoc.stages.cascade import register_scorer

register_scorer("field_coverage", lambda doc: _fraction_of_fields_present(doc))
# YAML:  cascade: {primary: ..., fallback: ..., scorer: field_coverage, threshold: 0.8}
```

## Customize verification without new code

The verifiers are config-driven — pass your own domain labels:

```python
from prismdoc.stages.verify import LabelVerifyStage, TableColumnVerifyStage

LabelVerifyStage(field_labels={"amount": {"expect": ["amount", "total"], "reject": ["tax", "change"]}})
TableColumnVerifyStage(column_labels={"balance": {"expect_col": ["balance"], "reject_col": ["debit", "credit"]}})
```

## What NOT to extend here

By design prismdoc does not implement OCR, table detection, layout analysis, or VLMs — those are the
provider's job (Docling, pdfplumber, Textract, Azure DI, …). It also is not a RAG / agent / chat
framework. It is the routing, verification, normalization, provenance, and evaluation layer *between* your
documents and those engines. Extend it there.
