"""Confidence stage: transparent heuristic per-field scores and low-confidence flags.

Confidence is driven by value presence, type-coercibility, and grounding (whether the
extracted value appears in the document source text). Grounding is the primary signal
for catching hallucinated values.

Scores are a transparent heuristic, not calibrated probabilities by default. Deployers
may pass a dataset-specific ``calibration`` map (raw → measured accuracy) measured on
their own labeled sample — see ``docs/BENCHMARK.md`` "Confidence calibration". The
SROIE map there is an example, not a hardcoded default. Stronger signals such as model
logprobs or self-consistency are deferred.
"""

from __future__ import annotations

from typing import Any

from prismdoc.matching import value_in_text
from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.validate import _coerce_value, _is_missing_or_empty

_CONF_MISSING = 0.0
_CONF_TYPE_MISMATCH = 0.3
_CONF_UNGROUNDED = 0.4
_CONF_GROUNDED = 0.9


class ConfidenceStage(Stage):
    """Attach per-field confidence via presence, type-coercibility, and grounding.

    Scores are a transparent heuristic in [0, 1]. Optional ``calibration`` remaps raw
    discrete scores (``0.0`` / ``0.3`` / ``0.4`` / ``0.9``) to measured accuracies;
    measure that map on the deployer's own labeled sample (dataset-specific). The
    SROIE map in the benchmark docs is an example, not a default. Grounding (value
    found in source text) is the main check against hallucinations.
    """

    name = "confidence"

    def __init__(
        self,
        schema: TargetSchema,
        threshold: float = 0.5,
        calibration: dict[float, float] | None = None,
    ) -> None:
        self.schema = schema
        self.threshold = threshold
        self.calibration = calibration

    def run(self, doc: Document, ctx: Context) -> Document:
        doc_text = doc.artifacts.get("parsed_markdown") or doc.full_text
        if not isinstance(doc_text, str):
            doc_text = doc.full_text
        low: list[dict[str, Any]] = []

        for index, record in enumerate(doc.records):
            for spec in self.schema.fields:
                preset = spec.name in record.confidence
                score, reason = _field_confidence(record, spec, doc_text)
                if not preset:
                    if self.calibration is not None:
                        score = self.calibration.get(round(score, 3), score)
                    record.confidence[spec.name] = score
                if score < self.threshold:
                    entry: dict[str, Any] = {
                        "record": index,
                        "field": spec.name,
                        "confidence": score,
                    }
                    if reason is not None:
                        entry["reason"] = reason
                    low.append(entry)

        doc.artifacts["low_confidence"] = low
        return doc


def _is_grounded(value: Any, doc_text: str) -> bool:
    """Return True if a normalized form of ``value`` appears in ``doc_text``."""
    return value_in_text(value, doc_text)


def _field_confidence(
    record: Record, spec: FieldSpec, doc_text: str
) -> tuple[float, str | None]:
    """Return ``(score, reason)``; ``reason`` is None when a pre-set score is kept."""
    if spec.name in record.confidence:
        return record.confidence[spec.name], None

    value = record.fields.get(spec.name)
    if _is_missing_or_empty(value):
        return _CONF_MISSING, "missing"

    _, error = _coerce_value(value, spec)
    if error is not None:
        return _CONF_TYPE_MISMATCH, "type_mismatch"

    if not _is_grounded(value, doc_text):
        return _CONF_UNGROUNDED, "ungrounded"

    return _CONF_GROUNDED, None


def register_plugins() -> None:
    """Register the default confidence stage in the plugin registry."""
    register("confidence.default", ConfidenceStage)


register_plugins()
