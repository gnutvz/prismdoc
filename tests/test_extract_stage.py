"""Tests for T-004 extract stage (schema-driven, injectable LLM client)."""

from __future__ import annotations

import builtins
from pathlib import Path

import fitz
import pytest

from prismdoc import (
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    IngestStage,
    LLMClient,
    Page,
    ParseStage,
    Pipeline,
    Source,
    TargetSchema,
    registry,
)
from prismdoc.stages.extract import Completion, LiteLLMClient, register_plugins

_CANNED_PRODUCTS = [
    {
        "name": "Widget A",
        "sku": "W-001",
        "price": 9.99,
        "currency": "USD",
    },
    {
        "name": "Widget B",
        "sku": "W-002",
        "price": 14.5,
        "currency": "USD",
    },
]


class FakeLLMClient(LLMClient):
    """Offline stand-in that returns a canned JSON array."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> Completion:
        self.prompts.append(prompt)
        return Completion(text=self.response)


def _product_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", description="Product name", required=True),
            FieldSpec(name="sku", type="string", description="Stock keeping unit", required=True),
            FieldSpec(name="price", type="number", description="Unit price", required=True),
            FieldSpec(
                name="currency",
                type="string",
                description="ISO currency code",
                required=False,
            ),
        ]
    )


def _canned_json() -> str:
    import json

    return json.dumps(_CANNED_PRODUCTS)


def test_target_schema_field_names_and_describe() -> None:
    schema = _product_schema()

    assert schema.field_names() == ["name", "sku", "price", "currency"]
    desc = schema.describe()
    assert "name" in desc
    assert "string" in desc
    assert "required" in desc
    assert "Product name" in desc
    assert "optional" in desc
    assert "currency" in desc


def test_extract_stage_populates_records() -> None:
    doc = Document(
        source=Source(path="/tmp/catalog.md", mime="text/markdown"),
        pages=[Page(index=0, text="Widget A W-001 $9.99\nWidget B W-002 $14.50")],
    )
    client = FakeLLMClient(_canned_json())
    result = ExtractStage(schema=_product_schema(), client=client).run(doc, Context())

    assert len(result.records) == 2
    assert result.records[0].fields["name"] == "Widget A"
    assert result.records[0].fields["sku"] == "W-001"
    assert result.records[1].fields["name"] == "Widget B"
    assert result.records[1].fields["sku"] == "W-002"
    assert client.prompts
    assert "JSON array" in client.prompts[0]


def test_extract_parses_json_fenced_response() -> None:
    fenced = "Here you go:\n```json\n" + _canned_json() + "\n```\n"
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        artifacts={"parsed_markdown": "catalog text"},
    )
    result = ExtractStage(
        schema=_product_schema(),
        client=FakeLLMClient(fenced),
    ).run(doc, Context())

    assert len(result.records) == 2
    assert result.records[0].fields["sku"] == "W-001"


def test_extract_unparseable_raises_clear_error() -> None:
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        pages=[Page(index=0, text="noise")],
    )
    with pytest.raises(ValueError, match=r"could not parse a JSON array"):
        ExtractStage(
            schema=_product_schema(),
            client=FakeLLMClient("sorry, I cannot help with that"),
        ).run(doc, Context())


def test_pipeline_ingest_parse_extract(tmp_path: Path) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99 USD\nWidget B W-002 14.50 USD")
    pdf.save(pdf_path)
    pdf.close()

    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    pipeline = Pipeline(
        [
            IngestStage(),
            ParseStage(),
            ExtractStage(schema=_product_schema(), client=FakeLLMClient(_canned_json())),
        ]
    )
    result = pipeline.run(doc, Context())

    assert result.artifacts.get("parsed_markdown")
    assert len(result.records) == 2
    assert result.records[0].fields["name"] == "Widget A"
    assert result.records[1].fields["sku"] == "W-002"
    assert [entry.stage for entry in result.trace] == ["ingest", "parse", "extract"]
    assert all(entry.ok for entry in result.trace)


def test_litellm_client_raises_clear_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "litellm" or name.startswith("litellm."):
            raise ImportError("No module named 'litellm'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match=r"prismdoc\[llm\]"):
        LiteLLMClient().complete("hello")


def test_extract_exports_and_registry() -> None:
    assert issubclass(ExtractStage, object)
    assert issubclass(LLMClient, object)

    register_plugins()
    keys = registry.get_keys()
    assert "extractor.litellm" in keys
    assert "extract.default" in keys

    client = registry.create("extractor.litellm")
    assert isinstance(client, LiteLLMClient)

    stage = registry.create("extract.default", schema=_product_schema())
    assert isinstance(stage, ExtractStage)
