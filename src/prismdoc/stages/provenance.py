"""Provenance stage: locate each extracted field value in the parsed document.

Answers the enterprise audit question "where did this value come from?" by
searching pages and layout blocks after extraction. Locations are never
fabricated — unfound values simply have no provenance entry.
"""

from __future__ import annotations

from typing import Any

from prismdoc.matching import value_in_text
from prismdoc.models import Document, FieldProvenance
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_SNIPPET_MAX = 120


class ProvenanceStage(Stage):
    """Attach per-field page / bbox / source_text when the value is found in-doc."""

    name = "provenance"

    def run(self, doc: Document, ctx: Context) -> Document:
        for record in doc.records:
            for field, value in record.fields.items():
                if _is_empty(value):
                    continue
                located = _locate_value(value, doc)
                if located is not None:
                    record.provenance[field] = located
        return doc


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _locate_value(value: Any, doc: Document) -> FieldProvenance | None:
    """Return provenance for ``value`` if found; otherwise ``None``."""
    for page in doc.pages:
        for block in page.blocks:
            if value_in_text(value, block.text):
                return FieldProvenance(
                    page=page.index,
                    bbox=block.bbox,
                    source_text=block.text,
                )
        if value_in_text(value, page.text):
            return FieldProvenance(
                page=page.index,
                bbox=None,
                source_text=_page_snippet(page.text),
            )
    return None


def _page_snippet(text: str) -> str:
    """Return a short source snippet for a page-level (no-block) match."""
    if not text:
        return ""
    if len(text) <= _SNIPPET_MAX:
        return text
    return text[:_SNIPPET_MAX]


def register_plugins() -> None:
    """Register the default provenance stage in the plugin registry."""
    register("provenance.default", ProvenanceStage)


register_plugins()
