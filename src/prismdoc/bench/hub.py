"""Benchmark hub: one YAML declares schema + pipeline + cases; run via eval."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

from prismdoc.config import build_pipeline
from prismdoc.eval.__main__ import _print_report
from prismdoc.eval.dataset import EvalCase
from prismdoc.eval.runner import (
    EvalReport,
    _aggregate,
    _inject_client,
    _swap_parser,
    run_case,
)
from prismdoc.stages.extract import LiteLLMClient

_REQUIRED_TOP_KEYS = ("name", "schema", "pipeline", "cases")


def load_spec(path: str | Path) -> dict[str, Any]:
    """Read a YAML benchmark spec and return the dict.

    Required top-level keys: ``name``, ``schema`` (with ``fields``),
    ``pipeline``, ``cases``. Each case must have ``input`` and ``expected``.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"Benchmark spec must be a mapping, got {type(data).__name__}"
        )

    for key in _REQUIRED_TOP_KEYS:
        if key not in data:
            raise ValueError(f"Benchmark spec missing required key {key!r}")

    schema = data["schema"]
    if not isinstance(schema, dict) or "fields" not in schema:
        raise ValueError(
            "Benchmark spec 'schema' must be a mapping with a 'fields' key"
        )

    cases = data["cases"]
    if not isinstance(cases, list):
        raise ValueError(
            f"Benchmark spec 'cases' must be a list, got {type(cases).__name__}"
        )
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(
                f"Benchmark spec cases[{index}] must be a mapping, "
                f"got {type(case).__name__}"
            )
        if "input" not in case:
            raise ValueError(
                f"Benchmark spec cases[{index}] missing required key 'input'"
            )
        if "expected" not in case:
            raise ValueError(
                f"Benchmark spec cases[{index}] missing required key 'expected'"
            )

    return data


def run_benchmark(
    spec: dict[str, Any],
    *,
    model: str | None = None,
    parser: str | None = None,
) -> EvalReport:
    """Build the pipeline from ``spec``, run every case, return an ``EvalReport``."""
    pipeline, ctx = build_pipeline(
        {"schema": spec["schema"], "pipeline": spec["pipeline"]}
    )
    if parser is not None:
        _swap_parser(pipeline.stages, parser)
    if model is not None:
        _inject_client(pipeline.stages, LiteLLMClient(model=model))

    schema = ctx.target_schema
    cases = [
        EvalCase(
            input_path=c["input"],
            expected=c["expected"],
            key_field=c.get("key_field"),
        )
        for c in spec["cases"]
    ]
    results = [run_case(pipeline, ctx, case, schema) for case in cases]
    return _aggregate(results, schema)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``prismdoc-bench-hub --spec PATH [--model M] [--parser P]``."""
    arg_parser = argparse.ArgumentParser(
        prog="prismdoc-bench-hub",
        description=(
            "Run a self-contained benchmark hub YAML (schema + pipeline + cases) "
            "and print the full evaluator report."
        ),
    )
    arg_parser.add_argument(
        "--spec",
        required=True,
        help="Path to a benchmark hub YAML spec",
    )
    arg_parser.add_argument(
        "--model",
        default=None,
        help="Override extract model (injects LiteLLMClient with this model)",
    )
    arg_parser.add_argument(
        "--parser",
        default=None,
        help="Override parse stage (registry key parse.<NAME>)",
    )
    args = arg_parser.parse_args(list(argv) if argv is not None else None)

    spec = load_spec(args.spec)
    report = run_benchmark(spec, model=args.model, parser=args.parser)
    _print_report(report, file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
