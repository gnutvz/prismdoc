"""Tests for T-001 core contracts: models, Stage, Pipeline, registry."""

from __future__ import annotations

import pytest

from prismdoc import (
    Block,
    Context,
    Document,
    Page,
    Pipeline,
    Record,
    Source,
    Stage,
    registry,
)
from prismdoc.models import TraceEntry


class EchoStage(Stage):
    """Fake stage that appends a marker to artifacts."""

    name = "echo"

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def run(self, doc: Document, ctx: Context) -> Document:
        markers = list(doc.artifacts.get("echo", []))
        markers.append(self.marker)
        doc.artifacts["echo"] = markers
        return doc


class BoomStage(Stage):
    """Fake stage that always fails."""

    name = "boom"

    def run(self, doc: Document, ctx: Context) -> Document:
        raise ValueError("intentional failure")


def _sample_doc() -> Document:
    return Document(
        source=Source(path="/tmp/sample.pdf", mime="application/pdf"),
        pages=[
            Page(index=0, text="Hello ", blocks=[Block(text="Hello")]),
            Page(index=1, text="World"),
        ],
    )


def test_document_add_trace_and_full_text() -> None:
    doc = _sample_doc()
    assert doc.full_text == "Hello \nWorld"

    entry = TraceEntry(stage="manual", ok=True, duration_ms=1.5)
    doc.add_trace(entry)
    assert len(doc.trace) == 1
    assert doc.trace[0].stage == "manual"
    assert doc.trace[0].ok is True
    assert doc.trace[0].duration_ms == 1.5

    # Ensure nested models construct cleanly
    assert isinstance(doc.records, list)
    assert Record(fields={"sku": "A1"}).fields["sku"] == "A1"


def test_pipeline_runs_stages_and_records_trace() -> None:
    doc = _sample_doc()
    ctx = Context()
    pipeline = Pipeline([EchoStage("one"), EchoStage("two")])

    result = pipeline.run(doc, ctx)

    assert result.artifacts["echo"] == ["one", "two"]
    assert len(result.trace) == 2
    assert [t.stage for t in result.trace] == ["echo", "echo"]
    assert all(t.ok for t in result.trace)
    assert all(isinstance(t.duration_ms, float) for t in result.trace)
    assert all(t.duration_ms >= 0 for t in result.trace)


def test_pipeline_records_failure_and_reraises() -> None:
    doc = _sample_doc()
    ctx = Context()
    pipeline = Pipeline([EchoStage("before"), BoomStage()])

    with pytest.raises(RuntimeError, match="Stage boom failed") as exc_info:
        pipeline.run(doc, ctx)

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert len(doc.trace) == 2
    assert doc.trace[0].ok is True
    assert doc.trace[0].stage == "echo"
    assert doc.trace[1].ok is False
    assert doc.trace[1].stage == "boom"
    assert doc.trace[1].error == "intentional failure"
    assert doc.trace[1].duration_ms >= 0


def test_registry_register_create_get_keys() -> None:
    # Isolate from other tests that may register keys
    registry.clear()

    registry.register("parser.echo", lambda marker="x": EchoStage(marker=marker))
    assert registry.get_keys() == ["parser.echo"]

    stage = registry.create("parser.echo", marker="via-registry")
    assert isinstance(stage, EchoStage)
    assert stage.marker == "via-registry"

    with pytest.raises(KeyError, match="Unknown stage key"):
        registry.create("parser.missing")
