"""Tests for T-012 eval harness (per-field accuracy vs ground truth)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismdoc.eval.dataset import EvalCase, EvalDataset, load_dataset
from prismdoc.eval.metrics import align_records, field_metrics
from prismdoc.eval.runner import run_eval
from prismdoc.schema import FieldSpec, TargetSchema
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
