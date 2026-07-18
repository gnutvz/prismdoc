"""Cost-aware cascade: cheap primary stage, escalate to fallback on low score.

Fallback runs on the **same document state the primary received** (a deep copy
taken before primary runs), so escalation re-does the step rather than stacking
on top of the primary's mutations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from prismdoc.matching import value_in_text
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


def _parsed_or_full_text(doc: Document) -> str:
    text = doc.artifacts.get("parsed_markdown") or doc.full_text
    return str(text) if text is not None else ""


def text_length(doc: Document) -> float:
    """Score by stripped length of parsed markdown (or full page text)."""
    return float(len(_parsed_or_full_text(doc).strip()))


def char_validity(doc: Document) -> float:
    """Alphanumeric ratio over non-whitespace characters.

    Empty / no non-whitespace text scores ``0.0``. High for language content;
    near ``0.0`` for symbol-dominated OCR garbage.
    """
    text = _parsed_or_full_text(doc)
    non_ws = [ch for ch in text if not ch.isspace()]
    if not non_ws:
        return 0.0
    return sum(1 for ch in non_ws if ch.isalnum()) / len(non_ws)


def text_sufficiency(doc: Document) -> float:
    """Normalized length score; 200 stripped characters -> ``1.0``."""
    return min(1.0, len(_parsed_or_full_text(doc).strip()) / 200.0)


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


def field_coverage_for(schema: TargetSchema) -> Scorer:
    """Return a scorer: mean fraction of schema fields that are non-empty."""
    field_names = [field.name for field in schema.fields]

    def field_coverage(doc: Document) -> float:
        if not doc.records:
            return 0.0
        if not field_names:
            return 1.0
        total = 0.0
        for record in doc.records:
            filled = sum(
                1 for name in field_names if _is_non_empty(record.fields.get(name))
            )
            total += filled / len(field_names)
        return total / len(doc.records)

    return field_coverage


def grounding_ratio_for(schema: TargetSchema) -> Scorer:
    """Return a scorer: mean fraction of extracted values grounded in doc text."""
    field_names = [field.name for field in schema.fields]

    def grounding_ratio(doc: Document) -> float:
        if not doc.records:
            return 0.0
        doc_text = _parsed_or_full_text(doc)
        total = 0.0
        for record in doc.records:
            extracted = [
                record.fields.get(name)
                for name in field_names
                if _is_non_empty(record.fields.get(name))
            ]
            if not extracted:
                continue
            grounded = sum(1 for value in extracted if value_in_text(value, doc_text))
            total += grounded / len(extracted)
        return total / len(doc.records)

    return grounding_ratio


def make_composite(components: Sequence[Mapping[str, Any]]) -> Scorer:
    """Build a weight-normalized composite scorer from named or callable parts.

    Each component is ``{"scorer": <name-or-callable>, "weight": <float>}``.
    Named scorers are resolved via the registry. At score time, components that
    raise are skipped and remaining weights are renormalized.
    """
    if not components:
        raise ValueError("make_composite requires at least one component")

    resolved: list[tuple[Scorer, float]] = []
    for index, component in enumerate(components):
        if "scorer" not in component or "weight" not in component:
            raise ValueError(
                f"composite component[{index}] must have 'scorer' and 'weight'"
            )
        raw = component["scorer"]
        weight = float(component["weight"])
        if callable(raw):
            scorer: Scorer = raw  # type: ignore[assignment]
        elif isinstance(raw, str):
            scorer = get_scorer(raw)
        else:
            raise TypeError(
                f"composite component[{index}].scorer must be a name or "
                f"callable, got {type(raw).__name__}"
            )
        resolved.append((scorer, weight))

    def composite(doc: Document) -> float:
        scored: list[tuple[float, float]] = []
        for scorer, weight in resolved:
            try:
                scored.append((float(scorer(doc)), weight))
            except Exception:
                continue
        if not scored:
            return 0.0
        weight_sum = sum(weight for _, weight in scored)
        if weight_sum <= 0.0:
            return 0.0
        return sum(score * (weight / weight_sum) for score, weight in scored)

    return composite


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
    """Register built-in parameter-free scorers."""
    register_scorer("text_length", text_length)
    register_scorer("char_validity", char_validity)
    register_scorer("text_sufficiency", text_sufficiency)


register_plugins()
