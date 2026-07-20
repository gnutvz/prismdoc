"""Tests for T-012 eval harness (per-field accuracy vs ground truth)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from prismdoc.eval.dataset import EvalCase, EvalDataset, load_dataset
from prismdoc.eval.metrics import align_records, field_metrics
from prismdoc.eval.runner import CaseResult, _aggregate, run_case, run_eval
from prismdoc.models import Document
from prismdoc.pipeline import Pipeline
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import Completion, LLMClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RETAIL_DATASET = _REPO_ROOT / "examples" / "eval" / "retail_dataset.json"
_DEMO_YAML = _REPO_ROOT / "examples" / "retail" / "demo.yaml"
_MAKE_SAMPLE = _REPO_ROOT / "examples" / "retail" / "make_sample.py"
_SAMPLE_XLSX = _REPO_ROOT / "examples" / "retail" / "sample_catalog.xlsx"


def _tiny_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string"),
            FieldSpec(name="price", type="number"),
        ]
    )


def test_align_records_by_order_and_count_mismatch() -> None:
    predicted = [{"sku": "A"}, {"sku": "B"}]
    expected = [{"sku": "A"}]

    pairs = align_records(predicted, expected, key_field=None)

    assert pairs == [
        ({"sku": "A"}, {"sku": "A"}),
        ({"sku": "B"}, None),
    ]


def test_align_records_by_key_field() -> None:
    predicted = [{"sku": "B", "name": "Bee"}, {"sku": "A", "name": "Aye"}]
    expected = [{"sku": "A", "name": "Aye"}, {"sku": "C", "name": "Cee"}]

    pairs = align_records(predicted, expected, key_field="sku")

    by_key = {
        (exp or pred or {}).get("sku"): (pred, exp) for pred, exp in pairs
    }
    assert by_key["A"] == (
        {"sku": "A", "name": "Aye"},
        {"sku": "A", "name": "Aye"},
    )
    assert by_key["C"] == (None, {"sku": "C", "name": "Cee"})
    assert by_key["B"] == ({"sku": "B", "name": "Bee"}, None)


def test_field_metrics_one_wrong_field() -> None:
    schema = _tiny_schema()
    predicted = [
        {"name": "Widget", "sku": "W-1", "price": "9.99"},
        {"name": "Gadget", "sku": "G-1", "price": "1.00"},
    ]
    expected = [
        {"name": "Widget", "sku": "W-1", "price": "9.99"},
        {"name": "Gadget", "sku": "G-1", "price": "2.00"},  # wrong price
    ]
    pairs = align_records(predicted, expected, key_field=None)
    metrics = field_metrics(pairs, schema)

    # 2 records × 3 fields = 6; one price wrong → 5/6
    assert metrics["fields"]["name"]["correct"] == 2
    assert metrics["fields"]["name"]["total"] == 2
    assert metrics["fields"]["name"]["accuracy"] == 1.0
    assert metrics["fields"]["sku"]["accuracy"] == 1.0
    assert metrics["fields"]["price"]["correct"] == 1
    assert metrics["fields"]["price"]["total"] == 2
    assert metrics["fields"]["price"]["accuracy"] == 0.5
    assert metrics["overall_field_accuracy"] == pytest.approx(5 / 6)
    assert metrics["record_count_predicted"] == 2
    assert metrics["record_count_expected"] == 2
    assert metrics["exact_record_match_ratio"] == pytest.approx(0.5)


def test_run_eval_retail_table_pipeline_is_perfect(tmp_path: Path) -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("retail_make_sample", _MAKE_SAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    xlsx = module.write_sample_catalog(tmp_path / "sample_catalog.xlsx")
    raw = json.loads(_RETAIL_DATASET.read_text(encoding="utf-8"))
    raw["config_path"] = str(_DEMO_YAML)
    raw["cases"][0]["input_path"] = str(xlsx)
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(raw), encoding="utf-8")

    report = run_eval(load_dataset(dataset_path))

    assert report.case_count == 1
    assert report.overall_field_accuracy == 1.0
    assert all(acc == 1.0 for acc in report.per_field_accuracy.values())
    assert report.case_results[0].metrics["overall_field_accuracy"] == 1.0


def test_run_eval_mock_llm_wrong_field_matches_error(tmp_path: Path) -> None:
    schema = TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string"),
            FieldSpec(name="price", type="number"),
        ]
    )
    # One wrong field: price 9.99 vs expected 1.00 → 2/3 overall accuracy
    expected = [{"name": "Widget", "sku": "W-1", "price": 1.0}]
    predicted_payload = [{"name": "Widget", "sku": "W-1", "price": 9.99}]

    class FakeClient(LLMClient):
        def complete(
            self, prompt: str, *, response_format: dict | None = None
        ) -> Completion:
            return Completion(text=json.dumps(predicted_payload))

    config = {
        "schema": {
            "fields": [
                {"name": "name", "type": "string", "required": True},
                {"name": "sku", "type": "string"},
                {"name": "price", "type": "number"},
            ]
        },
        # Extract-only: FakeClient ignores prompt text (ingest has no .txt loader).
        "pipeline": [
            {"extract.default": {"model": "fake-model"}},
        ],
    }
    import yaml

    config_path = tmp_path / "mock_pipeline.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    dataset = EvalDataset(
        schema=schema,
        config_path=str(config_path),
        cases=[
            EvalCase(input_path="memory://mock", expected=expected),
        ],
    )
    report = run_eval(dataset, client=FakeClient())

    assert report.case_count == 1
    assert report.overall_field_accuracy == pytest.approx(2 / 3)
    assert report.per_field_accuracy["name"] == 1.0
    assert report.per_field_accuracy["sku"] == 1.0
    assert report.per_field_accuracy["price"] == 0.0


def test_eval_cli_help() -> None:
    from prismdoc.eval.__main__ import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_load_retail_dataset_file() -> None:
    dataset = load_dataset(_RETAIL_DATASET)
    assert dataset.config_path.endswith("demo.yaml")
    assert len(dataset.cases) == 1
    assert len(dataset.cases[0].expected) == 5
    assert _SAMPLE_XLSX.name in dataset.cases[0].input_path


class _ArtifactStage(Stage):
    """Test stage that optionally stamps a low_confidence artifact."""

    name = "artifact"

    def __init__(self, low_confidence: list[dict[str, Any]] | None = None) -> None:
        self.low_confidence = low_confidence

    def run(self, doc: Document, ctx: Context) -> Document:
        if self.low_confidence is not None:
            doc.artifacts["low_confidence"] = self.low_confidence
        return doc


def test_run_case_latency_and_review_flagged() -> None:
    schema = _tiny_schema()
    case = EvalCase(input_path="memory://a", expected=[])
    ctx = Context(target_schema=schema)

    flagged = run_case(
        Pipeline([_ArtifactStage([{"record": 0, "field": "name", "confidence": 0.1}])]),
        ctx,
        case,
        schema,
    )
    assert flagged.latency_ms >= 0.0
    assert flagged.review_flagged is True

    clean = run_case(Pipeline([_ArtifactStage()]), ctx, case, schema)
    assert clean.latency_ms >= 0.0
    assert clean.review_flagged is False

    empty_list = run_case(
        Pipeline([_ArtifactStage([])]),
        ctx,
        case,
        schema,
    )
    assert empty_list.review_flagged is False


def test_aggregate_latency_percentiles_and_review_rate() -> None:
    schema = _tiny_schema()
    metrics = {"overall_field_accuracy": 1.0, "fields": {}}
    results = [
        CaseResult(
            input_path="a",
            metrics=metrics,
            latency_ms=10.0,
            review_flagged=True,
        ),
        CaseResult(
            input_path="b",
            metrics=metrics,
            latency_ms=30.0,
            review_flagged=False,
        ),
    ]
    report = _aggregate(results, schema)
    assert report.latency_p50_ms >= 0.0
    assert report.latency_p95_ms >= 0.0
    assert report.latency_p50_ms == 10.0  # nearest-rank p50 of [10, 30]
    assert report.latency_p95_ms == 30.0  # nearest-rank p95 of [10, 30]
    assert report.review_rate == 0.5


def _write_mock_eval_dataset(
    tmp_path: Path,
    *,
    pipeline: list[Any],
    expected: list[dict[str, Any]] | None = None,
) -> Path:
    schema = {
        "fields": [
            {"name": "name", "type": "string", "required": True},
            {"name": "sku", "type": "string"},
            {"name": "price", "type": "number"},
        ]
    }
    config = {"schema": schema, "pipeline": pipeline}
    config_path = tmp_path / "mock_pipeline.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    if expected is None:
        expected = [{"name": "Widget", "sku": "W-1", "price": 9.99}]
    dataset = {
        "schema": schema,
        "config_path": str(config_path),
        "cases": [{"input_path": "memory://mock", "expected": expected}],
    }
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    return dataset_path


def test_eval_cli_parser_and_model_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismdoc.eval import __main__ as eval_main
    from prismdoc.eval import runner as runner_mod
    from prismdoc.eval.__main__ import main
    from prismdoc.stages.extract import LiteLLMClient

    predicted = [{"name": "Widget", "sku": "W-1", "price": 9.99}]

    class FakeClient(LLMClient):
        def complete(
            self, prompt: str, *, response_format: dict | None = None
        ) -> Completion:
            return Completion(text=json.dumps(predicted))

    dataset_path = _write_mock_eval_dataset(
        tmp_path,
        pipeline=[
            "parse.default",
            {"extract.default": {"model": "ignored"}},
        ],
    )

    injected: list[LLMClient] = []
    original_inject = runner_mod._inject_client

    def tracking_inject(stages: list[Stage], client: LLMClient) -> None:
        injected.append(client)
        # Keep LiteLLMClient for assertion, but inject FakeClient so we stay offline.
        original_inject(stages, FakeClient())

    monkeypatch.setattr(runner_mod, "_inject_client", tracking_inject)

    buf = io.StringIO()
    monkeypatch.setattr(eval_main.sys, "stdout", buf)

    code = main(
        ["--dataset", str(dataset_path), "--parser", "passthrough", "--model", "X"]
    )
    assert code == 0
    out = buf.getvalue()
    assert "latency_p50_ms:" in out
    assert "latency_p95_ms:" in out
    assert "review_rate:" in out
    assert "%" in out
    assert injected and isinstance(injected[0], LiteLLMClient)
    assert injected[0].model == "X"


def test_run_eval_model_and_parser_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismdoc.eval import runner as runner_mod
    from prismdoc.stages.parse import ParseStage, PassthroughParser

    predicted = [{"name": "Widget", "sku": "W-1", "price": 9.99}]

    class FakeClient(LLMClient):
        def complete(
            self, prompt: str, *, response_format: dict | None = None
        ) -> Completion:
            return Completion(text=json.dumps(predicted))

    dataset_path = _write_mock_eval_dataset(
        tmp_path,
        pipeline=[
            "parse.default",
            {"extract.default": {"model": "ignored"}},
        ],
    )

    swapped: list[Stage] = []
    original_swap = runner_mod._swap_parser

    def tracking_swap(stages: list[Stage], parser_name: str) -> None:
        original_swap(stages, parser_name)
        swapped.extend(s for s in stages if s.name == "parse")

    def fake_litellm_client(model: str = "gpt-4o-mini", **opts: Any) -> LLMClient:
        assert model == "eval-model"
        return FakeClient()

    monkeypatch.setattr(runner_mod, "_swap_parser", tracking_swap)
    monkeypatch.setattr(runner_mod, "LiteLLMClient", fake_litellm_client)

    report = run_eval(
        load_dataset(dataset_path),
        model="eval-model",
        parser="passthrough",
    )
    assert report.case_count == 1
    assert report.latency_p50_ms >= 0.0
    assert report.latency_p95_ms >= 0.0
    assert 0.0 <= report.review_rate <= 1.0
    assert swapped
    assert isinstance(swapped[0], ParseStage)
    assert isinstance(swapped[0].parser, PassthroughParser)
