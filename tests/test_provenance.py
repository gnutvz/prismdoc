"""Tests for T-031 field provenance (page / bbox / source text)."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from prismdoc import (
    Block,
    Context,
    Document,
    ExtractStage,
    FieldProvenance,
    FieldSpec,
    IngestStage,
    LLMClient,
    NormalizeStage,
    Page,
    ParseStage,
    Pipeline,
    ProvenanceStage,
    Record,
    Source,
    TargetSchema,
    ValidateStage,
    build_pipeline,
    registry,
)
from prismdoc.api.app import app, get_runtime
from prismdoc.stages.extract import Completion
from prismdoc.stages.provenance import register_plugins as register_provenance


class FakeLLMClient(LLMClient):
    """Offline stand-in that returns a canned JSON array."""

    def __init__(self, response: str) -> None:
        self.response = response

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        return Completion(text=self.response)


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
            FieldSpec(name="price", type="number", required=True),
            FieldSpec(name="vendor", type="string", required=False),
        ]
    )


def _pdf_like_doc(
    fields: dict,
    *,
    pages: list[Page] | None = None,
) -> Document:
    return Document(
        source=Source(path="/tmp/invoice.pdf", mime="application/pdf"),
        pages=pages
        or [
            Page(
                index=0,
                text="Invoice ACME Corp total 12.50 SKU W-001",
                blocks=[
                    Block(
                        text="ACME Corp",
                        bbox=(10.0, 20.0, 110.0, 40.0),
                    ),
                    Block(
                        text="total 12.50",
                        bbox=(10.0, 50.0, 90.0, 70.0),
                    ),
                    Block(
                        text="SKU W-001",
                        bbox=(10.0, 80.0, 80.0, 100.0),
                    ),
                ],
            )
        ],
        records=[Record(fields=fields)],
    )


def test_block_match_sets_page_bbox_and_source_text() -> None:
    doc = _pdf_like_doc(
        {"name": "ACME Corp", "sku": "W-001", "price": 12.5, "vendor": ""}
    )
    result = ProvenanceStage().run(doc, Context())
    prov = result.records[0].provenance

    assert "name" in prov
    assert prov["name"].page == 0
    assert prov["name"].bbox == (10.0, 20.0, 110.0, 40.0)
    assert prov["name"].source_text == "ACME Corp"

    assert prov["sku"].page == 0
    assert prov["sku"].bbox == (10.0, 80.0, 80.0, 100.0)
    assert "W-001" in prov["sku"].source_text


def test_page_text_only_match_has_bbox_none() -> None:
    """Value in page.text but not in any block → page set, bbox=None."""
    doc = Document(
        source=Source(path="/tmp/x.pdf"),
        pages=[
            Page(
                index=1,
                text="Hidden vendor line: Globex Industries",
                blocks=[Block(text="Other block", bbox=(0.0, 0.0, 1.0, 1.0))],
            )
        ],
        records=[
            Record(
                fields={
                    "name": "Other block",
                    "sku": "X",
                    "price": 1.0,
                    "vendor": "Globex Industries",
                }
            )
        ],
    )
    result = ProvenanceStage().run(doc, Context())
    prov = result.records[0].provenance

    assert prov["vendor"].page == 1
    assert prov["vendor"].bbox is None
    assert "Globex Industries" in prov["vendor"].source_text
    # Block match still preferred when available
    assert prov["name"].bbox == (0.0, 0.0, 1.0, 1.0)


def test_absent_value_has_no_provenance_entry() -> None:
    doc = _pdf_like_doc(
        {
            "name": "ACME Corp",
            "sku": "W-001",
            "price": 12.5,
            "vendor": "Not In Document LLC",
        }
    )
    result = ProvenanceStage().run(doc, Context())
    prov = result.records[0].provenance

    assert "vendor" not in prov
    assert "name" in prov
    assert "sku" in prov


def test_empty_field_skipped() -> None:
    doc = _pdf_like_doc(
        {"name": "ACME Corp", "sku": "W-001", "price": 12.5, "vendor": ""}
    )
    result = ProvenanceStage().run(doc, Context())
    assert "vendor" not in result.records[0].provenance


def test_numeric_provenance_via_shared_matching() -> None:
    """``12.5`` locates against ``12.50`` in block text (shared matching)."""
    doc = _pdf_like_doc(
        {"name": "ACME Corp", "sku": "W-001", "price": 12.5}
    )
    result = ProvenanceStage().run(doc, Context())
    price_prov = result.records[0].provenance["price"]

    assert price_prov.page == 0
    assert price_prov.bbox == (10.0, 50.0, 90.0, 70.0)
    assert "12.50" in price_prov.source_text


def test_pipeline_stage_end_to_end(tmp_path: Path) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99")
    pdf.save(pdf_path)
    pdf.close()

    canned = [{"name": "Widget A", "sku": "W-001", "price": "9.99", "vendor": ""}]
    schema = _schema()
    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    pipeline = Pipeline(
        [
            IngestStage(),
            ParseStage(),
            ExtractStage(
                schema=schema,
                client=FakeLLMClient(json.dumps(canned)),
            ),
            ValidateStage(schema=schema),
            NormalizeStage(),
            ProvenanceStage(),
        ]
    )
    result = pipeline.run(doc, Context())

    assert result.records
    assert [entry.stage for entry in result.trace][-1] == "provenance"
    assert all(entry.ok for entry in result.trace)
    # After parse, page text should ground the extracted fields
    prov = result.records[0].provenance
    assert "name" in prov or "sku" in prov or "price" in prov
    for entry in prov.values():
        assert isinstance(entry, FieldProvenance)
        assert entry.page is not None


def test_api_extract_returns_provenance_block(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99")
    pdf.save(pdf_path)
    pdf.close()

    schema = _schema()
    canned = [{"name": "Widget A", "sku": "W-001", "price": 9.99, "vendor": ""}]

    def runtime() -> tuple[Pipeline, Context]:
        pipeline = Pipeline(
            [
                IngestStage(),
                ParseStage(),
                ExtractStage(
                    schema=schema,
                    client=FakeLLMClient(json.dumps(canned)),
                ),
                ValidateStage(schema=schema),
                NormalizeStage(),
                ProvenanceStage(),
            ]
        )
        return pipeline, Context(target_schema=schema)

    client = TestClient(app)
    app.dependency_overrides[get_runtime] = runtime
    try:
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={"file": ("catalog.pdf", handle, "application/pdf")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert "provenance" in body
    assert isinstance(body["provenance"], list)
    assert len(body["provenance"]) == 1
    # At least one grounded field should appear
    record_prov = body["provenance"][0]
    assert isinstance(record_prov, dict)
    assert any(
        key in record_prov for key in ("name", "sku", "price")
    )
    for field_prov in record_prov.values():
        assert "page" in field_prov
        assert "bbox" in field_prov
        assert "source_text" in field_prov


def test_provenance_yaml_stage_and_exports() -> None:
    register_provenance()
    assert "provenance.default" in registry.get_keys()
    stage = registry.create("provenance.default")
    assert isinstance(stage, ProvenanceStage)

    import prismdoc

    assert prismdoc.ProvenanceStage is ProvenanceStage
    assert prismdoc.FieldProvenance is FieldProvenance

    pipeline, _ctx = build_pipeline(
        {
            "schema": {
                "fields": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "price", "type": "number"},
                ]
            },
            "pipeline": [
                "ingest.default",
                "provenance.default",
            ],
        }
    )
    assert [s.name for s in pipeline.stages] == ["ingest", "provenance"]


def _repeated_value_doc(field_evidence: dict | None = None) -> Document:
    """A doc where 10.00 appears in three blocks (Subtotal / Tax / Total)."""
    return Document(
        source=Source(path="/tmp/receipt.pdf", mime="application/pdf"),
        pages=[
            Page(
                index=0,
                text="Subtotal 10.00 Tax 10.00 Total 10.00",
                blocks=[
                    Block(text="Subtotal 10.00", bbox=(0.0, 0.0, 1.0, 1.0)),
                    Block(text="Tax 10.00", bbox=(0.0, 2.0, 1.0, 3.0)),
                    Block(text="Total 10.00", bbox=(0.0, 4.0, 1.0, 5.0)),
                ],
            )
        ],
        records=[
            Record(fields={"price": 10.00}, field_evidence=field_evidence or {})
        ],
    )


def test_evidence_disambiguates_a_repeated_value() -> None:
    """Cited evidence locates the RIGHT occurrence; bare value search would pick the first."""
    # Without evidence: reverse value-search grabs the first block (Subtotal) — ambiguous.
    no_ev = ProvenanceStage().run(_repeated_value_doc(), Context())
    assert no_ev.records[0].provenance["price"].bbox == (0.0, 0.0, 1.0, 1.0)
    assert no_ev.records[0].provenance["price"].method == "value_search"

    # With evidence "Total 10.00": provenance locates the Total block instead.
    with_ev = ProvenanceStage().run(
        _repeated_value_doc({"price": "Total 10.00"}), Context()
    )
    prov = with_ev.records[0].provenance["price"]
    assert prov.bbox == (0.0, 4.0, 1.0, 5.0)
    assert prov.method == "evidence"
    assert prov.evidence == "Total 10.00"


def test_hallucinated_evidence_falls_back_to_value_search() -> None:
    """A cited span not present in the document is not trusted; fall back, don't fabricate."""
    doc = _repeated_value_doc({"price": "Grand Total 999.99 never in doc"})
    result = ProvenanceStage().run(doc, Context())
    prov = result.records[0].provenance["price"]
    assert prov.method == "value_search"          # evidence rejected
    assert prov.bbox == (0.0, 0.0, 1.0, 1.0)      # located the value best-effort


