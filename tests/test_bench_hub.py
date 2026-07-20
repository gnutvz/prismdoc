"""Tests for T-054 benchmark hub (self-contained YAML + CLI)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from prismdoc.bench.hub import load_spec, main, run_benchmark
from prismdoc.eval.runner import EvalReport

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_SPEC = _REPO_ROOT / "examples" / "bench" / "spec_smoke.yaml"
_SMOKE_INPUT = _REPO_ROOT / "examples" / "bench" / "sample_table.txt"


def _offline_spec_dict(input_path: Path) -> dict:
    return {
        "name": "offline-tiny",
        "schema": {
            "fields": [
                {"name": "name", "type": "string", "required": True},
                {"name": "sku", "type": "string"},
                {"name": "price", "type": "number"},
            ]
        },
        "pipeline": [
            "ingest.default",
            "parse.passthrough",
            "extract.table",
            "validate.default",
        ],
        "cases": [
            {
                "input": str(input_path),
                "expected": [
                    {"name": "Widget A", "sku": "W-1", "price": 9.99},
                    {"name": "Widget B", "sku": "W-2", "price": 1.50},
                ],
                "key_field": "sku",
            }
        ],
    }


def _write_offline_input(path: Path) -> Path:
    path.write_text(
        "name\tsku\tprice\nWidget A\tW-1\t9.99\nWidget B\tW-2\t1.50\n",
        encoding="utf-8",
    )
    return path


def test_load_spec_parses_valid_yaml(tmp_path: Path) -> None:
    input_path = _write_offline_input(tmp_path / "table.txt")
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(_offline_spec_dict(input_path)),
        encoding="utf-8",
    )

    loaded = load_spec(spec_path)

    assert loaded["name"] == "offline-tiny"
    assert "fields" in loaded["schema"]
    assert isinstance(loaded["pipeline"], list)
    assert len(loaded["cases"]) == 1
    assert loaded["cases"][0]["input"] == str(input_path)


def test_load_spec_missing_pipeline_raises(tmp_path: Path) -> None:
    bad = {
        "name": "no-pipeline",
        "schema": {"fields": [{"name": "sku", "type": "string"}]},
        "cases": [{"input": "x.txt", "expected": []}],
    }
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")

    with pytest.raises(ValueError, match="pipeline"):
        load_spec(path)


def test_load_spec_case_missing_input_raises(tmp_path: Path) -> None:
    bad = {
        "name": "no-input",
        "schema": {"fields": [{"name": "sku", "type": "string"}]},
        "pipeline": ["ingest.default"],
        "cases": [{"expected": [{"sku": "A"}]}],
    }
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")

    with pytest.raises(ValueError, match="input"):
        load_spec(path)


def test_run_benchmark_offline_returns_eval_report(tmp_path: Path) -> None:
    input_path = _write_offline_input(tmp_path / "table.txt")
    report = run_benchmark(_offline_spec_dict(input_path))

    assert isinstance(report, EvalReport)
    assert report.case_count >= 1
    assert isinstance(report.overall_field_accuracy, float)
    assert report.overall_field_accuracy == 1.0
    assert hasattr(report, "latency_p50_ms")
    assert hasattr(report, "review_rate")
    assert report.latency_p50_ms >= 0.0
    assert 0.0 <= report.review_rate <= 1.0


def test_run_benchmark_parser_override(tmp_path: Path) -> None:
    input_path = _write_offline_input(tmp_path / "table.txt")
    report = run_benchmark(_offline_spec_dict(input_path), parser="passthrough")

    assert report.case_count >= 1
    assert report.overall_field_accuracy == 1.0


def test_cli_main_prints_accuracy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = _write_offline_input(tmp_path / "table.txt")
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(_offline_spec_dict(input_path)),
        encoding="utf-8",
    )

    code = main(["--spec", str(spec_path)])
    captured = capsys.readouterr()

    assert code == 0
    assert "accuracy" in captured.out.lower()


def test_example_spec_smoke_runs_end_to_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _SMOKE_SPEC.is_file()
    assert _SMOKE_INPUT.is_file()

    # Case paths in the example are repo-root relative.
    monkeypatch.chdir(_REPO_ROOT)

    spec = load_spec(_SMOKE_SPEC)
    report = run_benchmark(spec)

    assert report.case_count == 1
    assert report.overall_field_accuracy == 1.0
    assert report.latency_p50_ms >= 0.0
    assert 0.0 <= report.review_rate <= 1.0

    code = main(["--spec", str(_SMOKE_SPEC)])
    captured = capsys.readouterr()
    assert code == 0
    assert "accuracy" in captured.out.lower()
