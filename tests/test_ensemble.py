"""Tests for T-036 model ensemble + disagreement flags."""

from __future__ import annotations

import json

import pytest

from prismdoc import (
    Context,
    Document,
    EnsembleExtractStage,
    FieldSpec,
    LLMClient,
    Page,
    Source,
    TargetSchema,
    registry,
)
from prismdoc.stages.ensemble import register_plugins
from prismdoc.stages.extract import Completion


def _receipt_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="company", type="string", required=True),
            FieldSpec(name="total", type="number", required=True),
        ]
    )


class SpyLLMClient(LLMClient):
    """Returns a canned payload; records call count."""

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


def _record_json(company: str, total: float | int) -> str:
    return json.dumps({"records": [{"company": company, "total": total}]})


def test_ensemble_full_agreement_on_total() -> None:
    clients = [
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
    ]
    doc = Document(
        source=Source(path="/tmp/receipt.md"),
        pages=[Page(index=0, text="BOOK TAK total 105.00")],
    )
    result = EnsembleExtractStage(schema=_receipt_schema(), clients=clients).run(
        doc, Context()
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert record.fields["total"] == 105.0
    assert record.confidence["total"] == 1.0
    assert all(entry["field"] != "total" for entry in result.artifacts["disagreement"])


def test_ensemble_majority_on_company_flags_disagreement() -> None:
    clients = [
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
        SpyLLMClient(_record_json("OTHER CO", 105.0)),
    ]
    doc = Document(
        source=Source(path="/tmp/receipt.md"),
        pages=[Page(index=0, text="BOOK TAK total 105.00")],
    )
    result = EnsembleExtractStage(schema=_receipt_schema(), clients=clients).run(
        doc, Context()
    )

    record = result.records[0]
    assert record.fields["company"] == "BOOK TAK"
    assert record.confidence["company"] == pytest.approx(2 / 3)
    company_flags = [
        e for e in result.artifacts["disagreement"] if e["field"] == "company"
    ]
    assert len(company_flags) == 1
    assert company_flags[0]["values"] == ["BOOK TAK", "BOOK TAK", "OTHER CO"]
    assert company_flags[0]["agreement"] == pytest.approx(2 / 3)


def test_ensemble_formatting_only_counts_as_agreement() -> None:
    clients = [
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
        SpyLLMClient(_record_json("BOOK TA K", 105.0)),
        SpyLLMClient(_record_json("BOOK TAK", 105.0)),
    ]
    doc = Document(
        source=Source(path="/tmp/receipt.md"),
        pages=[Page(index=0, text="BOOK TAK total 105.00")],
    )
    result = EnsembleExtractStage(schema=_receipt_schema(), clients=clients).run(
        doc, Context()
    )

    record = result.records[0]
    assert record.confidence["company"] == 1.0
    assert all(entry["field"] != "company" for entry in result.artifacts["disagreement"])
    # Surface form from the first (majority) group — first client.
    assert record.fields["company"] == "BOOK TAK"


def test_ensemble_each_client_called_once() -> None:
    clients = [
        SpyLLMClient(_record_json("A", 1.0)),
        SpyLLMClient(_record_json("A", 1.0)),
        SpyLLMClient(_record_json("B", 1.0)),
    ]
    doc = Document(
        source=Source(path="/tmp/receipt.md"),
        pages=[Page(index=0, text="receipt")],
    )
    EnsembleExtractStage(schema=_receipt_schema(), clients=clients).run(doc, Context())

    assert [c.calls for c in clients] == [1, 1, 1]


def test_ensemble_requires_at_least_two_clients() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        EnsembleExtractStage(
            schema=_receipt_schema(),
            clients=[SpyLLMClient(_record_json("A", 1.0))],
        )


def test_ensemble_exports_and_registry() -> None:
    import prismdoc

    assert prismdoc.EnsembleExtractStage is EnsembleExtractStage

    register_plugins()
    assert "extract.ensemble" in registry.get_keys()
    stage = registry.create(
        "extract.ensemble",
        schema=_receipt_schema(),
        clients=[
            SpyLLMClient(_record_json("A", 1.0)),
            SpyLLMClient(_record_json("A", 1.0)),
        ],
    )
    assert isinstance(stage, EnsembleExtractStage)
