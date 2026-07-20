"""Tests for T-047 document archetype classifier + router."""

from __future__ import annotations

from prismdoc import (
    ArchetypeRouterStage,
    Context,
    Document,
    DocumentArchetype,
    FieldSpec,
    Record,
    Source,
    TargetSchema,
    classify_archetype,
    registry,
)
from prismdoc.stages.archetype import register_plugins as register_archetype

INVOICE_TABLE = """\
|   No. | Description   | Qty  | Net price | Net worth | VAT [%] | Gross worth |
|-------|---------------|------|-----------|-----------|---------|-------------|
|    1. | Corkscrew ... | 1,00 | 7,50      | 7,50      | 10%     | 8,25        |
"""

_SCHEMA = TargetSchema(fields=[FieldSpec(name="total", type="number")])


def _doc(
    text: str,
    *,
    figures: list | None = None,
    fields: dict | None = None,
    field_evidence: dict | None = None,
) -> Document:
    artifacts: dict = {"parsed_markdown": text}
    if figures is not None:
        artifacts["figures"] = figures
    return Document(
        source=Source(path="/tmp/doc.pdf", mime="application/pdf"),
        artifacts=artifacts,
        records=[
            Record(
                fields=fields or {"total": 8.25},
                field_evidence=field_evidence or {},
            )
        ],
    )


def test_classify_flat() -> None:
    doc = _doc("Store ABC\nReceipt #42\nTotal: 12.50\nThank you")
    archetype, signals = classify_archetype(doc)
    assert archetype is DocumentArchetype.FLAT
    assert signals["has_figures"] is False
    assert signals["n_tables"] == 0
    assert signals["n_headings"] == 0


def test_classify_tabular() -> None:
    doc = _doc("# Invoice\n\n" + INVOICE_TABLE)
    archetype, signals = classify_archetype(doc)
    assert archetype is DocumentArchetype.TABULAR
    assert signals["n_tables"] >= 1
    assert signals["n_headings"] < 5


def test_classify_hierarchical() -> None:
    headings = "\n".join(f"# Section {i}" for i in range(5))
    doc = _doc(headings + "\n\nBody text.")
    archetype, signals = classify_archetype(doc)
    assert archetype is DocumentArchetype.HIERARCHICAL
    assert signals["n_headings"] >= 5


def test_classify_visual_and_mixed() -> None:
    figure = {"page": 1, "bbox": [0, 0, 10, 10]}
    short = _doc("Short caption.", figures=[figure])
    archetype_v, signals_v = classify_archetype(short)
    assert archetype_v is DocumentArchetype.VISUAL
    assert signals_v["has_figures"] is True
    assert signals_v["text_len"] < 800

    long_text = "x" * 800
    mixed = _doc(long_text, figures=[figure])
    archetype_m, signals_m = classify_archetype(mixed)
    assert archetype_m is DocumentArchetype.MIXED
    assert signals_m["text_len"] >= 800


def test_router_annotates() -> None:
    doc = _doc("Plain receipt text only.")
    result = ArchetypeRouterStage().run(doc, Context())
    assert result.artifacts["archetype"] == DocumentArchetype.FLAT.value
    assert "archetype_signals" in result.artifacts
    assert "archetype_route" not in result.artifacts


def test_router_routes_tabular_to_column_verify() -> None:
    doc = _doc(INVOICE_TABLE, fields={"total": 8.25})
    result = ArchetypeRouterStage(schema=_SCHEMA, verify=True).run(doc, Context())
    assert result.artifacts["archetype"] == DocumentArchetype.TABULAR.value
    assert result.artifacts["archetype_route"]["verifier"] == "verify.column"
    assert "total" in result.records[0].field_column_verification


def test_router_routes_flat_to_label_verify() -> None:
    doc = _doc(
        "Grand Total 8.25",
        fields={"total": 8.25},
        field_evidence={"total": "Grand Total 8.25"},
    )
    result = ArchetypeRouterStage(schema=_SCHEMA, verify=True).run(doc, Context())
    assert result.artifacts["archetype"] == DocumentArchetype.FLAT.value
    assert result.artifacts["archetype_route"]["verifier"] == "verify.label"
    assert "total" in result.records[0].field_verification


def test_registry_and_export() -> None:
    register_archetype()
    stage = registry.create("archetype.router")
    assert isinstance(stage, ArchetypeRouterStage)

    from prismdoc import (
        ArchetypeRouterStage as ExportedRouter,
        DocumentArchetype as ExportedArchetype,
        classify_archetype as exported_classify,
    )

    assert ExportedRouter is ArchetypeRouterStage
    assert ExportedArchetype is DocumentArchetype
    assert exported_classify is classify_archetype
