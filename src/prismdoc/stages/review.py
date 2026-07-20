"""Review stage: structured human-review queue from confidence and verification flags.

Composes ``low_confidence``, label/column verification mismatches, and provenance
into a single actionable ``doc.artifacts["review"]`` payload. This is the queue
abstraction — not a UI and not persistence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from prismdoc.models import Document
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage


class ReviewItem(BaseModel):
    """One field that needs human (or downstream queue) review."""

    record: int
    field: str
    value: Any = None
    reasons: list[str] = Field(default_factory=list)
    confidence: float | None = None
    evidence: str = ""
    source_text: str = ""
    page: int | None = None


class ReviewStage(Stage):
    """Build a deterministic review queue from confidence and verification signals."""

    name = "review"

    def __init__(self) -> None:
        pass

    def run(self, doc: Document, ctx: Context) -> Document:
        reasons_by_key: dict[tuple[int, str], set[str]] = {}

        for entry in doc.artifacts.get("low_confidence") or []:
            key = (int(entry["record"]), str(entry["field"]))
            reason = entry.get("reason") or "low_confidence"
            reasons_by_key.setdefault(key, set()).add(str(reason))

        for index, record in enumerate(doc.records):
            for field, status in record.field_verification.items():
                if status == "label_mismatch":
                    reasons_by_key.setdefault((index, field), set()).add(
                        "label_mismatch"
                    )
            for field, status in record.field_column_verification.items():
                if status == "column_mismatch":
                    reasons_by_key.setdefault((index, field), set()).add(
                        "column_mismatch"
                    )

        items: list[ReviewItem] = []
        for record_idx, field in sorted(reasons_by_key.keys()):
            record = doc.records[record_idx]
            provenance = record.provenance.get(field)
            items.append(
                ReviewItem(
                    record=record_idx,
                    field=field,
                    value=record.fields.get(field),
                    reasons=sorted(reasons_by_key[(record_idx, field)]),
                    confidence=record.confidence.get(field),
                    evidence=record.field_evidence.get(field, ""),
                    source_text=provenance.source_text if provenance is not None else "",
                    page=provenance.page if provenance is not None else None,
                )
            )

        doc.artifacts["review"] = {
            "items": [item.model_dump() for item in items],
            "count": len(items),
            "needs_review": bool(items),
        }
        return doc


def register_plugins() -> None:
    """Register the default review stage in the plugin registry."""
    register("review.default", ReviewStage)


register_plugins()
