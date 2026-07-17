"""Tests for T-013 cost ledger (token/$ accounting + optional budget)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismdoc import (
    BudgetExceededError,
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    LLMClient,
    Page,
    Source,
    TargetSchema,
    estimate_cost,
    record_cost,
)
from prismdoc.cost import PRICING, check_budget
from prismdoc.stages.extract import Completion
from prismdoc.eval.dataset import load_dataset
from prismdoc.eval.runner import run_eval


def _product_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
            FieldSpec(name="price", type="number", required=True),
        ]
    )


_CANNED = [{"name": "Widget", "sku": "W-1", "price": 9.99}]


class UsageLLMClient(LLMClient):
    """Mock client that reports token usage after complete."""

    def __init__(
        self,
        response: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        model: str | None = None,
    ) -> None:
        self.response = response
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        if model is not None:
            self.model = model

    def complete(self, prompt: str) -> Completion:
        return Completion(
            text=self.response,
            usage={
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            },
            model=getattr(self, "model", None),
        )


def test_estimate_cost_known_model() -> None:
    tokens_in, tokens_out = 1000, 500
    per_in, per_out = PRICING["gpt-4o-mini"]
    expected = (tokens_in / 1000.0) * per_in + (tokens_out / 1000.0) * per_out

    assert estimate_cost("gpt-4o-mini", tokens_in, tokens_out) == pytest.approx(
        expected
    )


def test_estimate_cost_bedrock_prefix_normalizes() -> None:
    tokens_in, tokens_out = 2000, 1000
    direct = estimate_cost("anthropic.claude-3-5-sonnet", tokens_in, tokens_out)
    prefixed = estimate_cost(
        "bedrock/anthropic.claude-3-5-sonnet", tokens_in, tokens_out
    )
    assert prefixed == pytest.approx(direct)


def test_estimate_cost_unknown_model_uses_default() -> None:
    tokens_in, tokens_out = 1000, 1000
    expected = estimate_cost("default", tokens_in, tokens_out)
    assert estimate_cost("totally-unknown-model-xyz", tokens_in, tokens_out) == (
        pytest.approx(expected)
    )


def test_extract_stage_records_cost_from_completion_usage() -> None:
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        pages=[Page(index=0, text="Widget W-1 9.99")],
    )
    tokens_in, tokens_out = 1000, 200
    client = UsageLLMClient(
        json.dumps(_CANNED),
        prompt_tokens=tokens_in,
        completion_tokens=tokens_out,
        model="gpt-4o-mini",
    )
    result = ExtractStage(
        schema=_product_schema(),
        client=client,
        model="gpt-4o-mini",
    ).run(doc, Context())

    cost = result.artifacts["cost"]
    expected_usd = estimate_cost("gpt-4o-mini", tokens_in, tokens_out)
    assert cost["total_usd"] == pytest.approx(expected_usd)
    assert cost["tokens_in"] == tokens_in
    assert cost["tokens_out"] == tokens_out
    assert cost["by_stage"]["extract"]["usd"] == pytest.approx(expected_usd)
    assert cost["by_stage"]["extract"]["tokens_in"] == tokens_in
    assert cost["by_stage"]["extract"]["tokens_out"] == tokens_out
    assert cost["by_stage"]["extract"]["model"] == "gpt-4o-mini"


def test_record_cost_accumulates_same_stage_twice() -> None:
    doc = Document(source=Source(path="/tmp/x.md"))
    record_cost(doc, "extract", "gpt-4o-mini", 1000, 100)
    record_cost(doc, "extract", "gpt-4o-mini", 500, 50)

    cost = doc.artifacts["cost"]
    expected = estimate_cost("gpt-4o-mini", 1500, 150)
    assert cost["tokens_in"] == 1500
    assert cost["tokens_out"] == 150
    assert cost["total_usd"] == pytest.approx(expected)
    assert cost["by_stage"]["extract"]["tokens_in"] == 1500
    assert cost["by_stage"]["extract"]["tokens_out"] == 150
    assert cost["by_stage"]["extract"]["usd"] == pytest.approx(expected)


def test_check_budget_raises_when_exceeded() -> None:
    doc = Document(source=Source(path="/tmp/x.md"))
    record_cost(doc, "extract", "gpt-4o", 100_000, 50_000)
    total = float(doc.artifacts["cost"]["total_usd"])
    assert total > 0.01

    with pytest.raises(BudgetExceededError):
        check_budget(doc, 0.01)


def test_extract_stage_surfaces_budget_exceeded() -> None:
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        pages=[Page(index=0, text="Widget W-1 9.99")],
    )
    client = UsageLLMClient(
        json.dumps(_CANNED),
        prompt_tokens=100_000,
        completion_tokens=50_000,
        model="gpt-4o",
    )
    stage = ExtractStage(schema=_product_schema(), client=client, model="gpt-4o")
    ctx = Context(options={"budget_usd": 0.01})

    with pytest.raises(BudgetExceededError):
        stage.run(doc, ctx)


def test_extract_without_usage_records_no_cost() -> None:
    class NoUsageClient(LLMClient):
        def complete(self, prompt: str) -> Completion:
            return Completion(text=json.dumps(_CANNED))

    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        pages=[Page(index=0, text="Widget")],
    )
    result = ExtractStage(
        schema=_product_schema(),
        client=NoUsageClient(),
    ).run(doc, Context())

    assert "cost" not in result.artifacts


def test_cost_symbols_exported_from_prismdoc() -> None:
    import prismdoc

    assert callable(prismdoc.estimate_cost)
    assert callable(prismdoc.record_cost)
    assert issubclass(prismdoc.BudgetExceededError, Exception)


def test_eval_report_total_usd_zero_for_offline_table(tmp_path: Path) -> None:
    import importlib.util
    import sys

    repo = Path(__file__).resolve().parents[1]
    make_sample = repo / "examples" / "retail" / "make_sample.py"
    retail_dataset = repo / "examples" / "eval" / "retail_dataset.json"
    demo_yaml = repo / "examples" / "retail" / "demo.yaml"

    spec = importlib.util.spec_from_file_location("retail_make_sample", make_sample)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    xlsx = module.write_sample_catalog(tmp_path / "sample_catalog.xlsx")
    raw = json.loads(retail_dataset.read_text(encoding="utf-8"))
    raw["config_path"] = str(demo_yaml)
    raw["cases"][0]["input_path"] = str(xlsx)
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(raw), encoding="utf-8")

    report = run_eval(load_dataset(dataset_path))

    assert report.total_usd == 0.0
    assert report.case_results[0].cost is None
