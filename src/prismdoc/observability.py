"""Operational metrics derived from pipeline Document state.

``document_metrics`` / ``aggregate_metrics`` produce structured signals meant to
be fed into the *deployer's* metrics or OpenTelemetry stack. prismdoc does not
ship a dashboard or a metrics backend.
"""

from __future__ import annotations

import math
from typing import Any

from prismdoc.cost import CostLedger
from prismdoc.models import Document


def document_metrics(doc: Document) -> dict[str, Any]:
    """Derive per-document operational signals from ``doc.trace`` and artifacts.

    These values are meant to be fed into the deployer's metrics / OpenTelemetry
    stack; prismdoc does not ship a dashboard or a metrics backend.
    """
    stage_latency_ms: dict[str, float] = {}
    stages_ok = 0
    stages_failed = 0
    total_latency_ms = 0.0

    for entry in doc.trace:
        stage_latency_ms[entry.stage] = (
            stage_latency_ms.get(entry.stage, 0.0) + entry.duration_ms
        )
        total_latency_ms += entry.duration_ms
        if entry.ok:
            stages_ok += 1
        else:
            stages_failed += 1

    return {
        "stage_latency_ms": stage_latency_ms,
        "total_latency_ms": total_latency_ms,
        "stages_ok": stages_ok,
        "stages_failed": stages_failed,
        "escalated": _is_escalated(doc),
        "records": len(doc.records),
        "low_confidence": _count_list(doc.artifacts.get("low_confidence")),
        "rule_violations": _count_list(doc.artifacts.get("rule_violations")),
        "repaired_fields": _repaired_field_count(doc.artifacts.get("repair")),
        **_cost_figures(doc.artifacts.get("cost")),
    }


def aggregate_metrics(metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate ``document_metrics`` results across many documents."""
    n = len(metrics_list)
    if n == 0:
        return {
            "n_documents": 0,
            "escalation_rate": 0.0,
            "violation_rate": 0.0,
            "low_confidence_rate": 0.0,
            "stage_latency_ms": {},
            "total_cost_usd": 0.0,
            "mean_tokens": 0.0,
        }

    escalated = sum(1 for m in metrics_list if m.get("escalated"))
    with_violations = sum(
        1 for m in metrics_list if int(m.get("rule_violations") or 0) >= 1
    )
    low_confidence_sum = sum(float(m.get("low_confidence") or 0) for m in metrics_list)
    total_cost = sum(float(m.get("cost_usd") or 0.0) for m in metrics_list)
    total_tokens = sum(
        float(m.get("tokens_in") or 0) + float(m.get("tokens_out") or 0)
        for m in metrics_list
    )

    latencies_by_stage: dict[str, list[float]] = {}
    for m in metrics_list:
        stage_map = m.get("stage_latency_ms") or {}
        if not isinstance(stage_map, dict):
            continue
        for stage, duration in stage_map.items():
            latencies_by_stage.setdefault(stage, []).append(float(duration))

    stage_latency_ms: dict[str, dict[str, float]] = {}
    for stage, values in latencies_by_stage.items():
        stage_latency_ms[stage] = {
            "mean": sum(values) / len(values),
            "p95": _percentile_nearest_rank(values, 0.95),
        }

    return {
        "n_documents": n,
        "escalation_rate": escalated / n,
        "violation_rate": with_violations / n,
        "low_confidence_rate": low_confidence_sum / n,
        "stage_latency_ms": stage_latency_ms,
        "total_cost_usd": total_cost,
        "mean_tokens": total_tokens / n,
    }


def _is_escalated(doc: Document) -> bool:
    router = doc.artifacts.get("router")
    if not isinstance(router, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("tier") == "fallback" for entry in router
    )


def _count_list(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return 0


def _repaired_field_count(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    total = 0
    for entry in value:
        if not isinstance(entry, dict):
            continue
        fields = entry.get("fields")
        if isinstance(fields, list):
            total += len(fields)
    return total


def _cost_figures(cost: Any) -> dict[str, float | int]:
    if isinstance(cost, CostLedger):
        return {
            "tokens_in": cost.tokens_in,
            "tokens_out": cost.tokens_out,
            "cost_usd": cost.total_usd,
        }
    return {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
    }


def _percentile_nearest_rank(values: list[float], p: float) -> float:
    """Nearest-rank percentile: rank = ceil(p * n), 1-indexed into sorted values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[rank - 1]
