"""Run eval cases through a pipeline and aggregate accuracy reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from prismdoc.config import load_pipeline
from prismdoc.eval.dataset import EvalCase, EvalDataset
from prismdoc.eval.metrics import align_records, field_metrics
from prismdoc.models import Document, Source
from prismdoc.pipeline import Pipeline
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.cascade import CascadeStage
from prismdoc.stages.extract import ExtractStage, LLMClient


class CaseResult(BaseModel):
    """Metrics and router info for one eval case."""

    input_path: str
    metrics: dict[str, Any]
    router: Any | None = None
    tier: str | None = None
    cost: dict[str, Any] | None = None


class EvalReport(BaseModel):
    """Per-case results plus dataset-level aggregates."""

    case_results: list[CaseResult] = Field(default_factory=list)
    case_count: int = 0
    overall_field_accuracy: float = 0.0
    per_field_accuracy: dict[str, float] = Field(default_factory=dict)
    escalation_count: int = 0
    total_usd: float = 0.0


def run_case(
    pipeline: Pipeline,
    ctx: Context,
    case: EvalCase,
    schema: TargetSchema,
) -> CaseResult:
    """Run one case: pipeline → align → field metrics (+ router tier if present)."""
    doc = Document(source=Source(path=str(Path(case.input_path))))
    doc = pipeline.run(doc, ctx)

    predicted = [dict(record.fields) for record in doc.records]
    pairs = align_records(predicted, case.expected, case.key_field)
    metrics = field_metrics(pairs, schema)

    router = doc.artifacts.get("router")
    tier = _tier_from_router(router)
    cost_raw = doc.artifacts.get("cost")
    cost = cost_raw if isinstance(cost_raw, dict) else None
    return CaseResult(
        input_path=case.input_path,
        metrics=metrics,
        router=router,
        tier=tier,
        cost=cost,
    )


def run_eval(
    dataset: EvalDataset,
    client: LLMClient | None = None,
) -> EvalReport:
    """Load the dataset pipeline, run every case, and aggregate metrics.

    When ``client`` is provided it is injected into any ``ExtractStage`` in the
    pipeline (including cascade primary/fallback extract stages).
    """
    pipeline, ctx = load_pipeline(dataset.config_path)
    if client is not None:
        _inject_client(pipeline.stages, client)

    schema = dataset.schema
    case_results: list[CaseResult] = []
    for case in dataset.cases:
        case_results.append(run_case(pipeline, ctx, case, schema))

    return _aggregate(case_results, schema)


def _inject_client(stages: list[Stage], client: LLMClient) -> None:
    for stage in stages:
        if isinstance(stage, ExtractStage):
            stage.client = client
        elif isinstance(stage, CascadeStage):
            _inject_client([stage.primary, stage.fallback], client)


def _tier_from_router(router: Any) -> str | None:
    if not isinstance(router, list) or not router:
        return None
    last = router[-1]
    if isinstance(last, dict):
        tier = last.get("tier")
        return str(tier) if tier is not None else None
    return None


def _case_escalated(result: CaseResult) -> bool:
    router = result.router
    if not isinstance(router, list):
        return result.tier == "fallback"
    return any(
        isinstance(entry, dict) and entry.get("tier") == "fallback"
        for entry in router
    )


def _aggregate(case_results: list[CaseResult], schema: TargetSchema) -> EvalReport:
    case_count = len(case_results)
    if case_count == 0:
        return EvalReport(
            case_results=[],
            case_count=0,
            overall_field_accuracy=0.0,
            per_field_accuracy={name: 0.0 for name in schema.field_names()},
            escalation_count=0,
            total_usd=0.0,
        )

    mean_overall = (
        sum(float(r.metrics.get("overall_field_accuracy", 0.0)) for r in case_results)
        / case_count
    )

    field_correct: dict[str, int] = {name: 0 for name in schema.field_names()}
    field_total: dict[str, int] = {name: 0 for name in schema.field_names()}
    for result in case_results:
        fields = result.metrics.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        for name in schema.field_names():
            stats = fields.get(name) or {}
            if not isinstance(stats, dict):
                continue
            field_correct[name] += int(stats.get("correct", 0))
            field_total[name] += int(stats.get("total", 0))

    per_field_accuracy = {
        name: (
            (field_correct[name] / field_total[name]) if field_total[name] else 0.0
        )
        for name in schema.field_names()
    }

    escalation_count = sum(1 for result in case_results if _case_escalated(result))
    total_usd = sum(
        float((result.cost or {}).get("total_usd", 0.0)) for result in case_results
    )

    return EvalReport(
        case_results=case_results,
        case_count=case_count,
        overall_field_accuracy=mean_overall,
        per_field_accuracy=per_field_accuracy,
        escalation_count=escalation_count,
        total_usd=total_usd,
    )
