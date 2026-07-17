"""Command-line entrypoint: run one document through a YAML pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from prismdoc.config import load_pipeline
from prismdoc.models import Document, Source
from prismdoc.schema import TargetSchema


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, run the pipeline, print a summary, optionally write outputs."""
    parser = argparse.ArgumentParser(
        prog="prismdoc",
        description="Run a document through a prismdoc pipeline.",
    )
    parser.add_argument(
        "--config",
        default="examples/retail/pipeline.yaml",
        help="Path to pipeline YAML (default: examples/retail/pipeline.yaml)",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input document",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional JSON output path",
    )
    parser.add_argument(
        "--csv",
        default=None,
        dest="csv_path",
        help="Optional CSV output path",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    pipeline, ctx = load_pipeline(args.config)
    doc = Document(source=Source(path=str(Path(args.input))))
    doc = pipeline.run(doc, ctx)

    _print_summary(doc, file=sys.stdout)
    _print_records_table(doc, ctx.target_schema, file=sys.stdout)

    if args.out is not None:
        _write_json(doc, Path(args.out))
    if args.csv_path is not None:
        _write_csv(doc, ctx.target_schema, Path(args.csv_path))

    return 0


def _print_summary(doc: Document, *, file: TextIO) -> None:
    validation = doc.artifacts.get("validation")
    print(f"records: {len(doc.records)}", file=file)
    if isinstance(validation, dict):
        print(
            "validation: "
            f"valid={validation.get('valid', 0)} "
            f"invalid={validation.get('invalid', 0)} "
            f"errors={len(validation.get('errors') or [])}",
            file=file,
        )
    else:
        print("validation: (none)", file=file)


def _print_records_table(
    doc: Document,
    schema: TargetSchema | None,
    *,
    file: TextIO,
) -> None:
    columns = _column_names(doc, schema)
    if not columns:
        print("(no records)", file=file)
        return

    widths = [len(col) for col in columns]
    rows: list[list[str]] = []
    for record in doc.records:
        cells = [_cell_str(record.fields.get(col)) for col in columns]
        rows.append(cells)
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    header = " | ".join(col.ljust(widths[i]) for i, col in enumerate(columns))
    sep = "-+-".join("-" * w for w in widths)
    print(header, file=file)
    print(sep, file=file)
    for row in rows:
        print(
            " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)),
            file=file,
        )


def _column_names(doc: Document, schema: TargetSchema | None) -> list[str]:
    if schema is not None and schema.fields:
        return schema.field_names()
    seen: list[str] = []
    for record in doc.records:
        for key in record.fields:
            if key not in seen:
                seen.append(key)
    return seen


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _write_json(doc: Document, path: Path) -> None:
    payload = {
        "records": [record.fields for record in doc.records],
        "validation": doc.artifacts.get("validation"),
        "normalize": doc.artifacts.get("normalize"),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_csv(
    doc: Document,
    schema: TargetSchema | None,
    path: Path,
) -> None:
    columns = _column_names(doc, schema)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in doc.records:
            row = {col: record.fields.get(col, "") for col in columns}
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
