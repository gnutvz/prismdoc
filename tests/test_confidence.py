"""Tests for T-014 ConfidenceStage (per-field scores + low-confidence flags)."""

from __future__ import annotations

import json
from pathlib import Path

import fitz

from prismdoc import (
    ConfidenceStage,
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
    build_pipeline,
    registry,
)
from prismdoc.stages.confidence import register_plugins as register_confidence
from prismdoc.stages.extract import Completion


class FakeLLMClient(LLMClient):
    """Offline stand-in that returns a canned JSON array."""

    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, prompt: str) -> Completion:
        return Completion(text=self.response)


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
            FieldSpec(name="price", type="number", required=True),
            FieldSpec(name="qty", type="integer", required=False),
        ]
    )


def test_all_fields_valid_confidence_0_9_no_flags() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(
                fields={
                    "name": "Widget",
                    "sku": "W-1",
                    "price": 12.5,
                    "qty": 3,
                }
            )
        ],
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence == {
        "name": 0.9,
        "sku": 0.9,
        "price": 0.9,
        "qty": 0.9,
    }
    assert result.artifacts["low_confidence"] == []


def test_valid_with_fallback_tier_scales_by_0_85() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(
                fields={
                    "name": "Widget",
                    "sku": "W-1",
                    "price": 12.5,
                    "qty": 3,
                }
            )
        ],
        artifacts={"router": [{"tier": "fallback", "score": 0.1}]},
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    expected = round(0.9 * 0.85, 10)
    assert result.records[0].confidence == {
        "name": expected,
        "sku": expected,
        "price": expected,
        "qty": expected,
    }
    assert result.artifacts["low_confidence"] == []


def test_missing_required_field_confidence_0_and_flagged() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[Record(fields={"name": "Widget", "price": 1.0, "qty": 1})],
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["sku"] == 0.0
    assert {
        "record": 0,
        "field": "sku",
        "confidence": 0.0,
    } in result.artifacts["low_confidence"]


def test_type_mismatch_confidence_0_5() -> None:
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
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["qty"] == 0.5


def test_threshold_controls_which_fields_are_flagged() -> None:
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
    # Default threshold 0.5: mismatch at 0.5 is not below threshold
    default = ConfidenceStage(schema=_schema()).run(doc.model_copy(deep=True), Context())
    assert all(entry["field"] != "qty" for entry in default.artifacts["low_confidence"])

    # Higher threshold flags the 0.5 mismatch
    strict = ConfidenceStage(schema=_schema(), threshold=0.6).run(doc, Context())
    assert {
        "record": 0,
        "field": "qty",
        "confidence": 0.5,
    } in strict.artifacts["low_confidence"]


def test_preset_confidence_preserved() -> None:
    doc = Document(
        source=Source(path="/tmp/x.md"),
        records=[
            Record(
                fields={
                    "name": "Widget",
                    "sku": "W-1",
                    "price": 1.0,
                    "qty": 2,
                },
                confidence={"price": 0.42},
            )
        ],
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["price"] == 0.42
    assert result.records[0].confidence["name"] == 0.9


def test_pipeline_ingest_to_confidence_offline(tmp_path: Path) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99")
    pdf.save(pdf_path)
    pdf.close()

    canned = [
        {
            "name": "Widget A",
            "sku": "W-001",
            "price": "9.99",
            "qty": "3",
        }
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
            ConfidenceStage(schema=schema),
        ]
    )
    result = pipeline.run(doc, Context())

    assert result.records
    assert result.records[0].confidence["name"] == 0.9
    assert result.records[0].confidence["sku"] == 0.9
    assert result.records[0].confidence["price"] == 0.9
    assert result.records[0].confidence["qty"] == 0.9
    assert result.artifacts["low_confidence"] == []
    assert [entry.stage for entry in result.trace] == [
        "ingest",
        "parse",
        "extract",
        "validate",
        "normalize",
        "confidence",
    ]
    assert all(entry.ok for entry in result.trace)


def test_confidence_yaml_stage_and_exports() -> None:
    register_confidence()
    assert "confidence.default" in registry.get_keys()
    stage = registry.create("confidence.default", schema=_schema(), threshold=0.7)
    assert isinstance(stage, ConfidenceStage)
    assert stage.threshold == 0.7

    import prismdoc

    assert prismdoc.ConfidenceStage is ConfidenceStage

    pipeline, ctx = build_pipeline(
        {
            "schema": {
                "fields": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "price", "type": "number"},
                ]
            },
            "pipeline": [
                "ingest.default",
                "validate.default",
                {"confidence.default": {"threshold": 0.8}},
            ],
        }
    )
    assert [s.name for s in pipeline.stages] == ["ingest", "validate", "confidence"]
    conf = pipeline.stages[2]
    assert isinstance(conf, ConfidenceStage)
    assert conf.threshold == 0.8
    assert conf.schema.field_names() == ctx.target_schema.field_names()
