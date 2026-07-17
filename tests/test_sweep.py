"""Tests for T-020 type-aware metrics and threshold-sweep frontier."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from prismdoc.eval.dataset import EvalCase
from prismdoc.eval.metrics import align_records, field_metrics, values_match
from prismdoc.eval.sweep import SweepPoint, main as sweep_main, sweep_threshold
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.extract import Completion, LLMClient


def test_values_match_type_aware() -> None:
    assert values_match(12.5, "12.50", "number") is True
    assert values_match("USD", "usd", "string") is True
    assert values_match("USD", "usd ", "string") is True
    assert values_match("7", 7, "integer") is True
    assert values_match("yes", True, "boolean") is True
    assert values_match(12.5, "13.0", "number") is False
    assert values_match("USD", "EUR", "string") is False
    assert values_match("7", 8, "integer") is False
    assert values_match("yes", False, "boolean") is False


def test_values_match_string_alphanumeric_formatting() -> None:
    """Punctuation/spacing-only diffs match; real content diffs do not."""
    assert (
        values_match(
            "BOOK TA .K (TAMAN DAYA) SDN BHD",
            "BOOK TAK(TAMAN DAYA)SDN BHD",
            "string",
        )
        is True
    )
    assert values_match("Acme Corp", "Globex Ltd", "string") is False
    assert values_match("abc", "ab", "string") is False
    # Address: different street number digits must still fail.
    assert (
        values_match(
            "NO.53 55,57 & 59, JALAN SAGU 18",
            "NO.53 55,57 & 59, JALAN SAGU 19",
            "string",
        )
        is False
    )


def test_field_metrics_formatting_only_number_matches() -> None:
    """Formatting-only number diffs that fail raw str==str now match."""
    schema = TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="price", type="number"),
        ]
    )
    predicted = [{"name": "Widget", "price": 12.5}]
    expected = [{"name": "Widget", "price": "12.50"}]
    # Raw string compare would fail: "12.5" != "12.50"
    assert str(predicted[0]["price"]).strip() != str(expected[0]["price"]).strip()

    pairs = align_records(predicted, expected, key_field=None)
    metrics = field_metrics(pairs, schema)

    assert metrics["fields"]["price"]["correct"] == 1
    assert metrics["fields"]["price"]["accuracy"] == 1.0
    assert metrics["overall_field_accuracy"] == 1.0
    assert metrics["exact_record_match_ratio"] == 1.0


def _cascade_extract_config() -> dict:
    return {
        "schema": {
            "fields": [
                {"name": "name", "type": "string", "required": True},
                {"name": "price", "type": "number"},
            ]
        },
        "pipeline": [
            {
                "cascade": {
                    "primary": "extract.default",
                    "fallback": "extract.default",
                    "scorer": "required_fill_ratio",
                    "threshold": 0.5,
                }
            }
        ],
    }


class _FakeClient(LLMClient):
    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        return Completion(
            text=json.dumps([{"name": "Widget", "price": 9.99}]),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            model="fake-model",
        )


def test_sweep_threshold_returns_one_point_per_threshold() -> None:
    config = _cascade_extract_config()
    cases = [
        EvalCase(
            input_path="memory://mock",
            expected=[{"name": "Widget", "price": 9.99}],
        )
    ]
    thresholds = [0.0, 0.5, 2.0]
    points = sweep_threshold(config, cases, thresholds, client=_FakeClient())

    assert len(points) == 3
    assert all(isinstance(p, SweepPoint) for p in points)
    assert [p.threshold for p in points] == thresholds
    for point in points:
        assert 0.0 <= point.accuracy <= 1.0
        assert point.total_usd >= 0.0
        assert point.escalations >= 0
    # fill ratio is 1.0 → escalate only when threshold > 1.0
    assert points[0].escalations == 0
    assert points[1].escalations == 0
    assert points[2].escalations == 1


def test_sweep_threshold_requires_cascade() -> None:
    config = {
        "schema": {"fields": [{"name": "name", "type": "string"}]},
        "pipeline": ["extract.default"],
    }
    with pytest.raises(ValueError, match="cascade"):
        sweep_threshold(config, [], [0.5])


def test_sweep_cli_writes_csv(tmp_path: Path) -> None:
    # Offline cascade (no LLM): validate primary/fallback + required_fill_ratio.
    config = {
        "schema": {
            "fields": [{"name": "name", "type": "string", "required": True}]
        },
        "pipeline": [
            {
                "cascade": {
                    "primary": "validate.default",
                    "fallback": "validate.default",
                    "scorer": "required_fill_ratio",
                    "threshold": 0.5,
                }
            }
        ],
    }
    config_path = tmp_path / "validate_cascade.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema": config["schema"],
                "config_path": str(config_path),
                "cases": [
                    {
                        "input_path": "memory://mock",
                        "expected": [{"name": "Widget"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "frontier.csv"

    rc = sweep_main(
        [
            "--dataset",
            str(dataset_path),
            "--thresholds",
            "0,0.5,2",
            "--out",
            str(out_path),
        ]
    )
    assert rc == 0
    assert out_path.is_file()
    with out_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["threshold", "accuracy", "total_usd", "escalations"]
    assert len(rows) == 4  # header + 3 thresholds


def test_sweep_cli_help() -> None:
    with pytest.raises(SystemExit) as excinfo:
        sweep_main(["--help"])
    assert excinfo.value.code == 0
