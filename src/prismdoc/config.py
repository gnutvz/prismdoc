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
from prismdoc.stages.cascade import (
    CascadeStage,
    Scorer,
    field_coverage_for,
    get_scorer,
    grounding_ratio_for,
    make_composite,
    required_fill_ratio_for,
)


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
        if key == "cascade":
            stages.append(
                _build_cascade_stage(params, target_schema, index)
            )
            continue
        stages.append(_instantiate_stage(key, params, target_schema))

    return Pipeline(stages), Context(target_schema=target_schema)


def _instantiate_stage(
    key: str, params: dict[str, Any], target_schema: TargetSchema
) -> Stage:
    params = dict(params)
    factory = get_factory(key)
    if (
        "schema" in inspect.signature(factory).parameters
        and "schema" not in params
    ):
        params["schema"] = target_schema
    stage = create(key, **params)
    if not isinstance(stage, Stage):
        raise TypeError(
            f"Registry key {key!r} did not produce a Stage, "
            f"got {type(stage).__name__}"
        )
    return stage


def _build_cascade_stage(
    params: dict[str, Any],
    target_schema: TargetSchema,
    index: int,
) -> CascadeStage:
    for required in ("primary", "fallback", "scorer", "threshold"):
        if required not in params:
            raise ValueError(
                f"Pipeline item[{index}] cascade missing required key "
                f"{required!r}"
            )
    primary_key = params["primary"]
    fallback_key = params["fallback"]
    scorer_cfg = params["scorer"]
    if not isinstance(primary_key, str):
        raise ValueError(
            f"Pipeline item[{index}] cascade.primary must be a string, "
            f"got {type(primary_key).__name__}"
        )
    if not isinstance(fallback_key, str):
        raise ValueError(
            f"Pipeline item[{index}] cascade.fallback must be a string, "
            f"got {type(fallback_key).__name__}"
        )

    primary = _instantiate_stage(primary_key, {}, target_schema)
    fallback = _instantiate_stage(fallback_key, {}, target_schema)
    scorer = _resolve_cascade_scorer(scorer_cfg, target_schema, index)

    return CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=scorer,
        threshold=float(params["threshold"]),
    )


def _resolve_named_scorer(name: str, target_schema: TargetSchema) -> Scorer:
    """Resolve a scorer name, injecting schema for schema-dependent factories."""
    if name == "required_fill_ratio":
        return required_fill_ratio_for(target_schema)
    if name == "field_coverage":
        return field_coverage_for(target_schema)
    if name in ("grounding", "grounding_ratio"):
        return grounding_ratio_for(target_schema)
    return get_scorer(name)


def _resolve_cascade_scorer(
    scorer_cfg: Any,
    target_schema: TargetSchema,
    index: int,
) -> Scorer:
    """Build a cascade scorer from a string name or composite mapping."""
    if isinstance(scorer_cfg, str):
        return _resolve_named_scorer(scorer_cfg, target_schema)

    if isinstance(scorer_cfg, dict):
        if "composite" not in scorer_cfg or len(scorer_cfg) != 1:
            raise ValueError(
                f"Pipeline item[{index}] cascade.scorer mapping must be "
                f"{{composite: [...]}}, got keys {sorted(scorer_cfg)!r}"
            )
        components_cfg = scorer_cfg["composite"]
        if not isinstance(components_cfg, list) or not components_cfg:
            raise ValueError(
                f"Pipeline item[{index}] cascade.scorer.composite must be "
                f"a non-empty list"
            )
        components: list[dict[str, Any]] = []
        for comp_index, item in enumerate(components_cfg):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Pipeline item[{index}] cascade.scorer.composite"
                    f"[{comp_index}] must be a mapping"
                )
            if "scorer" not in item or "weight" not in item:
                raise ValueError(
                    f"Pipeline item[{index}] cascade.scorer.composite"
                    f"[{comp_index}] must have 'scorer' and 'weight'"
                )
            raw = item["scorer"]
            if isinstance(raw, str):
                resolved: Scorer | str = _resolve_named_scorer(raw, target_schema)
            elif callable(raw):
                resolved = raw
            else:
                raise ValueError(
                    f"Pipeline item[{index}] cascade.scorer.composite"
                    f"[{comp_index}].scorer must be a string name, "
                    f"got {type(raw).__name__}"
                )
            components.append(
                {"scorer": resolved, "weight": float(item["weight"])}
            )
        return make_composite(components)

    raise ValueError(
        f"Pipeline item[{index}] cascade.scorer must be a string or "
        f"{{composite: [...]}} mapping, got {type(scorer_cfg).__name__}"
    )


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
    from prismdoc.stages.cascade import register_plugins as register_cascade
    from prismdoc.stages.chunked_extract import register_plugins as register_chunked
    from prismdoc.stages.confidence import register_plugins as register_confidence
    from prismdoc.stages.ensemble import register_plugins as register_ensemble
    from prismdoc.stages.extract import register_plugins as register_extract
    from prismdoc.stages.figures import register_plugins as register_figures
    from prismdoc.stages.hybrid_extract import register_plugins as register_hybrid
    from prismdoc.stages.ingest import register_plugins as register_ingest
    from prismdoc.stages.normalize import register_plugins as register_normalize
    from prismdoc.stages.parse import register_plugins as register_parse
    from prismdoc.stages.provenance import register_plugins as register_provenance
    from prismdoc.stages.rules import register_plugins as register_rules
    from prismdoc.stages.table_extract import register_plugins as register_table_extract
    from prismdoc.stages.validate import register_plugins as register_validate

    register_ingest()
    register_parse()
    register_figures()
    register_extract()
    register_chunked()
    register_ensemble()
    register_hybrid()
    register_table_extract()
    register_validate()
    register_normalize()
    register_confidence()
    register_provenance()
    register_rules()
    register_cascade()
