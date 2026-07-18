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
