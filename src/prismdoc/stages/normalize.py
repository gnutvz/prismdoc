"""Normalize stage: clean string values and deduplicate records."""

from __future__ import annotations

import re
from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_WHITESPACE_RE = re.compile(r"\s+")


class NormalizeStage(Stage):
    """Trim/collapse whitespace, empty-to-None, and dedupe identical records."""

    name = "normalize"

    def run(self, doc: Document, ctx: Context) -> Document:
        for record in doc.records:
            record.fields = {
                key: _normalize_value(value) for key, value in record.fields.items()
            }

        unique: list[Record] = []
        deduped = 0
        for record in doc.records:
            if any(existing.fields == record.fields for existing in unique):
                deduped += 1
            else:
                unique.append(record)

        doc.records = unique
        doc.artifacts["normalize"] = {"deduped": deduped}
        return doc


def _normalize_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = _WHITESPACE_RE.sub(" ", value.strip())
    return None if cleaned == "" else cleaned


def register_plugins() -> None:
    """Register the default normalize stage in the plugin registry."""
    register("normalize.default", NormalizeStage)


register_plugins()
