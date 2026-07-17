"""Tests for T-010 cost-aware CascadeStage."""

from __future__ import annotations

from pathlib import Path

from prismdoc import (
    CascadeStage,
    Context,
    Document,
    Page,
    ParseStage,
    Record,
    Source,
    TargetSchema,
    ValidateStage,
    build_pipeline,
    get_scorer,
    load_pipeline,
    register_scorer,
)
from prismdoc.schema import FieldSpec
from prismdoc.stages.base import Stage
from prismdoc.stages.cascade import required_fill_ratio_for, text_length
from prismdoc.stages.parse import PassthroughParser

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CASCADE_YAML = _REPO_ROOT / "examples" / "retail" / "pipeline_cascade.yaml"


class _TagStage(Stage):
    """Fake stage that stamps an artifact and optional parsed text."""

    name = "tag"

    def __init__(
        self,
        tag: str,
        *,
        parsed: str | None = None,
        records: list[Record] | None = None,
    ) -> None:
        self.tag = tag
        self.parsed = parsed
        self.records = records
        self.calls = 0

    def run(self, doc: Document, ctx: Context) -> Document:
        self.calls += 1
        doc.artifacts["tag"] = self.tag
        if self.parsed is not None:
            doc.artifacts["parsed_markdown"] = self.parsed
        if self.records is not None:
            doc.records = list(self.records)
        return doc


def _doc(*, text: str = "", parsed: str | None = None) -> Document:
    doc = Document(
        source=Source(path="/tmp/sample.txt", mime="text/plain"),
        pages=[Page(index=0, text=text)] if text else [],
    )
    if parsed is not None:
        doc.artifacts["parsed_markdown"] = parsed
    return doc


def test_cascade_keeps_primary_when_score_at_or_above_threshold() -> None:
    primary = _TagStage("primary", parsed="x" * 25)
    fallback = _TagStage("fallback", parsed="fallback-text")
    stage = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=text_length,
        threshold=20.0,
    )

    result = stage.run(_doc(), Context())

    assert primary.calls == 1
    assert fallback.calls == 0
    assert result.artifacts["tag"] == "primary"
    assert result.artifacts["router"] == [
        {"tier": "primary", "score": 25.0, "threshold": 20.0}
    ]


def test_cascade_runs_fallback_when_score_below_threshold() -> None:
    primary = _TagStage("primary", parsed="short")
    fallback = _TagStage("fallback", parsed="from-fallback")
    stage = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=text_length,
        threshold=20.0,
    )

    result = stage.run(_doc(), Context())

    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.artifacts["tag"] == "fallback"
    assert result.artifacts["parsed_markdown"] == "from-fallback"
    assert result.artifacts["router"] == [
        {"tier": "fallback", "score": 5.0, "threshold": 20.0}
    ]


def test_fallback_receives_pre_primary_document_state() -> None:
    """Fallback must re-do the step on the snapshot taken before primary."""

    class _MutatingPrimary(Stage):
        name = "primary"

        def run(self, doc: Document, ctx: Context) -> Document:
            doc.artifacts["mutated_by_primary"] = True
            doc.artifacts["parsed_markdown"] = "tiny"
            return doc

    class _AssertBaseline(Stage):
        name = "fallback"

        def run(self, doc: Document, ctx: Context) -> Document:
            assert "mutated_by_primary" not in doc.artifacts
            assert "parsed_markdown" not in doc.artifacts
            doc.artifacts["parsed_markdown"] = "recovered-by-fallback"
            return doc

    stage = CascadeStage(
        primary=_MutatingPrimary(),
        fallback=_AssertBaseline(),
        scorer=text_length,
        threshold=20.0,
    )
    result = stage.run(_doc(), Context())
    assert result.artifacts["parsed_markdown"] == "recovered-by-fallback"
    assert result.artifacts["router"][0]["tier"] == "fallback"


def test_text_length_scorer_uses_parsed_markdown() -> None:
    doc = _doc(text="ignored-page-text", parsed="  abcde  ")
    assert text_length(doc) == 5.0
    assert get_scorer("text_length")(doc) == 5.0


