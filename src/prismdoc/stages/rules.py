"""Cross-field business-rule validation stage.

Declarative, typed rule engine for semantic checks that grounding and schema-type
validation miss (e.g. ``subtotal + tax == total``). Rules are parameterized
factories — no ``eval`` / ``exec``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prismdoc.models import Document
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

RuleCheck = Callable[[dict[str, Any]], str | None]
RuleFactory = Callable[..., RuleCheck]

_RULES: dict[str, RuleFactory] = {}


def register_rule(type_name: str, fn: RuleFactory) -> None:
    """Register a rule factory under ``type_name`` (e.g. ``\"sum_equals\"``)."""
    _RULES[type_name] = fn


def get_rule(type_name: str) -> RuleFactory:
    """Return a registered rule factory by type name.

    Raises:
        KeyError: if ``type_name`` is not registered.
    """
    try:
        return _RULES[type_name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown rule type {type_name!r}; registered: {sorted(_RULES)}"
        ) from exc


def _coerce_float(value: Any) -> float | None:
    """Try to coerce ``value`` to float; return ``None`` on failure."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _field_missing(fields: dict[str, Any], name: str) -> bool:
    return name not in fields or fields[name] is None or fields[name] == ""


def _make_sum_equals(
    *,
    fields: list[str],
    target: str,
    tolerance: float = 0.01,
) -> RuleCheck:
    field_names = list(fields)
    target_name = target
    tol = float(tolerance)

    def check(record_fields: dict[str, Any]) -> str | None:
        values: list[float] = []
        for name in field_names:
            if _field_missing(record_fields, name):
                return f"cannot evaluate: {name} missing/non-numeric"
            coerced = _coerce_float(record_fields[name])
            if coerced is None:
                return f"cannot evaluate: {name} missing/non-numeric"
            values.append(coerced)

        if _field_missing(record_fields, target_name):
            return f"cannot evaluate: {target_name} missing/non-numeric"
        target_value = _coerce_float(record_fields[target_name])
        if target_value is None:
            return f"cannot evaluate: {target_name} missing/non-numeric"

        total = sum(values)
        if abs(total - target_value) > tol:
            return (
                f"sum({', '.join(field_names)})={total} != "
                f"{target_name}={target_value} (tolerance={tol})"
            )
        return None

    return check


def _normalize_set_token(value: Any) -> str:
    return str(value).strip().lower()


def _make_in_set(*, field: str, values: list[Any]) -> RuleCheck:
    field_name = field
    allowed = {_normalize_set_token(v) for v in values}

    def check(record_fields: dict[str, Any]) -> str | None:
        if field_name not in record_fields or record_fields[field_name] is None:
            return f"cannot evaluate: {field_name} missing"
        normalized = _normalize_set_token(record_fields[field_name])
        if normalized not in allowed:
            return (
                f"{field_name}={record_fields[field_name]!r} "
                f"not in allowed set"
            )
        return None

    return check


def _make_range(
    *,
    field: str,
    min: float | None = None,
    max: float | None = None,
) -> RuleCheck:
    field_name = field
    min_val = None if min is None else float(min)
    max_val = None if max is None else float(max)

    def check(record_fields: dict[str, Any]) -> str | None:
        if _field_missing(record_fields, field_name):
            return f"cannot evaluate: {field_name} missing/non-numeric"
        coerced = _coerce_float(record_fields[field_name])
        if coerced is None:
            return f"cannot evaluate: {field_name} missing/non-numeric"
        if min_val is not None and coerced < min_val:
            return f"{field_name}={coerced} below min={min_val}"
        if max_val is not None and coerced > max_val:
            return f"{field_name}={coerced} above max={max_val}"
        return None

    return check


def _make_non_negative(*, field: str) -> RuleCheck:
    field_name = field

    def check(record_fields: dict[str, Any]) -> str | None:
        if _field_missing(record_fields, field_name):
            return f"cannot evaluate: {field_name} missing/non-numeric"
        coerced = _coerce_float(record_fields[field_name])
        if coerced is None:
            return f"cannot evaluate: {field_name} missing/non-numeric"
        if coerced < 0:
            return f"{field_name}={coerced} is negative"
        return None

    return check


class RuleValidateStage(Stage):
    """Evaluate declarative cross-field rules; collect violations (never raise)."""

    name = "rules"

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = list(rules)
        self._checks: list[tuple[str, RuleCheck]] = []
        for spec in self.rules:
            if not isinstance(spec, dict):
                raise TypeError(
                    f"rule spec must be a dict, got {type(spec).__name__}"
                )
            if "type" not in spec:
                raise ValueError("rule spec missing required key 'type'")
            rule_type = spec["type"]
            if not isinstance(rule_type, str):
                raise TypeError(
                    f"rule type must be a string, got {type(rule_type).__name__}"
                )
            params = {k: v for k, v in spec.items() if k != "type"}
            factory = get_rule(rule_type)
            self._checks.append((rule_type, factory(**params)))

    def run(self, doc: Document, ctx: Context) -> Document:
        violations: list[dict[str, Any]] = []
        uneval: list[dict[str, Any]] = []
        n_rules = len(self._checks)
        n_records = len(doc.records)

        for index, record in enumerate(doc.records):
            for rule_type, check in self._checks:
                detail = check(record.fields)
                if detail is None:
                    continue
                # A rule that cannot run (a field is missing or non-numeric) is
                # NOT the same as a rule that ran and failed. Lumping the two
                # inflates the violation rate, so keep them in separate buckets.
                entry = {"record": index, "rule": rule_type, "detail": detail}
                if detail.startswith("cannot evaluate"):
                    uneval.append(entry)
                else:
                    violations.append(entry)

        doc.artifacts["rule_violations"] = violations
        doc.artifacts["rule_uneval"] = uneval
        doc.artifacts["rules"] = {
            "checked": n_rules * n_records,
            "violations": len(violations),
            "cannot_evaluate": len(uneval),
        }
        return doc


def register_builtin_rules() -> None:
    """Register the built-in rule type factories."""
    register_rule("sum_equals", _make_sum_equals)
    register_rule("in_set", _make_in_set)
    register_rule("range", _make_range)
    register_rule("non_negative", _make_non_negative)


def register_plugins() -> None:
    """Register built-in rules and the default rules stage."""
    register_builtin_rules()
    register("rules.default", RuleValidateStage)


register_plugins()
