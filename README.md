# prismdoc

Cost-aware, schema-driven document extraction pipeline (deploy-as-microservice).

## Quickstart / Demo

Offline end-to-end on a structured retail spreadsheet (no API key):

```
pip install -e .
python examples/retail/make_sample.py
python -m prismdoc.cli --config examples/retail/demo.yaml --input examples/retail/sample_catalog.xlsx --csv out.csv
```

Messy PDFs/images use the LLM extractor (`extract.default`), which needs
`pip install prismdoc[llm]` and a provider API key. The offline demo uses
`extract.table` on structured spreadsheets.
