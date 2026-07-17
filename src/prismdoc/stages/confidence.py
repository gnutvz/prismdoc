"""Confidence stage: rule-based per-field confidence scores and low-confidence flags."""

from __future__ import annotations

from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.validate import _coerce_value, _is_missing_or_empty

_FALLBACK_SCALE = 0.85
_CONF_MISSING = 0.0
_CONF_VALID = 0.9
_CONF_TYPE_MISMATCH = 0.5


class ConfidenceStage(Stage):
    """Attach per-field confidence scores and collect low-confidence flags."""

    name = "confidence"

    def __init__(self, schema: TargetSchema, threshold: float = 0.5) -> None:
        self.schema = schema
        self.threshold = threshold

    def run(self, doc: Document, ctx: Context) -> Document:
        scale = _fallback_scale(doc)
        low: list[dict[str, Any]] = []

        for index, record in enumerate(doc.records):
            for spec in self.schema.fields:
                score = _field_confidence(record, spec, scale)
                record.confidence[spec.name] = score
                if score < self.threshold:
                    low.append(
                        {
                            "record": index,
                            "field": spec.name,
                            "confidence": score,
                        }
                    )

        doc.artifacts["low_confidence"] = low
        return doc


def _fallback_scale(doc: Document) -> float:
    router = doc.artifacts.get("router")
    if not isinstance(router, list):
        return 1.0
    if any(
        isinstance(entry, dict) and entry.get("tier") == "fallback"
        for entry in router
    ):
        return _FALLBACK_SCALE
    return 1.0


def _field_confidence(
    record: Record, spec: FieldSpec, scale: float
) -> float:
    if spec.name in record.confidence:
        return record.confidence[spec.name]

    value = record.fields.get(spec.name)
    if _is_missing_or_empty(value):
        score = _CONF_MISSING
    else:
        _, error = _coerce_value(value, spec)
        score = _CONF_VALID if error is None else _CONF_TYPE_MISMATCH

    return max(0.0, min(1.0, score * scale))


def register_plugins() -> None:
    """Register the default confidence stage in the plugin registry."""
    register("confidence.default", ConfidenceStage)


register_plugins()
