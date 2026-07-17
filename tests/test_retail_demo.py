"""Tests for T-008 offline table extract, CLI, and retail demo."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

from prismdoc import (
    Context,
    Document,
    FieldSpec,
    Page,
    Source,
    TableExtractStage,
    TargetSchema,
    load_pipeline,
    registry,
)
from prismdoc.cli import main
from prismdoc.stages.table_extract import register_plugins

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAKE_SAMPLE = _REPO_ROOT / "examples" / "retail" / "make_sample.py"
_DEMO_YAML = _REPO_ROOT / "examples" / "retail" / "demo.yaml"


def _load_make_sample():
    spec = importlib.util.spec_from_file_location("retail_make_sample", _MAKE_SAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _retail_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string"),
            FieldSpec(name="price", type="number"),
            FieldSpec(name="currency", type="string"),
            FieldSpec(name="unit", type="string"),
            FieldSpec(name="brand", type="string"),
            FieldSpec(name="category", type="string"),
        ]
    )


def test_make_sample_writes_xlsx(tmp_path: Path) -> None:
    make_sample = _load_make_sample()
    out = tmp_path / "sample_catalog.xlsx"
    written = make_sample.write_sample_catalog(out)
    assert written == out
    assert out.is_file()
    assert out.stat().st_size > 0


def test_table_extract_maps_headers_and_skips_empty(tmp_path: Path) -> None:
    # Headers with noise; empty data row should be skipped.
    page_text = (
        "Name\tSKU\tPrice\tExtra\n"
        "Widget A\tW-1\t9.99\tignore\n"
        "\t\t\t\n"
        "Widget B\tW-2\t1.50\tx\n"
    )
    doc = Document(
        source=Source(path="memory"),
        pages=[Page(index=0, text=page_text)],
    )
    schema = TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string"),
            FieldSpec(name="price", type="number"),
        ]
    )
    result = TableExtractStage(schema=schema).run(doc, Context())

    assert len(result.records) == 2
    assert result.records[0].fields == {
        "name": "Widget A",
        "sku": "W-1",
        "price": "9.99",
    }
    assert result.records[1].fields["name"] == "Widget B"
    assert result.records[1].fields["sku"] == "W-2"
    assert "Extra" not in result.records[0].fields


def test_table_extract_skips_page_with_no_matching_headers() -> None:
    doc = Document(
        source=Source(path="memory"),
        pages=[Page(index=0, text="foo\tbar\n1\t2\n")],
    )
    result = TableExtractStage(schema=_retail_schema()).run(doc, Context())
    assert result.records == []


def test_end_to_end_cli_on_sample(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_sample = _load_make_sample()
    xlsx = make_sample.write_sample_catalog(tmp_path / "sample_catalog.xlsx")
    csv_path = tmp_path / "out.csv"

    code = main(
        [
            "--config",
            str(_DEMO_YAML),
            "--input",
            str(xlsx),
            "--csv",
            str(csv_path),
        ]
    )
    assert code == 0

    captured = capsys.readouterr()
    assert "records: 5" in captured.out

    # Re-run via load_pipeline for structured assertions
    pipeline, ctx = load_pipeline(_DEMO_YAML)
    doc = pipeline.run(Document(source=Source(path=str(xlsx))), ctx)
    assert len(doc.records) == 5

    by_sku = {r.fields["sku"]: r.fields for r in doc.records}
    assert by_sku["SKU-1001"]["name"] == "Arabica Coffee Beans"
    assert by_sku["SKU-1001"]["price"] == 12.5
    assert by_sku["SKU-1005"]["name"] == "Dark Chocolate Bar"
    assert by_sku["SKU-1005"]["price"] == 3.75

    assert csv_path.is_file()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0].keys()) == [
        "name",
        "sku",
        "price",
        "currency",
        "unit",
        "brand",
        "category",
    ]
    assert len(rows) == 5
    assert rows[0]["sku"] == "SKU-1001"
    assert rows[0]["name"] == "Arabica Coffee Beans"


def test_extract_table_registered_and_exported() -> None:
    register_plugins()
    assert "extract.table" in registry.get_keys()
    stage = registry.create("extract.table", schema=_retail_schema())
    assert isinstance(stage, TableExtractStage)

    import prismdoc

    assert prismdoc.TableExtractStage is TableExtractStage
    assert prismdoc.cli_main is main
    assert callable(prismdoc.cli_main)
