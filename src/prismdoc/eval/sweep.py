"""Threshold sweep: accuracy vs cost frontier for cascade configs."""

from __future__ import annotations

import argparse
import copy
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, TextIO

import yaml

from prismdoc.config import build_pipeline
from prismdoc.eval.dataset import EvalCase, load_dataset
from prismdoc.eval.runner import (
    CaseResult,
    _aggregate,
    _inject_client,
    run_case,
)
from prismdoc.stages.extract import LLMClient


@dataclass(frozen=True)
class SweepPoint:
    """One point on the accuracy/cost frontier for a cascade threshold."""

    threshold: float
    accuracy: float
    total_usd: float
    escalations: int


def sweep_threshold(
    config: dict[str, Any],
    cases: list[EvalCase],
    thresholds: list[float],
    client: LLMClient | None = None,
) -> list[SweepPoint]:
    """Run the dataset at each cascade threshold and collect frontier points.

    Requires ``config`` to contain a ``cascade`` pipeline item; raises
    ``ValueError`` otherwise.
    """
    if not _config_has_cascade(config):
        raise ValueError(
            "sweep_threshold requires a pipeline config with a 'cascade' stage"
        )

    points: list[SweepPoint] = []
    for threshold in thresholds:
        cfg = copy.deepcopy(config)
        _set_cascade_threshold(cfg, threshold)
        pipeline, ctx = build_pipeline(cfg)
        if client is not None:
            _inject_client(pipeline.stages, client)

        schema = ctx.target_schema
        case_results: list[CaseResult] = []
        for case in cases:
            case_results.append(run_case(pipeline, ctx, case, schema))

        report = _aggregate(case_results, schema)
        points.append(
            SweepPoint(
                threshold=float(threshold),
                accuracy=float(report.overall_field_accuracy),
                total_usd=float(report.total_usd),
                escalations=int(report.escalation_count),
            )
        )
    return points


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: sweep cascade thresholds and write a CSV frontier."""
    parser = argparse.ArgumentParser(
        prog="prismdoc-sweep",
        description=(
            "Sweep cascade thresholds and write an accuracy vs USD frontier CSV."
        ),
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to an eval dataset JSON file",
    )
    parser.add_argument(
        "--thresholds",
        required=True,
        help="Comma-separated cascade thresholds (e.g. 0,10,20,50,100)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output CSV path (threshold,accuracy,total_usd,escalations)",
    )
    parser.add_argument(
        "--plot",
        default=None,
        help="Optional PNG path (requires matplotlib / prismdoc[viz])",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    thresholds = _parse_thresholds(args.thresholds)
    dataset = load_dataset(Path(args.dataset))
    config = _load_config(dataset.config_path)
    points = sweep_threshold(config, list(dataset.cases), thresholds)

    out_path = Path(args.out)
    _write_csv(out_path, points)
    _print_table(points, file=sys.stdout)

    if args.plot:
        _maybe_plot(Path(args.plot), points, file=sys.stdout)

    return 0


def _config_has_cascade(config: dict[str, Any]) -> bool:
    pipeline = config.get("pipeline")
    if not isinstance(pipeline, list):
        return False
    for item in pipeline:
        if isinstance(item, dict) and "cascade" in item:
            return True
        if item == "cascade":
            return True
    return False


def _set_cascade_threshold(config: dict[str, Any], threshold: float) -> None:
    """Mutate the first cascade stage's threshold in ``config``."""
    pipeline = config.get("pipeline")
    if not isinstance(pipeline, list):
        raise ValueError(
            "sweep_threshold requires a pipeline config with a 'cascade' stage"
        )
    for item in pipeline:
        if not isinstance(item, dict) or "cascade" not in item:
            continue
        params = item["cascade"]
        if not isinstance(params, dict):
            raise ValueError(
                "cascade pipeline item must map to a parameter object"
            )
        params["threshold"] = threshold
        return
    raise ValueError(
        "sweep_threshold requires a pipeline config with a 'cascade' stage"
    )


def _load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(
            f"Pipeline config must be a mapping, got {type(data).__name__}"
        )
    return data


def _parse_thresholds(raw: str) -> list[float]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise SystemExit("error: --thresholds must list at least one number")
    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise SystemExit(
            f"error: --thresholds must be comma-separated numbers: {raw!r}"
        ) from exc


def _write_csv(path: Path, points: list[SweepPoint]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["threshold", "accuracy", "total_usd", "escalations"])
        for point in points:
            writer.writerow(
                [point.threshold, point.accuracy, point.total_usd, point.escalations]
            )


def _print_table(points: list[SweepPoint], *, file: TextIO) -> None:
    headers = ("threshold", "accuracy", "total_usd", "escalations")
    rows = [
        (
            f"{point.threshold:g}",
            f"{point.accuracy:.4f}",
            f"{point.total_usd:.6f}",
            str(point.escalations),
        )
        for point in points
    ]
    widths = [
        max(len(headers[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(len(headers))
    ]
    print(
        "  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))),
        file=file,
    )
    print(
        "  ".join("-" * widths[i] for i in range(len(headers))),
        file=file,
    )
    for row in rows:
        print(
            "  ".join(row[i].ljust(widths[i]) for i in range(len(headers))),
            file=file,
        )


def _maybe_plot(
    path: Path,
    points: list[SweepPoint],
    *,
    file: TextIO,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "note: matplotlib is not installed; skipping --plot "
            "(install with: pip install 'prismdoc[viz]')",
            file=file,
        )
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    xs = [point.total_usd for point in points]
    ys = [point.accuracy for point in points]
    fig, ax = plt.subplots()
    ax.plot(xs, ys, marker="o")
    for point in points:
        ax.annotate(
            f"{point.threshold:g}",
            (point.total_usd, point.accuracy),
            textcoords="offset points",
            xytext=(5, 5),
        )
    ax.set_xlabel("total_usd")
    ax.set_ylabel("accuracy")
    ax.set_title("Cascade threshold sweep (accuracy vs USD)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
