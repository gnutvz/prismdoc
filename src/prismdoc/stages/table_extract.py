"""Offline deterministic table extractor for tab-separated spreadsheet pages."""

from __future__ import annotations

from typing import Any

from prismdoc.models import Document, Page, Record
from prismdoc.registry import register
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage


class TableExtractStage(Stage):
    """Map spreadsheet headers to schema fields without an LLM."""

    name = "extract"

    def __init__(self, schema: TargetSchema) -> None:
        self.schema = schema

    def run(self, doc: Document, ctx: Context) -> Document:
        records: list[Record] = []
        for page in doc.pages:
            records.extend(_extract_page(page, self.schema))
        doc.records = records
        return doc


def _extract_page(page: Page, schema: TargetSchema) -> list[Record]:
    rows = [line.split("\t") for line in page.text.splitlines()]
    if not rows:
        return []

    header = rows[0]
    col_to_field = _map_headers(header, schema)
    if not col_to_field:
        return []

    field_names = set(schema.field_names())
    records: list[Record] = []
    for row in rows[1:]:
        if _is_empty_row(row):
            continue
        fields: dict[str, Any] = {}
        for col_index, field_name in col_to_field.items():
            if field_name not in field_names:
                continue
            value = row[col_index] if col_index < len(row) else ""
            fields[field_name] = value
        if fields:
            records.append(Record(fields=fields))
    return records


def _map_headers(header: list[str], schema: TargetSchema) -> dict[int, str]:
    """Map column index -> schema field name when headers match after normalize."""
    by_normalized = {_normalize_key(name): name for name in schema.field_names()}
    mapping: dict[int, str] = {}
    for index, cell in enumerate(header):
        key = _normalize_key(cell)
        if key in by_normalized:
            mapping[index] = by_normalized[key]
    return mapping


def _normalize_key(value: str) -> str:
    return value.lower().strip().replace(" ", "").replace("_", "")


def _is_empty_row(row: list[str]) -> bool:
    return all(cell.strip() == "" for cell in row)


def register_plugins() -> None:
    """Register the offline table extract stage in the plugin registry."""
    register("extract.table", TableExtractStage)


register_plugins()
