"""Tests for T-034 observability signals (document_metrics / aggregate_metrics)."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import prismdoc
import pytest
from fastapi.testclient import TestClient

from prismdoc import (
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    IngestStage,
    LLMClient,
    NormalizeStage,
    ParseStage,
    Pipeline,
    Record,
    Source,
    TargetSchema,
    ValidateStage,
    aggregate_metrics,
    document_metrics,
)
from prismdoc.api.app import app, get_runtime
from prismdoc.cost import CostLedger
from prismdoc.models import TraceEntry
from prismdoc.stages.extract import Completion


def test_document_metrics_full_artifacts() -> None:
    ledger = CostLedger()
    ledger.add("extract", "gpt-4o-mini", tokens_in=100, tokens_out=40, usd=0.012)

    doc = Document(
        source=Source(path="sample.pdf"),
        records=[
            Record(fields={"name": "A"}),
            Record(fields={"name": "B"}),
        ],
        artifacts={
            "router": [
                {"tier": "primary", "score": 0.2},
                {"tier": "fallback", "score": 0.9},
            ],
            "low_confidence": [
                {"record": 0, "field": "sku"},
                {"record": 1, "field": "price"},
            ],
            "rule_violations": [
                {"rule": "required", "record": 0, "field": "sku"},
            ],
            "repair": [
                {"record": 0, "round": 1, "fields": ["sku", "price"]},
                {"record": 1, "round": 1, "fields": ["qty"]},
            ],
            "cost": ledger,
        },
        trace=[
            TraceEntry(stage="ingest", ok=True, duration_ms=10.0),
            TraceEntry(stage="parse", ok=True, duration_ms=20.0),
            TraceEntry(stage="extract", ok=True, duration_ms=30.0),
            TraceEntry(stage="extract", ok=False, duration_ms=5.0),
            TraceEntry(stage="validate", ok=True, duration_ms=15.0),
        ],
    )

    metrics = document_metrics(doc)

    assert metrics["stage_latency_ms"] == {
        "ingest": 10.0,
        "parse": 20.0,
        "extract": 35.0,
        "validate": 15.0,
    }
    assert metrics["total_latency_ms"] == 80.0
    assert metrics["stages_ok"] == 4
    assert metrics["stages_failed"] == 1
    assert metrics["escalated"] is True
    assert metrics["records"] == 2
    assert metrics["low_confidence"] == 2
    assert metrics["rule_violations"] == 1
    assert metrics["repaired_fields"] == 3
    assert metrics["tokens_in"] == 100
    assert metrics["tokens_out"] == 40
    assert metrics["cost_usd"] == pytest.approx(0.012)


def test_document_metrics_bare_doc() -> None:
    doc = Document(source=Source(path="empty.pdf"))

    metrics = document_metrics(doc)

    assert metrics["stage_latency_ms"] == {}
    assert metrics["total_latency_ms"] == 0.0
    assert metrics["stages_ok"] == 0
    assert metrics["stages_failed"] == 0
    assert metrics["escalated"] is False
    assert metrics["records"] == 0
    assert metrics["low_confidence"] == 0
    assert metrics["rule_violations"] == 0
    assert metrics["repaired_fields"] == 0
    assert metrics["tokens_in"] == 0
    assert metrics["tokens_out"] == 0
    assert metrics["cost_usd"] == 0.0


def test_aggregate_metrics_rates_and_p95() -> None:
    # Known latencies for stage "extract": [10, 20, 30, 40]
    # nearest-rank p95: ceil(0.95 * 4) = 4 -> 40
    metrics_list = [
        {
            "escalated": True,
            "rule_violations": 2,
            "low_confidence": 1,
            "stage_latency_ms": {"extract": 10.0, "ingest": 5.0},
            "tokens_in": 10,
            "tokens_out": 5,
            "cost_usd": 0.01,
        },
        {
            "escalated": False,
            "rule_violations": 0,
            "low_confidence": 3,
            "stage_latency_ms": {"extract": 20.0, "ingest": 7.0},
            "tokens_in": 20,
            "tokens_out": 10,
            "cost_usd": 0.02,
        },
        {
            "escalated": True,
            "rule_violations": 1,
            "low_confidence": 0,
            "stage_latency_ms": {"extract": 30.0, "ingest": 9.0},
            "tokens_in": 30,
            "tokens_out": 15,
            "cost_usd": 0.03,
        },
        {
            "escalated": False,
            "rule_violations": 0,
            "low_confidence": 4,
            "stage_latency_ms": {"extract": 40.0, "ingest": 11.0},
            "tokens_in": 40,
            "tokens_out": 20,
            "cost_usd": 0.04,
        },
    ]

    agg = aggregate_metrics(metrics_list)

    assert agg["n_documents"] == 4
    assert agg["escalation_rate"] == pytest.approx(0.5)
    assert agg["violation_rate"] == pytest.approx(0.5)
    assert agg["low_confidence_rate"] == pytest.approx(2.0)  # (1+3+0+4)/4
    assert agg["stage_latency_ms"]["extract"]["mean"] == pytest.approx(25.0)
    assert agg["stage_latency_ms"]["extract"]["p95"] == pytest.approx(40.0)
    assert agg["stage_latency_ms"]["ingest"]["mean"] == pytest.approx(8.0)
    assert agg["stage_latency_ms"]["ingest"]["p95"] == pytest.approx(11.0)
    assert agg["total_cost_usd"] == pytest.approx(0.10)
    assert agg["mean_tokens"] == pytest.approx(37.5)  # (15+30+45+60)/4


def test_observability_exported_from_prismdoc() -> None:
    assert prismdoc.document_metrics is document_metrics
    assert prismdoc.aggregate_metrics is aggregate_metrics


class _FakeLLMClient(LLMClient):
    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        return Completion(
            text=json.dumps([{"name": "Widget A", "sku": "W-001", "price": 9.99}])
        )


def _offline_runtime() -> tuple[Pipeline, Context]:
    schema = TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string"),
            FieldSpec(name="price", type="number"),
        ]
    )
    pipeline = Pipeline(
        [
            IngestStage(),
            ParseStage(),
            ExtractStage(schema=schema, client=_FakeLLMClient()),
            ValidateStage(schema=schema),
            NormalizeStage(),
        ]
    )
    return pipeline, Context(target_schema=schema)


def test_extract_response_includes_metrics(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Widget A W-001 9.99")
    pdf.save(pdf_path)
    pdf.close()

    app.dependency_overrides[get_runtime] = _offline_runtime
    try:
        with TestClient(app) as client:
            with pdf_path.open("rb") as handle:
                response = client.post(
                    "/extract",
                    files={"file": ("catalog.pdf", handle, "application/pdf")},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert "metrics" in body
    metrics = body["metrics"]
    assert isinstance(metrics, dict)
    assert "stage_latency_ms" in metrics
    assert "total_latency_ms" in metrics
    assert metrics["stages_ok"] >= 1
    assert metrics["escalated"] is False
    assert metrics["records"] == 1
    assert "tokens_in" in metrics
    assert "tokens_out" in metrics
    assert "cost_usd" in metrics
