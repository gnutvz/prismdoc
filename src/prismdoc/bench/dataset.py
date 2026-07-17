"""Benchmark dataset models and manifest loader."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class BenchSample(BaseModel):
    """One labeled receipt image with ground-truth entity values."""

    image_path: str
    fields: dict[str, str] = Field(default_factory=dict)


def load_manifest(path: str | Path) -> list[BenchSample]:
    """Load a JSON list of ``{"image": ..., "fields": {...}}`` entries.

    Relative ``image`` paths are resolved against the manifest file's directory.
    """
    manifest_path = Path(path)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"Bench manifest must be a JSON list, got {type(raw).__name__}"
        )

    base_dir = manifest_path.resolve().parent
    samples: list[BenchSample] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"Manifest entry {i} must be an object, got {type(item).__name__}"
            )
        image = item.get("image")
        fields = item.get("fields")
        if not isinstance(image, str):
            raise ValueError(f"Manifest entry {i}: 'image' must be a string")
        if not isinstance(fields, dict):
            raise ValueError(f"Manifest entry {i}: 'fields' must be an object")
        image_path = Path(image)
        if not image_path.is_absolute():
            image_path = base_dir / image_path
        samples.append(
            BenchSample(
                image_path=str(image_path.resolve()),
                fields={str(k): str(v) for k, v in fields.items()},
            )
        )
    return samples
