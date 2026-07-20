"""Tests for T-032 RepairStage (adaptive field retry)."""

from __future__ import annotations

import json

from prismdoc import (
    Context,
    Document,
    FieldSpec,
    LLMClient,
    Page,
    Record,
    RepairStage,
    Source,
    TargetSchema,
    registry,
)
from prismdoc.stages.extract import Completion
from prismdoc.stages.repair import register_plugins as register_repair


class FakeLLMClient(LLMClient):
    """Offline stand-in returning canned corrections (string or callable)."""

    def __init__(self, response: str | list[str]) -> None:
        self._responses = [response] if isinstance(response, str) else list(response)
        self.prompts: list[str] = []
        self.call_count = 0

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        self.prompts.append(prompt)
        self.call_count += 1
        idx = min(self.call_count - 1, len(self._responses) - 1)
        return Completion(text=self._responses[idx])


def _schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(
                name="name",
                type="string",
                description="Product name",
                required=True,
            ),
            FieldSpec(
                name="sku",
                type="string",
                description="Stock keeping unit",
                required=True,
            ),
            FieldSpec(
                name="price",
                type="number",
                description="Unit price",
                required=True,
            ),
            FieldSpec(
                name="qty",
                type="integer",
                description="Quantity",
                required=False,
            ),
        ]
    )


def _doc(
    fields: dict,
    *,
    text: str = "Catalog: Widget sku W-1 price 12.5",
    low_confidence: list[dict] | None = None,
) -> Document:
    artifacts: dict = {"parsed_markdown": text}
    if low_confidence is not None:
        artifacts["low_confidence"] = low_confidence
    return Document(
        source=Source(path="/tmp/x.md"),
        pages=[Page(index=0, text=text)],
        records=[Record(fields=dict(fields))],
        artifacts=artifacts,
    )


def test_repair_fills_only_missing_required_field() -> None:
    doc = _doc({"name": "Widget", "sku": "W-1", "price": None, "qty": 3})
    client = FakeLLMClient(json.dumps({"price": 12.5}))
    result = RepairStage(schema=_schema(), client=client).run(doc, Context())

    assert result.records[0].fields == {
        "name": "Widget",
        "sku": "W-1",
        "price": 12.5,
        "qty": 3,
    }
    assert client.call_count == 1


def test_repair_overwrites_low_confidence_field_only() -> None:
    doc = _doc(
        {"name": "Widget", "sku": "W-1", "price": 99.0, "qty": 3},
        low_confidence=[{"record": 0, "field": "price", "confidence": 0.3}],
    )
    client = FakeLLMClient(json.dumps({"price": 12.5, "name": "IGNORE"}))
    result = RepairStage(schema=_schema(), client=client).run(doc, Context())

    assert result.records[0].fields["price"] == 12.5
    assert result.records[0].fields["name"] == "Widget"
    assert result.records[0].fields["sku"] == "W-1"
    assert result.records[0].fields["qty"] == 3
    assert client.call_count == 1


def test_repair_skips_when_no_failed_fields() -> None:
    doc = _doc({"name": "Widget", "sku": "W-1", "price": 12.5, "qty": 3})
    client = FakeLLMClient(json.dumps({"price": 0}))
    original = dict(doc.records[0].fields)
    result = RepairStage(schema=_schema(), client=client).run(doc, Context())

    assert client.call_count == 0
    assert result.records[0].fields == original
    assert result.artifacts["repair"] == []


def test_max_rounds_bounds_the_loop() -> None:
    doc = _doc({"name": "Widget", "sku": "W-1", "price": None, "qty": 3})
    # Always return empty price — field stays missing after each round.
    client = FakeLLMClient(json.dumps({"other": 1}))
    result = RepairStage(schema=_schema(), client=client, max_rounds=2).run(
        doc, Context()
    )

    assert client.call_count == 2
    assert result.records[0].fields["price"] is None
    assert len(result.artifacts["repair"]) == 2


def test_repaired_low_confidence_field_not_re_repaired_next_round() -> None:
    """Regression: the low_confidence artifact is a pre-repair snapshot and is
    never recomputed. A low-confidence field repaired in round 1 must NOT be
    re-selected via that stale list in round 2 (which would burn a second LLM
    call on an already-fixed field and never let the loop terminate early)."""
    doc = _doc(
        {"name": "Widget", "sku": "W-1", "price": 99.0, "qty": 3},
        low_confidence=[{"record": 0, "field": "price", "confidence": 0.3}],
    )
    client = FakeLLMClient(json.dumps({"price": 12.5}))
    result = RepairStage(schema=_schema(), client=client, max_rounds=3).run(
        doc, Context()
    )

    assert result.records[0].fields["price"] == 12.5
    # One round only: round 2 sees price is repaired (excluded from the stale
    # low_confidence list) and no field is missing, so the loop breaks.
    assert client.call_count == 1
    assert result.artifacts["repair"] == [
        {"record": 0, "round": 1, "fields": ["price"]}
    ]


