"""Tests for T-006 config-as-YAML pipeline loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismdoc import (
    Context,
    ExtractStage,
    Pipeline,
    TargetSchema,
    ValidateStage,
    build_pipeline,
    load_pipeline,
)
from prismdoc.stages.extract import LiteLLMClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RETAIL_YAML = _REPO_ROOT / "examples" / "retail" / "pipeline.yaml"

_SAMPLE_CONFIG: dict = {
    "schema": {
        "fields": [
            {"name": "name", "type": "string", "required": True},
            {"name": "sku", "type": "string"},
            {"name": "price", "type": "number"},
        ]
    },
    "pipeline": [
        "ingest.default",
        "parse.default",
        {"extract.default": {"model": "gpt-4o-mini"}},
        "validate.default",
        "normalize.default",
    ],
}


def test_build_pipeline_stage_order_and_context_schema() -> None:
    pipeline, ctx = build_pipeline(_SAMPLE_CONFIG)

    assert isinstance(pipeline, Pipeline)
    assert [stage.name for stage in pipeline.stages] == [
        "ingest",
        "parse",
        "extract",
        "validate",
        "normalize",
    ]
    assert isinstance(ctx, Context)
    assert isinstance(ctx.target_schema, TargetSchema)


def test_build_pipeline_target_schema_fields() -> None:
    _, ctx = build_pipeline(_SAMPLE_CONFIG)
    schema = ctx.target_schema
    assert schema is not None
    assert schema.field_names() == ["name", "sku", "price"]

    by_name = {field.name: field for field in schema.fields}
    assert by_name["name"].type == "string"
    assert by_name["name"].required is True
    assert by_name["sku"].type == "string"
    assert by_name["sku"].required is False
    assert by_name["price"].type == "number"
    assert by_name["price"].required is False


def test_schema_injection_into_validate_and_extract() -> None:
    pipeline, ctx = build_pipeline(_SAMPLE_CONFIG)
    assert ctx.target_schema is not None

    extract = pipeline.stages[2]
    validate = pipeline.stages[3]
    assert isinstance(extract, ExtractStage)
    assert isinstance(validate, ValidateStage)
    assert extract.schema.field_names() == ctx.target_schema.field_names()
    assert validate.schema.field_names() == ["name", "sku", "price"]
    assert validate.schema.field_names() == ctx.target_schema.field_names()


def test_per_step_params_passed_to_extract() -> None:
    pipeline, _ = build_pipeline(_SAMPLE_CONFIG)
    extract = pipeline.stages[2]
    assert isinstance(extract, ExtractStage)
    assert isinstance(extract.client, LiteLLMClient)
    assert extract.client.model == "gpt-4o-mini"


def test_unknown_stage_key_raises_clear_error() -> None:
    config = {
        "schema": {"fields": []},
        "pipeline": ["ingest.default", "no.such.stage"],
    }
    with pytest.raises(KeyError, match=r"Unknown stage key"):
        build_pipeline(config)


def test_missing_pipeline_key_raises() -> None:
    with pytest.raises(ValueError, match=r"missing required key 'pipeline'"):
        build_pipeline({"schema": {"fields": []}})


def test_malformed_pipeline_item_raises() -> None:
    with pytest.raises(ValueError, match=r"Pipeline item\[0\]"):
        build_pipeline({"pipeline": [{"a": {}, "b": {}}]})


def test_load_pipeline_retail_example() -> None:
    pipeline, ctx = load_pipeline(_RETAIL_YAML)

    assert isinstance(pipeline, Pipeline)
    assert [stage.name for stage in pipeline.stages] == [
        "ingest",
        "parse",
        "extract",
        "validate",
        "normalize",
    ]
    assert isinstance(ctx.target_schema, TargetSchema)
    assert ctx.target_schema.field_names() == [
        "name",
        "sku",
        "price",
        "currency",
        "unit",
        "brand",
        "category",
    ]
    extract = pipeline.stages[2]
    assert isinstance(extract, ExtractStage)
    assert isinstance(extract.client, LiteLLMClient)
    assert extract.client.model == "gpt-4o-mini"


def test_exports_from_prismdoc() -> None:
    import prismdoc

    assert callable(prismdoc.load_pipeline)
    assert callable(prismdoc.build_pipeline)
