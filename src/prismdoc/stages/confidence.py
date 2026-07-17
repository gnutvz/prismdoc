"""Confidence stage: transparent heuristic per-field scores and low-confidence flags.

Confidence is driven by value presence, type-coercibility, and grounding (whether the
extracted value appears in the document source text). Grounding is the primary signal
for catching hallucinated values.

This is **not** a calibrated probability. Proper calibration requires a labeled
validation set (see the eval harness) and is future work. Stronger signals such as
model logprobs or self-consistency are also deferred.
"""

from __future__ import annotations

import re
from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.validate import _coerce_value, _is_missing_or_empty

_CONF_MISSING = 0.0
_CONF_TYPE_MISMATCH = 0.3
_CONF_UNGROUNDED = 0.4
_CONF_GROUNDED = 0.9

_WHITESPACE_RE = re.compile(r"\s+")


class ConfidenceStage(Stage):
    """Attach per-field confidence via presence, type-coercibility, and grounding.

    Scores are a transparent heuristic in [0, 1], not calibrated probabilities.
    Grounding (value found in source text) is the main check against hallucinations.
    """

    name = "confidence"

    def __init__(self, schema: TargetSchema, threshold: float = 0.5) -> None:
        self.schema = schema
        self.threshold = threshold

    def run(self, doc: Document, ctx: Context) -> Document:
        doc_text = doc.artifacts.get("parsed_markdown") or doc.full_text
        if not isinstance(doc_text, str):
            doc_text = doc.full_text
        low: list[dict[str, Any]] = []

        for index, record in enumerate(doc.records):
            for spec in self.schema.fields:
                score, reason = _field_confidence(record, spec, doc_text)
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


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.lower()).strip()


def _digits_only(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip()
    if not text:
        return False
    digits = _digits_only(text)
    if not digits:
        return False
    # Allow common numeric punctuation; reject if other letters remain.
    stripped = re.sub(r"[\d\s.,+\-]", "", text)
    return stripped == ""


def _is_grounded(value: Any, doc_text: str) -> bool:
    """Return True if a normalized form of ``value`` appears in ``doc_text``."""
    raw = str(value).strip()
    if not raw:
        return False

    norm_value = _normalize(raw)
    if not norm_value:
        return False

    norm_doc = _normalize(doc_text)
    if norm_value in norm_doc:
        return True

    if _looks_numeric(value):
        value_digits = _digits_only(raw)
        doc_digits = _digits_only(norm_doc)
        if value_digits and value_digits in doc_digits:
            return True

    return False


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
