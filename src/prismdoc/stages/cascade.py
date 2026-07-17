"""Cost-aware cascade: cheap primary stage, escalate to fallback on low score.

Fallback runs on the **same document state the primary received** (a deep copy
taken before primary runs), so escalation re-does the step rather than stacking
on top of the primary's mutations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prismdoc.models import Document
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage

Scorer = Callable[[Document], float]

_SCORERS: dict[str, Scorer] = {}


def register_scorer(name: str, fn: Scorer) -> None:
    """Register a named scorer callable."""
    _SCORERS[name] = fn


def get_scorer(name: str) -> Scorer:
    """Return a registered scorer by name.

    Raises:
        KeyError: if ``name`` is not registered.
    """
    try:
        return _SCORERS[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown scorer {name!r}; registered: {sorted(_SCORERS)}"
        ) from exc


def text_length(doc: Document) -> float:
    """Score by stripped length of parsed markdown (or full page text)."""
    text = doc.artifacts.get("parsed_markdown") or doc.full_text
    return float(len(str(text).strip()))


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def required_fill_ratio_for(schema: TargetSchema) -> Scorer:
    """Return a scorer: fraction of records with all schema-required fields set."""
    required_names = [field.name for field in schema.fields if field.required]

    def required_fill_ratio(doc: Document) -> float:
        if not doc.records:
            return 0.0
        if not required_names:
            return 1.0
        filled = 0
        for record in doc.records:
            if all(
                _is_non_empty(record.fields.get(name)) for name in required_names
            ):
                filled += 1
        return filled / len(doc.records)

    return required_fill_ratio


class CascadeStage(Stage):
    """Run ``primary``, score the result, escalate to ``fallback`` if below threshold."""

    name = "cascade"

    def __init__(
        self,
        primary: Stage,
        fallback: Stage,
        scorer: Scorer,
        threshold: float,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.scorer = scorer
        self.threshold = threshold

    def run(self, doc: Document, ctx: Context) -> Document:
        # Snapshot pre-primary state so fallback can re-do the step cleanly.
        baseline = doc.model_copy(deep=True)
        primary_doc = self.primary.run(doc, ctx)
        score = float(self.scorer(primary_doc))
        if score < self.threshold:
            result = self.fallback.run(baseline, ctx)
            tier = "fallback"
        else:
            result = primary_doc
            tier = "primary"

        entry: dict[str, Any] = {
            "tier": tier,
            "score": score,
            "threshold": self.threshold,
        }
        router = result.artifacts.get("router")
        if isinstance(router, list):
            router.append(entry)
        else:
            result.artifacts["router"] = [entry]
        return result


def register_plugins() -> None:
    """Register built-in scorers."""
    register_scorer("text_length", text_length)


register_plugins()