def test_extract_evidence_mode_splits_field_evidence() -> None:
    """ExtractStage(evidence=True) keeps _evidence out of fields and into field_evidence."""
    schema = TargetSchema(fields=[FieldSpec(name="total", type="number", required=True)])
    client = FakeLLMClient(
        '{"records": [{"total": "10.00", "_evidence": {"total": "TOTAL 10.00"}}]}'
    )
    doc = Document(source=Source(path="/tmp/x.txt"))
    doc.artifacts["parsed_markdown"] = "Some receipt TOTAL 10.00"
    result = ExtractStage(schema, client=client, evidence=True).run(doc, Context())

    rec = result.records[0]
    assert rec.fields == {"total": "10.00"}          # _evidence stripped from values
    assert rec.field_evidence == {"total": "TOTAL 10.00"}


def test_extract_without_evidence_mode_ignores_evidence_key() -> None:
    """Default mode still strips a stray _evidence but does not populate field_evidence."""
    schema = TargetSchema(fields=[FieldSpec(name="total", type="number", required=True)])
    client = FakeLLMClient(
        '{"records": [{"total": "10.00", "_evidence": {"total": "TOTAL 10.00"}}]}'
    )
    doc = Document(source=Source(path="/tmp/x.txt"))
    doc.artifacts["parsed_markdown"] = "Some receipt TOTAL 10.00"
    result = ExtractStage(schema, client=client).run(doc, Context())

    rec = result.records[0]
    assert rec.fields == {"total": "10.00"}
    assert rec.field_evidence == {}
