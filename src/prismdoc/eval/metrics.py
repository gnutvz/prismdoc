"""Record alignment and per-field accuracy metrics."""

from __future__ import annotations

import math
import re
from typing import Any

from prismdoc.matching import normalize_alphanumeric
from prismdoc.schema import TargetSchema

_ABS_TOL = 1e-9
_REL_TOL = 1e-6
_CURRENCY_RE = re.compile(r"[$€£¥₩₹]")
_THOUSANDS_RE = re.compile(r",")
_WHITESPACE_RE = re.compile(r"\s+")

_TRUE_TOKENS = frozenset({"true", "1", "yes"})
_FALSE_TOKENS = frozenset({"false", "0", "no"})


def values_match(pred: Any, exp: Any, field_type: str) -> bool:
    """Compare predicted and expected values with type-aware semantics.

    Missing (``None``) values never match. Unknown ``field_type`` falls back to
    string comparison.
    """
    if pred is None or exp is None:
        return False

    if field_type == "number":
        return _numbers_match(pred, exp)
    if field_type == "integer":
        return _integers_match(pred, exp)
    if field_type == "boolean":
        return _booleans_match(pred, exp)
    return _strings_match(pred, exp)


def align_records(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    key_field: str | None = None,
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    """Align predicted and expected records by ``key_field`` or by index.

    Unmatched records on either side are paired with ``None``.
    """
    if key_field is None:
        return _align_by_index(predicted, expected)
    return _align_by_key(predicted, expected, key_field)


def field_metrics(
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]],
    schema: TargetSchema,
) -> dict[str, Any]:
    """Compute per-field and overall accuracy for aligned record pairs.

    A field is correct when predicted and expected values are both present and
    ``values_match`` agrees under the field's declared schema type. Missing
    predicted or expected counts as incorrect.
    """
    field_names = schema.field_names()
    field_types = {field.name: field.type for field in schema.fields}
    per_field: dict[str, dict[str, float | int]] = {
        name: {"correct": 0, "total": 0, "accuracy": 0.0} for name in field_names
    }

    exact_matches = 0
    overall_correct = 0
    overall_total = 0

    for predicted, expected in pairs:
        record_ok = predicted is not None and expected is not None
        for name in field_names:
            per_field[name]["total"] = int(per_field[name]["total"]) + 1
            overall_total += 1
            if _field_correct(predicted, expected, name, field_types.get(name, "string")):
                per_field[name]["correct"] = int(per_field[name]["correct"]) + 1
                overall_correct += 1
            else:
                record_ok = False
        if record_ok:
            exact_matches += 1

    for name in field_names:
        stats = per_field[name]
        total = int(stats["total"])
        correct = int(stats["correct"])
        stats["accuracy"] = (correct / total) if total else 0.0

    n_pairs = len(pairs)
    predicted_count = sum(1 for pred, _ in pairs if pred is not None)
    expected_count = sum(1 for _, exp in pairs if exp is not None)

    return {
        "fields": per_field,
        "overall_field_accuracy": (
            (overall_correct / overall_total) if overall_total else 0.0
        ),
        "record_count_predicted": predicted_count,
        "record_count_expected": expected_count,
        "exact_record_match_ratio": (exact_matches / n_pairs) if n_pairs else 0.0,
    }


def _align_by_index(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    length = max(len(predicted), len(expected))
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    for index in range(length):
        pred = predicted[index] if index < len(predicted) else None
        exp = expected[index] if index < len(expected) else None
        pairs.append((pred, exp))
    return pairs


def _align_by_key(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    key_field: str,
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    pred_by_key: dict[str, dict[str, Any]] = {}
    for record in predicted:
        key = _key_value(record, key_field)
        if key is not None and key not in pred_by_key:
            pred_by_key[key] = record

    exp_by_key: dict[str, dict[str, Any]] = {}
    for record in expected:
        key = _key_value(record, key_field)
        if key is not None and key not in exp_by_key:
            exp_by_key[key] = record

    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    seen: set[str] = set()

    for key, exp in exp_by_key.items():
        pairs.append((pred_by_key.get(key), exp))
        seen.add(key)

    for key, pred in pred_by_key.items():
        if key not in seen:
            pairs.append((pred, None))

    return pairs


def _key_value(record: dict[str, Any], key_field: str) -> str | None:
    if key_field not in record:
        return None
    value = record[key_field]
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _field_correct(
    predicted: dict[str, Any] | None,
    expected: dict[str, Any] | None,
    name: str,
    field_type: str,
) -> bool:
    if predicted is None or expected is None:
        return False
    if name not in predicted or name not in expected:
        return False
    return values_match(predicted[name], expected[name], field_type)


def _parse_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = _CURRENCY_RE.sub("", text)
    text = _THOUSANDS_RE.sub("", text)
    text = _WHITESPACE_RE.sub("", text)
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _numbers_match(pred: Any, exp: Any) -> bool:
    left = _parse_number(pred)
    right = _parse_number(exp)
    if left is None or right is None:
        return False
    return math.isclose(left, right, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def _parse_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        as_float = float(text)
    except ValueError:
        return None
    if not math.isfinite(as_float) or not as_float.is_integer():
        return None
    return int(as_float)


def _integers_match(pred: Any, exp: Any) -> bool:
    left = _parse_integer(pred)
    right = _parse_integer(exp)
    if left is None or right is None:
        return False
    return left == right


def _normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    text = str(value).strip().lower()
    if text in _TRUE_TOKENS:
        return True
    if text in _FALSE_TOKENS:
        return False
    return None


def _booleans_match(pred: Any, exp: Any) -> bool:
    left = _normalize_bool(pred)
    right = _normalize_bool(exp)
    if left is None or right is None:
        return False
    return left is right


def _normalize_string(value: Any) -> str:
    return normalize_alphanumeric(str(value))


def _strings_match(pred: Any, exp: Any) -> bool:
    return _normalize_string(pred) == _normalize_string(exp)
