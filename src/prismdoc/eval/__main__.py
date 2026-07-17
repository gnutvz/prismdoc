"""CLI entrypoint: ``python -m prismdoc.eval --dataset <path.json>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from prismdoc.eval.dataset import load_dataset
from prismdoc.eval.runner import EvalReport, run_eval


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, run eval, and print a readable accuracy table."""
    parser = argparse.ArgumentParser(
        prog="prismdoc-eval",
        description="Evaluate extraction quality against a ground-truth dataset.",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to an eval dataset JSON file",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    dataset_path = Path(args.dataset)
    dataset = load_dataset(dataset_path)
    report = run_eval(dataset)
    _print_report(report, file=sys.stdout)
    return 0


def _print_report(report: EvalReport, *, file: TextIO) -> None:
    print(f"cases: {report.case_count}", file=file)
    print(f"overall_field_accuracy: {report.overall_field_accuracy:.4f}", file=file)
    print(f"escalation_count: {report.escalation_count}", file=file)
    if report.total_usd or any(r.cost for r in report.case_results):
        print(f"total_usd: {report.total_usd:.6f}", file=file)
    print(file=file)

    if not report.per_field_accuracy:
        print("(no fields)", file=file)
        return

    name_w = max(len("field"), max(len(n) for n in report.per_field_accuracy))
    acc_w = len("accuracy")
    print(f"{'field'.ljust(name_w)}  {'accuracy'.rjust(acc_w)}", file=file)
    print(f"{'-' * name_w}  {'-' * acc_w}", file=file)
    for name, accuracy in report.per_field_accuracy.items():
        print(
            f"{name.ljust(name_w)}  {accuracy:>{acc_w}.4f}",
            file=file,
        )


if __name__ == "__main__":
    raise SystemExit(main())
