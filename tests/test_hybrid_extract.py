"""Tests for T-037 hybrid deterministic + LLM extraction."""

from __future__ import annotations

import json

import pytest

from prismdoc import (
    Context,
    Document,
    FieldSpec,
    HybridExtractStage,
    LLMClient,
    Page,
    Source,
    TargetSchema,
    build_pipeline,
    registry,
)
from prismdoc.stages.extract import Completion
from prismdoc.stages.hybrid_extract import DETERMINISTIC_MATCHERS, register_plugins


def _invoice_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="invoice_id", type="string", required=True),
            FieldSpec(name="total", type="number", required=True),
            FieldSpec(name="currency", type="string", required=True),
            FieldSpec(name="vendor", type="string", required=True),
        ]
    )


class SpyLLMClient(LLMClient):
    """Returns a canned payload; records call count and prompts."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.prompts: list[str] = []

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        self.prompts.append(prompt)
        self.calls += 1
        return Completion(text=self.response)


def _doc(text: str) -> Document:
    return Document(
        source=Source(path="/tmp/invoice.md"),
        pages=[Page(index=0, text=text)],
    )


def _prompt_instruction_section(prompt: str) -> str:
    """Schema/instructions portion of the extract prompt (before Document:)."""
    marker = "Document:"
    if marker in prompt:
        return prompt.split(marker, 1)[0]
    return prompt


def test_pattern_field_not_sent_to_llm() -> None:
    client = SpyLLMClient(
        json.dumps({"records": [{"vendor": "Acme Corp", "currency": "USD"}]})
    )
    text = "Total 99.50 USD. Invoice INV-42 from Acme Corp"
    stage = HybridExtractStage(
        schema=_invoice_schema(),
        field_rules={
            "invoice_id": {"pattern": r"INV-\d+", "group": 0},
            "total": {"matcher": "number"},
        },
        client=client,
    )
    result = stage.run(_doc(text), Context())

    assert client.calls == 1
    assert len(client.prompts) == 1
    instructions = _prompt_instruction_section(client.prompts[0])
    assert "invoice_id" not in instructions
    assert "total" not in instructions
    assert "vendor" in instructions
    assert "currency" in instructions

    record = result.records[0]
    assert record.fields["invoice_id"] == "INV-42"
    assert record.fields["total"] == "99.50"
    assert record.fields["vendor"] == "Acme Corp"
    assert record.fields["currency"] == "USD"


def test_all_deterministic_never_calls_client() -> None:
    client = SpyLLMClient(json.dumps({"records": [{"vendor": "SHOULD_NOT_APPEAR"}]}))
    text = "Total 12.00 USD. Invoice INV-99 vendor ignored"
    stage = HybridExtractStage(
        schema=TargetSchema(
            fields=[
                FieldSpec(name="invoice_id", type="string"),
                FieldSpec(name="total", type="number"),
                FieldSpec(name="currency", type="string"),
            ]
        ),
        field_rules={
            "invoice_id": {"pattern": r"INV-\d+"},
            "total": {"matcher": "number"},
            "currency": {"matcher": "currency"},
        },
        client=client,
    )
    result = stage.run(_doc(text), Context())

    assert client.calls == 0
    assert client.prompts == []
    assert len(result.records) == 1
    assert result.records[0].fields == {
        "invoice_id": "INV-99",
        "total": "12.00",
        "currency": "USD",
    }
    assert result.artifacts["hybrid"] == {
        "deterministic": ["invoice_id", "total", "currency"],
        "llm": [],
    }


def test_mixed_deterministic_and_llm_split() -> None:
    client = SpyLLMClient(json.dumps({"vendor": "Book Tak Sdn Bhd"}))
    # Put the amount before date digits so matcher "number" hits 105.00 first.
    text = "Total 105.00 MYR on 19/07/2026 — Book Tak Sdn Bhd"
    stage = HybridExtractStage(
        schema=TargetSchema(
            fields=[
                FieldSpec(name="date", type="string"),
                FieldSpec(name="total", type="number"),
                FieldSpec(name="currency", type="string"),
                FieldSpec(name="vendor", type="string"),
            ]
        ),
        field_rules={
            "date": {"matcher": "date"},
            "total": {"matcher": "number"},
            "currency": {"matcher": "currency"},
        },
        client=client,
    )
    result = stage.run(_doc(text), Context())

    assert client.calls == 1
    instructions = _prompt_instruction_section(client.prompts[0])
    assert "vendor" in instructions
    assert "date" not in instructions
    assert "total" not in instructions
    assert "currency" not in instructions

    record = result.records[0]
    assert record.fields["date"] == "19/07/2026"
    assert record.fields["total"] == "105.00"
    assert record.fields["currency"] == "MYR"
    assert record.fields["vendor"] == "Book Tak Sdn Bhd"
    assert result.artifacts["hybrid"] == {
        "deterministic": ["date", "total", "currency"],
        "llm": ["vendor"],
    }


def test_unmatched_pattern_falls_back_to_llm() -> None:
    client = SpyLLMClient(
        json.dumps({"records": [{"invoice_id": "FALLBACK-1", "vendor": "X"}]})
    )
    stage = HybridExtractStage(
        schema=TargetSchema(
            fields=[
                FieldSpec(name="invoice_id", type="string"),
                FieldSpec(name="vendor", type="string"),
            ]
        ),
        field_rules={
            "invoice_id": {"pattern": r"INV-\d+"},
        },
        client=client,
    )
    result = stage.run(_doc("No invoice code here, vendor X"), Context())

    assert client.calls == 1
    instructions = _prompt_instruction_section(client.prompts[0])
    assert "invoice_id" in instructions
    assert result.records[0].fields["invoice_id"] == "FALLBACK-1"
    assert result.artifacts["hybrid"]["deterministic"] == []
    assert result.artifacts["hybrid"]["llm"] == ["invoice_id", "vendor"]


@pytest.mark.parametrize(
    ("matcher_name", "sample", "expected"),
    [
        ("number", "Amount due: 42.75 today", "42.75"),
        ("currency", "Paid in EUR only", "EUR"),
        ("currency", "Total: $19.99", "USD"),
        ("date", "Issued on 2026-07-19", "2026-07-19"),
        ("date", "Issued on 19/07/2026", "19/07/2026"),
        ("date", "Issued on 19-07-2026", "19-07-2026"),
        ("date", "Issued on 19.07.2026", "19.07.2026"),
        ("email", "Contact billing@acme.example.com please", "billing@acme.example.com"),
    ],
)
def test_builtin_matchers(
    matcher_name: str, sample: str, expected: str
) -> None:
    assert DETERMINISTIC_MATCHERS[matcher_name](sample) == expected


def test_export_and_registry() -> None:
    import prismdoc

    assert prismdoc.HybridExtractStage is HybridExtractStage
    register_plugins()
    assert "extract.hybrid" in registry.get_keys()
    stage = registry.create(
        "extract.hybrid",
        schema=_invoice_schema(),
        field_rules={"total": {"matcher": "number"}},
    )
    assert isinstance(stage, HybridExtractStage)


def test_yaml_field_rules_via_build_pipeline() -> None:
    pipeline, _ = build_pipeline(
        {
            "schema": {
                "fields": [
                    {"name": "total", "type": "number"},
                    {"name": "vendor", "type": "string"},
                ]
            },
            "pipeline": [
                {
                    "extract.hybrid": {
                        "field_rules": {
                            "total": {"matcher": "number"},
                        }
                    }
                }
            ],
        }
    )
    stage = pipeline.stages[0]
    assert isinstance(stage, HybridExtractStage)
    assert stage.field_rules == {"total": {"matcher": "number"}}
    assert stage.schema.field_names() == ["total", "vendor"]