def test_text_length_falls_back_to_full_text() -> None:
    doc = _doc(text="hello world")
    assert text_length(doc) == float(len("hello world"))


def test_required_fill_ratio_for_schema() -> None:
    schema = TargetSchema(
        fields=[
            FieldSpec(name="name", required=True),
            FieldSpec(name="sku", required=False),
        ]
    )
    scorer = required_fill_ratio_for(schema)
    doc = _doc()
    doc.records = [
        Record(fields={"name": "A", "sku": ""}),
        Record(fields={"name": "", "sku": "1"}),
    ]
    assert scorer(doc) == 0.5


def test_router_appends_across_multiple_cascades() -> None:
    first = CascadeStage(
        primary=_TagStage("p1", parsed="x" * 30),
        fallback=_TagStage("f1"),
        scorer=text_length,
        threshold=20.0,
    )
    second = CascadeStage(
        primary=_TagStage("p2", parsed="y"),
        fallback=_TagStage("f2", parsed="yy" * 15),
        scorer=text_length,
        threshold=10.0,
    )
    doc = first.run(_doc(), Context())
    doc = second.run(doc, Context())
    assert len(doc.artifacts["router"]) == 2
    assert doc.artifacts["router"][0]["tier"] == "primary"
    assert doc.artifacts["router"][1]["tier"] == "fallback"


def test_build_pipeline_cascade_item() -> None:
    config = {
        "schema": {
            "fields": [
                {"name": "name", "type": "string", "required": True},
                {"name": "sku", "type": "string"},
            ]
        },
        "pipeline": [
            "ingest.default",
            {
                "cascade": {
                    "primary": "parse.passthrough",
                    "fallback": "parse.docling",
                    "scorer": "text_length",
                    "threshold": 20,
                }
            },
            "validate.default",
        ],
    }
    pipeline, ctx = build_pipeline(config)
    assert [stage.name for stage in pipeline.stages] == [
        "ingest",
        "cascade",
        "validate",
    ]
    cascade = pipeline.stages[1]
    assert isinstance(cascade, CascadeStage)
    assert isinstance(cascade.primary, ParseStage)
    assert isinstance(cascade.primary.parser, PassthroughParser)
    assert isinstance(cascade.fallback, ParseStage)
    assert cascade.fallback.parser.name == "docling"
    assert cascade.threshold == 20.0
    assert cascade.scorer is get_scorer("text_length")

    validate = pipeline.stages[2]
    assert isinstance(validate, ValidateStage)
    assert ctx.target_schema is not None
    assert validate.schema.field_names() == ctx.target_schema.field_names()


def test_build_pipeline_cascade_required_fill_ratio_injects_schema() -> None:
    config = {
        "schema": {
            "fields": [{"name": "name", "type": "string", "required": True}]
        },
        "pipeline": [
            {
                "cascade": {
                    "primary": "validate.default",
                    "fallback": "validate.default",
                    "scorer": "required_fill_ratio",
                    "threshold": 0.5,
                }
            }
        ],
    }
    pipeline, ctx = build_pipeline(config)
    cascade = pipeline.stages[0]
    assert isinstance(cascade, CascadeStage)
    assert isinstance(cascade.primary, ValidateStage)
    assert cascade.primary.schema.field_names() == ["name"]
    assert ctx.target_schema is not None
    doc = _doc()
    doc.records = [Record(fields={"name": "ok"})]
    assert cascade.scorer(doc) == 1.0


def test_load_pipeline_cascade_example() -> None:
    pipeline, _ = load_pipeline(_CASCADE_YAML)
    assert [stage.name for stage in pipeline.stages] == [
        "ingest",
        "cascade",
        "extract",
        "validate",
        "normalize",
    ]
    cascade = pipeline.stages[1]
    assert isinstance(cascade, CascadeStage)
    assert cascade.threshold == 20.0


def test_cascade_exports_from_prismdoc() -> None:
    import prismdoc

    assert prismdoc.CascadeStage is CascadeStage
    assert callable(prismdoc.register_scorer)
    assert callable(prismdoc.get_scorer)
    register_scorer("text_length", text_length)  # idempotent re-register
