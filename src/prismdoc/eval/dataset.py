"""Eval dataset models and JSON loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from prismdoc.schema import TargetSchema


class EvalCase(BaseModel):
    """One (document, expected records) evaluation case."""

    input_path: str
    expected: list[dict[str, Any]]
    key_field: str | None = None


class EvalDataset(BaseModel):
    """A collection of eval cases sharing a schema and pipeline config."""

    schema: TargetSchema
    config_path: str
    cases: list[EvalCase] = Field(default_factory=list)


def load_dataset(path: str | Path) -> EvalDataset:
    """Load an ``EvalDataset`` from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"Eval dataset must be a JSON object, got {type(data).__name__}"
        )
    return EvalDataset.model_validate(data)
