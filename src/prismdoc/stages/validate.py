"""Validate stage: enforce target schema (required fields + type coercion)."""

from __future__ import annotations

from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage

_TRUE_VALUES = frozenset({"true", "1", "yes"})
_FALSE_VALUES = frozenset({"false", "0", "no"})


class ValidateStage(Stage):
    """Check required fields and coerce values to the declared schema types."""

    name = "validate"

    def __init__(self, schema: TargetSchema) -> None:
        self.schema = schema

    def run(self, doc: Document, ctx: Context) -> Document:
        errors: list[str] = []
        valid = 0
        invalid = 0

        for index, record in enumerate(doc.records):
            record_errors = _validate_record(record, self.schema, index)
            errors.extend(record_errors)
            if record_errors:
                invalid += 1
            else:
                valid += 1

        doc.artifacts["validation"] = {
            "errors": errors,
            "valid": valid,
            "invalid": invalid,
        }
        return doc


def _validate_record(
    record: Record,
    schema: TargetSchema,
    index: int,
) -> list[str]:
    errors: list[str] = []
    fields = record.fields

    for spec in schema.fields:
        if spec.required and _is_missing_or_empty(fields.get(spec.name)):
            errors.append(
                f"record[{index}].{spec.name}: required field missing or empty"
            )

    for spec in schema.fields:
        if spec.name not in fields:
            continue
        value = fields[spec.name]
        if _is_missing_or_empty(value):
            continue
        coerced, error = _coerce_value(value, spec)
        if error is not None:
            errors.append(f"record[{index}].{spec.name}: {error}")
        else:
            fields[spec.name] = coerced

    return errors


def _is_missing_or_empty(value: Any) -> bool:
    return value is None or value == ""


def _coerce_value(value: Any, spec: FieldSpec) -> tuple[Any, str | None]:
    try:
        if spec.type == "string":
            return str(value), None
        if spec.type == "integer":
            return _coerce_integer(value), None
        if spec.type == "number":
            return _coerce_number(value), None
        if spec.type == "boolean":
            return _coerce_boolean(value), None
    except (TypeError, ValueError) as exc:
        return value, (
            f"cannot coerce {value!r} to {spec.type} ({exc})"
        )
    return value, f"unsupported field type {spec.type!r}"


def _coerce_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"non-integer float {value!r}")
    if isinstance(value, str):
        return int(value.strip())
    raise TypeError(f"unsupported type {type(value).__name__}")


def _coerce_number(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value.strip())
    raise TypeError(f"unsupported type {type(value).__name__}")


def _coerce_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
        raise ValueError(f"unrecognized boolean string {value!r}")
    raise TypeError(f"unsupported type {type(value).__name__}")


def register_plugins() -> None:
    """Register the default validate stage in the plugin registry."""
    register("validate.default", ValidateStage)


register_plugins()
