"""Tests for T-042 table column verification (semantic verification, slice 3)."""

from __future__ import annotations

from prismdoc import Context, Document, Record, Source, TableColumnVerifyStage, registry
from prismdoc.stages.verify import (
    STATUS_COLUMN_MISMATCH,
    STATUS_COLUMN_NO_LABEL,
    STATUS_COLUMN_VERIFIED,
    STATUS_NO_TABLE,
    STATUS_VALUE_NOT_IN_TABLE,
    numbers_match,
    parse_markdown_tables,
    register_plugins as register_verify,
)

INVOICE_TABLE = """\
|   No. | Description   | Qty  | Net price | Net worth | VAT [%] | Gross worth |
|-------|---------------|------|-----------|-----------|---------|-------------|
|    1. | Corkscrew ... | 1,00 | 7,50      | 7,50      | 10%     | 8,25        |
"""


def _doc(
    markdown: str,
    *,
    fields: dict | None = None,
) -> Document:
    return Document(
        source=Source(path="/tmp/invoice.pdf", mime="application/pdf"),
        artifacts={"parsed_markdown": markdown},
        records=[Record(fields=fields or {"total": 8.25})],
    )


def _run(doc: Document) -> Document:
    return TableColumnVerifyStage().run(doc, Context())


def test_column_verified_gross_worth() -> None:
    result = _run(_doc(INVOICE_TABLE, fields={"total": 8.25}))
    assert result.records[0].field_column_verification["total"] == STATUS_COLUMN_VERIFIED
    assert result.artifacts["column_verification"][STATUS_COLUMN_VERIFIED] == 1


def test_column_mismatch_net_worth() -> None:
    result = _run(_doc(INVOICE_TABLE, fields={"total": 7.50}))
    assert result.records[0].field_column_verification["total"] == STATUS_COLUMN_MISMATCH


def test_subtotal_column_verified_net_worth() -> None:
    result = _run(_doc(INVOICE_TABLE, fields={"subtotal": 7.50}))
    assert (
        result.records[0].field_column_verification["subtotal"] == STATUS_COLUMN_VERIFIED
    )


def test_subtotal_column_mismatch_gross_worth() -> None:
    result = _run(_doc(INVOICE_TABLE, fields={"subtotal": 8.25}))
    assert (
        result.records[0].field_column_verification["subtotal"]
        == STATUS_COLUMN_MISMATCH
    )


def test_tax_column_verified_total_vat() -> None:
    markdown = """\
| Description | Qty | Net worth | Total VAT | Gross worth |
|-------------|-----|-----------|-----------|-------------|
| Item        | 1   | 7,50      | 0,75      | 8,25        |
"""
    result = _run(_doc(markdown, fields={"tax": 0.75}))
    assert result.records[0].field_column_verification["tax"] == STATUS_COLUMN_VERIFIED


def test_tax_column_mismatch_gross_worth() -> None:
    result = _run(_doc(INVOICE_TABLE, fields={"tax": 8.25}))
    assert result.records[0].field_column_verification["tax"] == STATUS_COLUMN_MISMATCH


def test_number_normalization() -> None:
    assert numbers_match(8.25, "8,25")
    assert numbers_match(57483.07, "57 483,07")
    assert numbers_match(1767.34, "1.767,34")

    # End-to-end: field float matches EU-formatted cell under Gross worth.
    result = _run(_doc(INVOICE_TABLE, fields={"total": 8.25}))
    assert result.records[0].field_column_verification["total"] == STATUS_COLUMN_VERIFIED

    big_table = """\
| Amount |
|--------|
| 57 483,07 |
"""
    result2 = _run(_doc(big_table, fields={"total": 57483.07}))
    # "Amount" is neither expect nor reject → no_label, but value was found.
    assert result2.records[0].field_column_verification["total"] == STATUS_COLUMN_NO_LABEL


def test_value_not_in_table() -> None:
    result = _run(_doc(INVOICE_TABLE, fields={"total": 999.99}))
    assert (
        result.records[0].field_column_verification["total"] == STATUS_VALUE_NOT_IN_TABLE
    )


def test_no_table() -> None:
    result = _run(_doc("Grand Total 8.25\nNo pipes here.", fields={"total": 8.25}))
    assert result.records[0].field_column_verification["total"] == STATUS_NO_TABLE


def test_column_no_label() -> None:
    markdown = """\
| Item | Reference |
|------|-----------|
| Widget | 42.00 |
"""
    result = _run(_doc(markdown, fields={"total": 42.00}))
    assert result.records[0].field_column_verification["total"] == STATUS_COLUMN_NO_LABEL


def test_expect_precedence_over_reject() -> None:
    # Same value under both Gross worth (expect) and Net worth (reject).
    markdown = """\
| Net worth | Gross worth |
|-----------|-------------|
| 8,25      | 8,25        |
"""
    result = _run(_doc(markdown, fields={"total": 8.25}))
    assert result.records[0].field_column_verification["total"] == STATUS_COLUMN_VERIFIED


def test_parse_markdown_tables_skips_separator() -> None:
    tables = parse_markdown_tables(INVOICE_TABLE)
    assert len(tables) == 1
    header, rows = tables[0]
    assert header[-1] == "Gross worth"
    assert len(rows) == 1
    assert rows[0][-1] == "8,25"
    assert "Net worth" in header


def test_registry_and_export() -> None:
    register_verify()
    stage = registry.create("verify.column")
    assert isinstance(stage, TableColumnVerifyStage)

    from prismdoc import TableColumnVerifyStage as ExportedStage

    assert ExportedStage is TableColumnVerifyStage
