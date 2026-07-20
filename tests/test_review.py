"""Tests for ReviewStage (structured human-review queue artifact)."""

from __future__ import annotations

import prismdoc
from prismdoc import (
    Context,
    Document,
    FieldProvenance,
    Page,
    Record,
    ReviewItem,
    ReviewStage,
    Source,
    registry,
)
from prismdoc.config import _ensure_plugins
from prismdoc.stages.review import register_plugins


def _doc(
    fields: dict,
    *,
    confidence: dict[str, float] | None = None,
    field_evidence: dict[str, str] | None = None,
    field_verification: dict[str, str] | None = None,
    field_column_verification: dict[str, str] | None = None,
    provenance: dict[str, FieldProvenance] | None = None,
    low_confidence: list[dict] | None = None,
) -> Document:
    record = Record(
        fields=fields,
        confidence=confidence or {},
        field_evidence=field_evidence or {},
        field_verification=field_verification or {},
        field_column_verification=field_column_verification or {},
        provenance=provenance or {},
    )
    artifacts: dict = {}
    if low_confidence is not None:
        artifacts["low_confidence"] = low_confidence
    return Document(
        source=Source(path="/tmp/invoice.md"),
        pages=[Page(index=0, text="TOTAL 8.25")],
        records=[record],
        artifacts=artifacts,
    )


def test_low_confidence_becomes_review_item() -> None:
    doc = _doc(
        {"total": 8.25},
        confidence={"total": 0.3},
        low_confidence=[
            {
                "record": 0,
                "field": "total",
                "confidence": 0.3,
                "reason": "ungrounded",
            }
        ],
    )
    result = ReviewStage().run(doc, Context())
    review = result.artifacts["review"]

    assert review["count"] == 1
    assert review["needs_review"] is True
    item = review["items"][0]
    assert item["field"] == "total"
    assert item["reasons"] == ["ungrounded"]
    assert item["value"] == 8.25
    assert item["confidence"] == 0.3


def test_column_mismatch_becomes_review_item() -> None:
    doc = _doc(
        {"total": 8.25},
        field_column_verification={"total": "column_mismatch"},
        field_evidence={"total": "8.25 in Gross column"},
    )
    result = ReviewStage().run(doc, Context())
    review = result.artifacts["review"]

    assert review["count"] == 1
    item = review["items"][0]
    assert "column_mismatch" in item["reasons"]
    assert item["evidence"] == "8.25 in Gross column"


def test_merged_reasons_from_low_confidence_and_column_mismatch() -> None:
    doc = _doc(
        {"total": 8.25},
        confidence={"total": 0.3},
        field_column_verification={"total": "column_mismatch"},
        low_confidence=[
            {
                "record": 0,
                "field": "total",
                "confidence": 0.3,
                "reason": "ungrounded",
            }
        ],
    )
    result = ReviewStage().run(doc, Context())
    items = result.artifacts["review"]["items"]

    assert len(items) == 1
    assert items[0]["reasons"] == ["column_mismatch", "ungrounded"]


def test_provenance_populated_on_review_item() -> None:
    doc = _doc(
        {"total": 8.25},
        confidence={"total": 0.3},
        provenance={
            "total": FieldProvenance(page=1, source_text="TOTAL 8.25"),
        },
        low_confidence=[
            {
                "record": 0,
                "field": "total",
                "confidence": 0.3,
                "reason": "ungrounded",
            }
        ],
    )
    result = ReviewStage().run(doc, Context())
    item = result.artifacts["review"]["items"][0]

    assert item["page"] == 1
    assert item["source_text"] == "TOTAL 8.25"


def test_no_flags_yields_empty_review() -> None:
    doc = _doc({"total": 8.25}, confidence={"total": 0.9})
    result = ReviewStage().run(doc, Context())
    review = result.artifacts["review"]

    assert review["items"] == []
    assert review["count"] == 0
    assert review["needs_review"] is False


def test_registry_config_and_export() -> None:
    register_plugins()
    assert "review.default" in registry.get_keys()
    assert registry.get_factory("review.default") is ReviewStage

    registry.clear()
    _ensure_plugins()
    assert "review.default" in registry.get_keys()
    stage = registry.create("review.default")
    assert isinstance(stage, ReviewStage)

    assert prismdoc.ReviewStage is ReviewStage
    assert prismdoc.ReviewItem is ReviewItem
    from prismdoc import ReviewItem as ImportedItem
    from prismdoc import ReviewStage as ImportedStage

    assert ImportedStage is ReviewStage
    assert ImportedItem is ReviewItem
