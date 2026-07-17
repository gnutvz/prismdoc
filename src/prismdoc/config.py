"""Load a Pipeline + Context from YAML or a config dict."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import yaml

from prismdoc.pipeline import Pipeline
from prismdoc.registry import create, get_factory
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage


def load_pipeline(path: str | Path) -> tuple[Pipeline, Context]:
    """Read YAML from ``path`` and build a runnable ``Pipeline`` + ``Context``."""
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(
            f"Pipeline config must be a mapping, got {type(data).__name__}"
        )
    return build_pipeline(data)


def build_pipeline(config: dict[str, Any]) -> tuple[Pipeline, Context]:
    """Build a ``Pipeline`` + ``Context`` from an in-memory config dict."""
    _ensure_plugins()

    if "pipeline" not in config:
        raise ValueError("Pipeline config missing required key 'pipeline'")

    pipeline_items = config["pipeline"]
    if not isinstance(pipeline_items, list):
        raise ValueError(
            f"'pipeline' must be a list, got {type(pipeline_items).__name__}"
        )

    target_schema = _build_target_schema(config.get("schema"))
    stages: list[Stage] = []
    for index, item in enumerate(pipeline_items):
        key, params = _resolve_pipeline_item(item, index)
        params = dict(params)
        factory = get_factory(key)
        if (
            "schema" in inspect.signature(factory).parameters
            and "schema" not in params
        ):
            params["schema"] = target_schema
        stages.append(create(key, **params))

    return Pipeline(stages), Context(target_schema=target_schema)


def _build_target_schema(schema_cfg: Any) -> TargetSchema:
    if schema_cfg is None:
        return TargetSchema()
    if not isinstance(schema_cfg, dict):
        raise ValueError(
            f"'schema' must be a mapping, got {type(schema_cfg).__name__}"
        )
    fields_cfg = schema_cfg.get("fields", [])
    if fields_cfg is None:
        fields_cfg = []
    if not isinstance(fields_cfg, list):
        raise ValueError(
            f"'schema.fields' must be a list, got {type(fields_cfg).__name__}"
        )
    return TargetSchema(
        fields=[FieldSpec.model_validate(field) for field in fields_cfg]
    )


def _resolve_pipeline_item(
    item: Any, index: int
) -> tuple[str, dict[str, Any]]:
    if isinstance(item, str):
        return item, {}
    if isinstance(item, dict):
        if len(item) != 1:
            raise ValueError(
                f"Pipeline item[{index}] must be a string or a single-key "
                f"mapping, got {len(item)} keys: {sorted(item)!r}"
            )
        key, params = next(iter(item.items()))
        if not isinstance(key, str):
            raise ValueError(
                f"Pipeline item[{index}] key must be a string, "
                f"got {type(key).__name__}"
            )
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError(
                f"Pipeline item[{index}] params for {key!r} must be a "
                f"mapping, got {type(params).__name__}"
            )
        return key, params
    raise ValueError(
        f"Pipeline item[{index}] must be a string or a single-key mapping, "
        f"got {type(item).__name__}"
    )


def _ensure_plugins() -> None:
    """Re-register default stage factories (safe after ``registry.clear()``)."""
    from prismdoc.stages.extract import register_plugins as register_extract
    from prismdoc.stages.ingest import register_plugins as register_ingest
    from prismdoc.stages.normalize import register_plugins as register_normalize
    from prismdoc.stages.parse import register_plugins as register_parse
    from prismdoc.stages.table_extract import register_plugins as register_table_extract
    from prismdoc.stages.validate import register_plugins as register_validate

    register_ingest()
    register_parse()
    register_extract()
    register_table_extract()
    register_validate()
    register_normalize()
