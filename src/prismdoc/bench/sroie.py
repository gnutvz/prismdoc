"""CLI: ``python -m prismdoc.bench.sroie --manifest <path> [--limit N]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from prismdoc.bench.dataset import load_manifest
from prismdoc.bench.runner import BenchReport, run_ocr_recall

_DOCLING_EXTRA_HINT = (
    "Docling is required for the live SROIE OCR-recall run. "
    "Install the 'docling' extra: pip install prismdoc[docling]"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Load a manifest, run OCR-recall with DoclingParser, print a table."""
    parser = argparse.ArgumentParser(
        prog="prismdoc-bench",
        description=(
            "Offline OCR-recall benchmark: measure whether parse/OCR text "
            "contains ground-truth receipt field values."
        ),
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to a bench manifest JSON (list of {image, fields})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of samples to run",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        import docling  # noqa: F401
    except ImportError:
        print(_DOCLING_EXTRA_HINT, file=sys.stderr)
        return 1

    from prismdoc.stages.parse import DoclingParser

    samples = load_manifest(Path(args.manifest))
    if args.limit is not None:
        if args.limit < 0:
            print("--limit must be >= 0", file=sys.stderr)
            return 1
        samples = samples[: args.limit]

    report = run_ocr_recall(samples, DoclingParser())
    _print_report(report, file=sys.stdout)
    return 0


def _format_token(value: float | None, *, width: int) -> str:
    """Format a token-recall cell; ``None`` renders as an em dash."""
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>{width}.4f}"


def _print_report(report: BenchReport, *, file: TextIO) -> None:
    print(f"n_samples: {report.n_samples}", file=file)
    print(f"overall_exact: {report.overall_exact:.4f}", file=file)
    overall_token_s = (
        "—"
        if report.overall_token is None
        else f"{report.overall_token:.4f}"
    )
    print(f"overall_token: {overall_token_s}", file=file)
    print(file=file)

    if not report.per_field:
        print("(no fields)", file=file)
        return

    name_w = max(len("field"), max(len(n) for n in report.per_field))
    exact_w = len("exact")
    token_w = len("token")
    print(
        f"{'field'.ljust(name_w)}  "
        f"{'exact'.rjust(exact_w)}  "
        f"{'token'.rjust(token_w)}",
        file=file,
    )
    print(
        f"{'-' * name_w}  {'-' * exact_w}  {'-' * token_w}",
        file=file,
    )
    for name, metrics in report.per_field.items():
        print(
            f"{name.ljust(name_w)}  "
            f"{metrics.exact_recall:>{exact_w}.4f}  "
            f"{_format_token(metrics.token_recall, width=token_w)}",
            file=file,
        )


if __name__ == "__main__":
    raise SystemExit(main())
