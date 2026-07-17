"""Tests for T-005 ValidateStage and NormalizeStage."""

from __future__ import annotations

import json
from pathlib import Path

import fitz

from prismdoc import (
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    IngestStage,
    LLMClient,
    NormalizeStage,
    ParseStage,
    Pipeline,
    Record,
    Source,
    TargetSchema,
    ValidateStage,
    registry,
)
from prismdoc.stages.normalize import register_plugins as register_normalize
from prismdoc.stages.validate import register_plugins as register_validate


class FakeLLMClient(LLMClient):
    """Offline stand-in that returns a canned JSON array."""

    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, prompt: str) -> str:
        return self.response


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
            FieldSpec(name="price", type="number", required=True),
            FieldSpec(name="qty", type="integer", required=False),
            FieldSpec(name="active", type="boolean", required=False),
        ]
    )


def test_missing_required_field_recorded_as_invalid() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[Record(fields={"name": "Widget", "price": 1.0})],
    )
    result = ValidateStage(schema=_schema()).run(doc, Context())

    summary = result.artifacts["validation"]
    assert summary["invalid"] == 1
    assert summary["valid"] == 0
    assert any("sku" in err and "required" in err for err in summary["errors"])


def test_coercion_number_integer_boolean() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(
                fields={
                    "name": "Widget",
                    "sku": "W-1",
                    "price": "12.5",
                    "qty": "7",
                    "active": "yes",
                }
            )
        ],
    )
    result = ValidateStage(schema=_schema()).run(doc, Context())

    fields = result.records[0].fields
    assert fields["price"] == 12.5
    assert isinstance(fields["price"], float)
    assert fields["qty"] == 7
    assert isinstance(fields["qty"], int)
    assert fields["active"] is True
    assert result.artifacts["validation"]["valid"] == 1
    assert result.artifacts["validation"]["invalid"] == 0
    assert result.artifacts["validation"]["errors"] == []


def test_bad_coercion_keeps_original_and_records_error() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(
                fields={
                    "name": "Widget",
                    "sku": "W-1",
                    "price": 1.0,
                    "qty": "abc",
                }
            )
        ],
    )
    result = ValidateStage(schema=_schema()).run(doc, Context())

    assert result.records[0].fields["qty"] == "abc"
    summary = result.artifacts["validation"]
    assert summary["invalid"] == 1
    assert summary["valid"] == 0
    assert any("qty" in err for err in summary["errors"])


def test_normalize_trims_collapses_and_empty_to_none() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(
                fields={
                    "name": "  Widget   A  ",
                    "sku": "",
                    "note": "one\t\ttwo",
                }
            )
        ],
    )
    result = NormalizeStage().run(doc, Context())

    fields = result.records[0].fields
    assert fields["name"] == "Widget A"
    assert fields["sku"] is None
    assert fields["note"] == "one two"
    assert result.artifacts["normalize"]["deduped"] == 0


def test_normalize_dedups_identical_records() -> None:
    fields = {"name": "Widget", "sku": "W-1"}
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(fields=dict(fields)),
            Record(fields=dict(fields)),
            Record(fields={"name": "Other", "sku": "W-2"}),
            Record(fields=dict(fields)),
        ],
    )
    result = NormalizeStage().run(doc, Context())

    assert len(result.records) == 2
    assert result.records[0].fields == fields
    assert result.records[1].fields == {"name": "Other", "sku": "W-2"}
    assert result.artifacts["normalize"]["deduped"] == 2


def test_pipeline_ingest_parse_extract_validate_normalize(tmp_path: Path) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99")
    pdf.save(pdf_path)
    pdf.close()

    canned = [
        {
            "name": "  Widget   A  ",
            "sku": "W-001",
            "price": "9.99",
            "qty": "3",
            "active": "yes",
        },
        {
            "name": "Widget A",
            "sku": "W-001",
            "price": 9.99,
            "qty": 3,
            "active": True,
        },
    ]
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
        ]
    )
    result = pipeline.run(doc, Context())

    assert result.records
    assert "validation" in result.artifacts
    assert "normalize" in result.artifacts
    assert result.artifacts["validation"]["valid"] >= 1
    assert result.artifacts["normalize"]["deduped"] >= 1
    assert result.records[0].fields["name"] == "Widget A"
    assert result.records[0].fields["price"] == 9.99
    assert result.records[0].fields["qty"] == 3
    assert result.records[0].fields["active"] is True
    assert [entry.stage for entry in result.trace] == [
        "ingest",
        "parse",
        "extract",
        "validate",
        "normalize",
    ]
    assert all(entry.ok for entry in result.trace)


def test_validate_normalize_exports_and_registry() -> None:
    register_validate()
    register_normalize()
    keys = registry.get_keys()
    assert "validate.default" in keys
    assert "normalize.default" in keys

    stage_v = registry.create("validate.default", schema=_schema())
    assert isinstance(stage_v, ValidateStage)

    stage_n = registry.create("normalize.default")
    assert isinstance(stage_n, NormalizeStage)
