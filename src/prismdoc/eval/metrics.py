"""Record alignment and per-field accuracy metrics."""

from __future__ import annotations

from typing import Any

from prismdoc.schema import TargetSchema


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
    ``str(value).strip()`` matches. Missing predicted or expected counts as
    incorrect.
    """
    field_names = schema.field_names()
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
            if _field_correct(predicted, expected, name):
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
) -> bool:
    if predicted is None or expected is None:
        return False
    if name not in predicted or name not in expected:
        return False
    pred_val = predicted[name]
    exp_val = expected[name]
    if pred_val is None or exp_val is None:
        return False
    return str(pred_val).strip() == str(exp_val).strip()
