"""Tests for T-040 label/region-aware field verification."""

from __future__ import annotations

from prismdoc import Context, Document, LabelVerifyStage, Record, Source, registry
from prismdoc.stages.verify import (
    STATUS_LABEL_MISMATCH,
    STATUS_NO_EVIDENCE,
    STATUS_NO_LABEL,
    STATUS_UNLOCATED,
    STATUS_VERIFIED,
    register_plugins as register_verify,
)


def _doc(
    markdown: str,
    *,
    fields: dict | None = None,
    field_evidence: dict | None = None,
) -> Document:
    return Document(
        source=Source(path="/tmp/invoice.pdf", mime="application/pdf"),
        artifacts={"parsed_markdown": markdown},
        records=[
            Record(
                fields=fields or {"total": 100.0},
                field_evidence=field_evidence or {},
            )
        ],
    )


def _run(doc: Document) -> Document:
    return LabelVerifyStage().run(doc, Context())


def test_verified_grand_total() -> None:
    doc = _doc(
        "... Grand Total 100.00 ...",
        field_evidence={"total": "Grand Total 100.00"},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_VERIFIED


def test_label_mismatch_net_rejects_total() -> None:
    doc = _doc(
        "Total net worth 7.50",
        fields={"total": 7.50},
        field_evidence={"total": "Total net worth 7.50"},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_LABEL_MISMATCH

    doc2 = _doc(
        "Total net worth 7.50",
        fields={"total": 7.50},
        field_evidence={"total": "7.50"},
    )
    result2 = _run(doc2)
    assert result2.records[0].field_verification["total"] == STATUS_LABEL_MISMATCH


def test_word_boundary_subtotal_not_total() -> None:
    doc = _doc(
        "Subtotal 5.00",
        fields={"total": 5.00},
        field_evidence={"total": "Subtotal 5.00"},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_LABEL_MISMATCH


def test_no_window_bleed_previous_line() -> None:
    doc = _doc(
        "Total net worth 7.50\nTotal gross worth 8.25",
        fields={"total": 8.25},
        field_evidence={"total": "8.25"},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_VERIFIED


def test_no_evidence() -> None:
    doc = _doc(
        "Grand Total 100.00",
        field_evidence={"total": ""},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_NO_EVIDENCE


def test_unlocated() -> None:
    doc = _doc(
        "Grand Total 100.00",
        field_evidence={"total": "Grand Total 999.99"},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_UNLOCATED


def test_no_label() -> None:
    doc = _doc(
        "12.34 items listed",
        fields={"total": 12.34},
        field_evidence={"total": "12.34"},
    )
    result = _run(doc)
    assert result.records[0].field_verification["total"] == STATUS_NO_LABEL


def test_aggregate_counts() -> None:
    doc = Document(
        source=Source(path="/tmp/invoice.pdf", mime="application/pdf"),
        artifacts={"parsed_markdown": "Grand Total 100.00\nSubtotal 5.00"},
        records=[
            Record(
                fields={"total": 100.0, "subtotal": 5.0},
                field_evidence={
                    "total": "Grand Total 100.00",
                    "subtotal": "Subtotal 5.00",
                },
            ),
            Record(
                fields={"total": 999.0},
                field_evidence={"total": "missing value"},
            ),
        ],
    )
    result = _run(doc)
    counts = result.artifacts["verification"]
    assert counts[STATUS_VERIFIED] == 2
    assert counts[STATUS_UNLOCATED] == 1
    assert counts[STATUS_NO_EVIDENCE] == 0
    assert counts[STATUS_LABEL_MISMATCH] == 0
    assert counts[STATUS_NO_LABEL] == 0
    assert sum(counts.values()) == 3


def test_registry_and_export() -> None:
    register_verify()
    stage = registry.create("verify.label")
    assert isinstance(stage, LabelVerifyStage)

    from prismdoc import LabelVerifyStage as ExportedStage

    assert ExportedStage is LabelVerifyStage
