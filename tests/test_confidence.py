"""Tests for ConfidenceStage (grounding heuristic + low-confidence flags)."""

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
    Page,
    ParseStage,
    Pipeline,
    Record,
    Source,
    TargetSchema,
    ValidateStage,
    build_pipeline,
    registry,
)
import prismdoc.stages.confidence as confidence_mod
from prismdoc.stages.confidence import _is_grounded
from prismdoc.stages.confidence import register_plugins as register_confidence
from prismdoc.stages.extract import Completion


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
            FieldSpec(name="qty", type="integer", required=False),
        ]
    )


def _doc_with_text(
    fields: dict,
    text: str,
    *,
    confidence: dict[str, float] | None = None,
    artifacts: dict | None = None,
) -> Document:
    arts = dict(artifacts or {})
    arts.setdefault("parsed_markdown", text)
    return Document(
        source=Source(path="/tmp/x.md"),
        pages=[Page(index=0, text=text)],
        records=[Record(fields=fields, confidence=confidence or {})],
        artifacts=arts,
    )


def test_grounded_value_confidence_0_9() -> None:
    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 12.5, "qty": 3},
        "Catalog: Widget sku W-1 price 12.5 qty 3",
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence == {
        "name": 0.9,
        "sku": 0.9,
        "price": 0.9,
        "qty": 0.9,
    }
    assert result.artifacts["low_confidence"] == []


def test_ungrounded_hallucination_0_4_with_reason() -> None:
    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 12.5, "qty": 3},
        "Catalog lists Widget and W-1 only",
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["price"] == 0.4
    assert result.records[0].confidence["qty"] == 0.4
    assert result.records[0].confidence["name"] == 0.9
    assert {
        "record": 0,
        "field": "price",
        "confidence": 0.4,
        "reason": "ungrounded",
    } in result.artifacts["low_confidence"]
    assert {
        "record": 0,
        "field": "qty",
        "confidence": 0.4,
        "reason": "ungrounded",
    } in result.artifacts["low_confidence"]


def test_numeric_grounding_tolerates_formatting() -> None:
    assert _is_grounded(12.5, "total is 12.50 EUR")
    assert _is_grounded("12.5", "amount 12,5 units")

    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 12.5, "qty": 3},
        "Widget W-1 costs 12.50; qty 3",
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())
    assert result.records[0].confidence["price"] == 0.9


def test_missing_required_field_confidence_0_and_flagged() -> None:
    doc = _doc_with_text(
        {"name": "Widget", "price": 1.0, "qty": 1},
        "Widget price 1.0 qty 1",
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["sku"] == 0.0
    assert {
        "record": 0,
        "field": "sku",
        "confidence": 0.0,
        "reason": "missing",
    } in result.artifacts["low_confidence"]


def test_type_mismatch_confidence_0_3() -> None:
    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 1.0, "qty": "abc"},
        "Widget W-1 price 1.0 qty abc",
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["qty"] == 0.3
    assert {
        "record": 0,
        "field": "qty",
        "confidence": 0.3,
        "reason": "type_mismatch",
    } in result.artifacts["low_confidence"]


def test_fallback_tier_no_longer_scales_confidence() -> None:
    assert not hasattr(confidence_mod, "_FALLBACK_SCALE")
    assert not hasattr(confidence_mod, "_fallback_scale")

    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 12.5, "qty": 3},
        "Widget W-1 12.5 qty 3",
        artifacts={"router": [{"tier": "fallback", "score": 0.1}]},
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence == {
        "name": 0.9,
        "sku": 0.9,
        "price": 0.9,
        "qty": 0.9,
    }
    assert result.artifacts["low_confidence"] == []


def test_threshold_controls_which_fields_are_flagged() -> None:
    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 1.0, "qty": "abc"},
        "Widget W-1 1.0",
    )
    # Default threshold 0.5: type mismatch at 0.3 is flagged
    default = ConfidenceStage(schema=_schema()).run(doc.model_copy(deep=True), Context())
    assert {
        "record": 0,
        "field": "qty",
        "confidence": 0.3,
        "reason": "type_mismatch",
    } in default.artifacts["low_confidence"]

    # Lower threshold can exclude the mismatch
    lenient = ConfidenceStage(schema=_schema(), threshold=0.25).run(doc, Context())
    assert all(entry["field"] != "qty" for entry in lenient.artifacts["low_confidence"])


def test_preset_confidence_preserved() -> None:
    doc = _doc_with_text(
        {"name": "Widget", "sku": "W-1", "price": 1.0, "qty": 2},
        "Widget W-1 1.0 qty 2",
        confidence={"price": 0.42},
    )
    result = ConfidenceStage(schema=_schema()).run(doc, Context())

    assert result.records[0].confidence["price"] == 0.42
    assert result.records[0].confidence["name"] == 0.9


def test_pipeline_ingest_to_confidence_offline(tmp_path: Path) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99 qty 3")
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