def test_artifacts_repair_records_fields() -> None:
    doc = _doc({"name": "Widget", "sku": "", "price": 12.5, "qty": 3})
    client = FakeLLMClient(json.dumps({"sku": "W-1"}))
    result = RepairStage(schema=_schema(), client=client).run(doc, Context())

    assert result.artifacts["repair"] == [
        {"record": 0, "round": 1, "fields": ["sku"]}
    ]


def test_targeted_prompt_contains_failed_field_names() -> None:
    doc = _doc({"name": "Widget", "sku": None, "price": 12.5, "qty": 3})
    client = FakeLLMClient(json.dumps({"sku": "W-1"}))
    RepairStage(schema=_schema(), client=client).run(doc, Context())

    assert client.prompts
    prompt = client.prompts[0]
    assert "sku" in prompt
    assert "Stock keeping unit" in prompt
    # Other non-failed fields should not be requested as repair targets.
    assert "ONLY these fields: sku" in prompt or "ONLY these fields: sku." in prompt


def test_repair_stage_registered_and_importable() -> None:
    register_repair()
    assert registry.get_factory("repair.default") is RepairStage
    from prismdoc import RepairStage as Exported

    assert Exported is RepairStage


def _invoice_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(
                name="vendor",
                type="string",
                description="Vendor name",
                required=True,
            ),
            FieldSpec(
                name="total",
                type="number",
                description="Invoice total",
                required=True,
            ),
        ]
    )


def _confident_doc(
    fields: dict,
    *,
    field_verification: dict[str, str] | None = None,
    field_column_verification: dict[str, str] | None = None,
    text: str = "Invoice: Vendor Acme total 120.00 net 100.00",
) -> Document:
    record = Record(fields=dict(fields))
    if field_verification is not None:
        record.field_verification = dict(field_verification)
    if field_column_verification is not None:
        record.field_column_verification = dict(field_column_verification)
    return Document(
        source=Source(path="/tmp/x.md"),
        pages=[Page(index=0, text=text)],
        records=[record],
        artifacts={"parsed_markdown": text},
    )


def test_column_mismatch_triggers_repair() -> None:
    doc = _confident_doc(
        {"vendor": "Acme", "total": 100.0},
        field_column_verification={"total": "column_mismatch"},
    )
    client = FakeLLMClient(json.dumps({"total": 120.0}))
    result = RepairStage(schema=_invoice_schema(), client=client).run(
        doc, Context()
    )

    assert client.call_count == 1
    assert result.records[0].fields["total"] == 120.0
    assert result.artifacts["repair"] == [
        {"record": 0, "round": 1, "fields": ["total"]}
    ]


def test_label_mismatch_triggers_repair() -> None:
    doc = _confident_doc(
        {"vendor": "Acme", "total": 100.0},
        field_verification={"total": "label_mismatch"},
    )
    client = FakeLLMClient(json.dumps({"total": 120.0}))
    result = RepairStage(schema=_invoice_schema(), client=client).run(
        doc, Context()
    )

    assert client.call_count == 1
    assert result.records[0].fields["total"] == 120.0
    assert result.artifacts["repair"] == [
        {"record": 0, "round": 1, "fields": ["total"]}
    ]


def test_verified_status_does_not_trigger_repair() -> None:
    doc = _confident_doc(
        {"vendor": "Acme", "total": 120.0},
        field_column_verification={"total": "column_verified"},
    )
    client = FakeLLMClient(json.dumps({"total": 0}))
    original = dict(doc.records[0].fields)
    result = RepairStage(schema=_invoice_schema(), client=client).run(
        doc, Context()
    )

    assert client.call_count == 0
    assert result.records[0].fields == original
    assert result.artifacts["repair"] == []


def test_mismatch_repair_prompt_contains_hint() -> None:
    doc = _confident_doc(
        {"vendor": "Acme", "total": 100.0},
        field_column_verification={"total": "column_mismatch"},
    )
    client = FakeLLMClient(json.dumps({"total": 120.0}))
    RepairStage(schema=_invoice_schema(), client=client).run(doc, Context())

    assert client.prompts
    prompt = client.prompts[0]
    # Hint is attached to the total field line.
    total_line = next(
        line for line in prompt.splitlines() if line.startswith("- total ")
    )
    assert "wrong" in total_line
    assert "column" in total_line


def test_mismatch_repaired_once_not_reselected_in_later_rounds() -> None:
    """Verification dicts are a pre-repair snapshot and are never recomputed.
    A column_mismatch field repaired in round 1 must NOT be re-selected in
    later rounds (would burn extra LLM calls on an already-fixed field)."""
    doc = _confident_doc(
        {"vendor": "Acme", "total": 100.0},
        field_column_verification={"total": "column_mismatch"},
    )
    client = FakeLLMClient(json.dumps({"total": 120.0}))
    result = RepairStage(
        schema=_invoice_schema(), client=client, max_rounds=3
    ).run(doc, Context())

    assert result.records[0].fields["total"] == 120.0
    assert client.call_count == 1
    assert result.artifacts["repair"] == [
        {"record": 0, "round": 1, "fields": ["total"]}
    ]
