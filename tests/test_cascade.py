"""Tests for T-010 cost-aware CascadeStage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismdoc import (
    CascadeStage,
    Context,
    CostLedger,
    Document,
    ExtractStage,
    LLMClient,
    Page,
    ParseStage,
    Record,
    Source,
    TargetSchema,
    ValidateStage,
    build_pipeline,
    char_validity,
    estimate_cost,
    get_scorer,
    load_pipeline,
    make_composite,
    record_cost,
    register_scorer,
)
from prismdoc.schema import FieldSpec
from prismdoc.stages.base import Stage
from prismdoc.stages.cascade import (
    field_coverage_for,
    grounding_ratio_for,
    required_fill_ratio_for,
    text_length,
    text_sufficiency,
)
from prismdoc.stages.extract import Completion
from prismdoc.stages.parse import PassthroughParser

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CASCADE_YAML = _REPO_ROOT / "examples" / "retail" / "pipeline_cascade.yaml"

_EXTRACT_CANNED = [{"name": "Widget", "sku": "W-1"}]


class _UsageLLMClient(LLMClient):
    """Fake client with fixed response text and token usage."""

    def __init__(
        self,
        response: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
    ) -> None:
        self.response = response
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model = model

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        return Completion(
            text=self.response,
            usage={
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            },
            model=self.model,
        )


def _extract_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
        ]
    )


def _always(score: float):
    def _scorer(_doc: Document) -> float:
        return score

    return _scorer


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
    assert prismdoc.char_validity is char_validity
    assert prismdoc.make_composite is make_composite
    register_scorer("text_length", text_length)  # idempotent re-register


def test_char_validity_high_for_clean_text_low_for_garbage() -> None:
    clean = _doc(parsed="Total: 42.50 USD  Vendor: ACME Corp")
    garbage = _doc(parsed="@#%^&*<>{}[] " * 160)  # ~2000 chars of symbol soup
    assert char_validity(clean) > 0.6
    assert char_validity(garbage) < 0.2
    # Length alone would clear a typical cascade threshold; validity does not.
    assert text_length(garbage) > 20.0
    assert get_scorer("char_validity")(garbage) == char_validity(garbage)


def test_char_validity_empty_is_zero() -> None:
    assert char_validity(_doc(parsed="")) == 0.0
    assert char_validity(_doc(parsed="   \n\t  ")) == 0.0


def test_char_validity_composite_garbage_scores_below_clean() -> None:
    """Long symbol garbage must score lower than short clean under composite."""
    clean = _doc(parsed="Total: 42.50 USD  Vendor: ACME Corp")
    garbage = _doc(parsed="@#%^&*<>{}[] " * 160)
    composite = make_composite(
        [
            {"scorer": "char_validity", "weight": 0.6},
            {"scorer": "text_sufficiency", "weight": 0.4},
        ]
    )
    clean_score = composite(clean)
    garbage_score = composite(garbage)
    assert garbage_score < clean_score
    assert garbage_score < 0.5


def test_text_sufficiency_bounds() -> None:
    assert text_sufficiency(_doc(parsed="")) == 0.0
    assert text_sufficiency(_doc(parsed="x" * 200)) == 1.0
    assert text_sufficiency(_doc(parsed="x" * 100)) == 0.5
    assert text_sufficiency(_doc(parsed="x" * 400)) == 1.0
    assert get_scorer("text_sufficiency")(_doc(parsed="a" * 50)) == 0.25


def test_field_coverage_for_averages_non_empty_schema_fields() -> None:
    schema = TargetSchema(
        fields=[
            FieldSpec(name="name"),
            FieldSpec(name="sku"),
        ]
    )
    scorer = field_coverage_for(schema)
    doc = _doc()
    assert scorer(doc) == 0.0
    doc.records = [
        Record(fields={"name": "A", "sku": ""}),  # 0.5
        Record(fields={"name": "B", "sku": "1"}),  # 1.0
    ]
    assert scorer(doc) == 0.75


def test_grounding_ratio_for_averages_grounded_extracted_values() -> None:
    schema = TargetSchema(
        fields=[
            FieldSpec(name="vendor"),
            FieldSpec(name="total"),
        ]
    )
    scorer = grounding_ratio_for(schema)
    doc = _doc(parsed="Vendor ACME paid total 12.50")
    assert scorer(doc) == 0.0
    doc.records = [
        # both extracted values grounded -> 1.0
        Record(fields={"vendor": "ACME", "total": "12.50"}),
        # one grounded, one hallucinated -> 0.5
        Record(fields={"vendor": "ACME", "total": "999.99"}),
    ]
    assert scorer(doc) == 0.75


def test_make_composite_weighted_combination_and_registry_resolve() -> None:
    def always_half(_doc: Document) -> float:
        return 0.5

    composite = make_composite(
        [
            {"scorer": "char_validity", "weight": 1.0},
            {"scorer": always_half, "weight": 1.0},
        ]
    )
    # Weights 1+1 normalized to 0.5 each; alnum ratio ~0.85 -> ~0.675
    score = composite(_doc(parsed="Hello world, total $10."))
    assert 0.65 < score < 0.75

    # Uneven weights: 3:1 toward always_half -> closer to 0.5
    skewed = make_composite(
        [
            {"scorer": always_half, "weight": 3.0},
            {"scorer": "text_sufficiency", "weight": 1.0},
        ]
    )
    # text_sufficiency("x"*200) = 1.0; weighted = 0.5*0.75 + 1.0*0.25 = 0.625
    assert skewed(_doc(parsed="x" * 200)) == 0.625


def test_make_composite_skips_erroring_component_and_renormalizes() -> None:
    def boom(_doc: Document) -> float:
        raise RuntimeError("scorer failed")

    composite = make_composite(
        [
            {"scorer": boom, "weight": 9.0},
            {"scorer": "text_sufficiency", "weight": 1.0},
        ]
    )
    assert composite(_doc(parsed="x" * 200)) == 1.0


def test_cascade_composite_escalates_long_garbage_unlike_length_only() -> None:
    garbage = "§¶†‡•‰™¡¿¤¢£¥€" * 80  # long OCR junk (no valid alnum/punct)
    assert text_length(_doc(parsed=garbage)) > 200.0
    assert char_validity(_doc(parsed=garbage)) < 0.05

    primary = _TagStage("primary", parsed=garbage)
    fallback = _TagStage("fallback", parsed="recovered clean text " * 20)
    composite = make_composite(
        [
            {"scorer": "char_validity", "weight": 0.7},
            {"scorer": "text_sufficiency", "weight": 0.3},
        ]
    )
    # composite score dominated by low char_validity despite long text
    assert composite(_doc(parsed=garbage)) < 0.5

    length_stage = CascadeStage(
        primary=_TagStage("p-len", parsed=garbage),
        fallback=_TagStage("f-len"),
        scorer=text_length,
        threshold=200.0,
    )
    length_result = length_stage.run(_doc(), Context())
    assert length_result.artifacts["router"][0]["tier"] == "primary"

    quality_stage = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=composite,
        threshold=0.5,
    )
    result = quality_stage.run(_doc(), Context())
    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.artifacts["tag"] == "fallback"
    assert result.artifacts["router"][0]["tier"] == "fallback"


def test_build_pipeline_cascade_composite_scorer_with_schema_component() -> None:
    config = {
        "schema": {
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "sku", "type": "string"},
            ]
        },
        "pipeline": [
            {
                "cascade": {
                    "primary": "validate.default",
                    "fallback": "validate.default",
                    "scorer": {
                        "composite": [
                            {"scorer": "field_coverage", "weight": 0.5},
                            {"scorer": "char_validity", "weight": 0.5},
                        ]
                    },
                    "threshold": 0.5,
                }
            }
        ],
    }
    pipeline, _ctx = build_pipeline(config)
    cascade = pipeline.stages[0]
    assert isinstance(cascade, CascadeStage)

    doc = _doc(parsed="Vendor ACME sku ABC-1")
    doc.records = [Record(fields={"name": "ACME", "sku": "ABC-1"})]
    # field_coverage=1.0, char_validity~1.0 -> composite near 1.0
    assert cascade.scorer(doc) > 0.9

    sparse = _doc(parsed="§¶†‡•‰™¡¿¤¢£¥" * 20)
    sparse.records = [Record(fields={"name": "", "sku": ""})]
    # field_coverage=0.0, char_validity~0 -> composite near 0
    assert cascade.scorer(sparse) < 0.3


# --- T-038: escalated cascade must keep primary + fallback cost ---


def test_escalated_cascade_cost_is_primary_plus_fallback() -> None:
    schema = _extract_schema()
    primary_in, primary_out = 1000, 200
    fallback_in, fallback_out = 2000, 400
    primary_payload = json.dumps([{"name": "Cheap", "sku": "C-1"}])
    fallback_payload = json.dumps([{"name": "Strong", "sku": "S-1"}])
    primary = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            primary_payload,
            prompt_tokens=primary_in,
            completion_tokens=primary_out,
            model="gpt-4o-mini",
        ),
        model="gpt-4o-mini",
    )
    fallback = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            fallback_payload,
            prompt_tokens=fallback_in,
            completion_tokens=fallback_out,
            model="gpt-4o",
        ),
        model="gpt-4o",
    )
    primary_cost = estimate_cost("gpt-4o-mini", primary_in, primary_out)
    fallback_cost = estimate_cost("gpt-4o", fallback_in, fallback_out)
    assert primary_cost is not None and fallback_cost is not None

    result = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=_always(0.0),
        threshold=0.5,
    ).run(_doc(text="Widget W-1 9.99"), Context())

    cost = result.artifacts["cost"]
    assert isinstance(cost, CostLedger)
    assert cost.total_usd == pytest.approx(primary_cost + fallback_cost)
    assert cost.tokens_in == primary_in + fallback_in
    assert cost.tokens_out == primary_out + fallback_out
    assert cost.by_stage["extract"].usd == pytest.approx(primary_cost + fallback_cost)
    assert result.records[0].fields["name"] == "Strong"
    assert result.artifacts["router"][0]["tier"] == "fallback"


def test_escalated_cascade_with_pre_cost_does_not_double_count() -> None:
    """Pre-cascade ledger + primary delta + fallback — pre_cost counted once."""
    schema = _extract_schema()
    pre_in, pre_out = 500, 50
    primary_in, primary_out = 1000, 200
    fallback_in, fallback_out = 2000, 400
    pre_cost = estimate_cost("gpt-4o-mini", pre_in, pre_out)
    primary_cost = estimate_cost("gpt-4o-mini", primary_in, primary_out)
    fallback_cost = estimate_cost("gpt-4o", fallback_in, fallback_out)
    assert pre_cost is not None and primary_cost is not None and fallback_cost is not None

    doc = _doc(text="Widget W-1 9.99")
    record_cost(doc, "parse", "gpt-4o-mini", pre_in, pre_out)

    primary = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            json.dumps([{"name": "Cheap", "sku": "C-1"}]),
            prompt_tokens=primary_in,
            completion_tokens=primary_out,
            model="gpt-4o-mini",
        ),
        model="gpt-4o-mini",
    )
    fallback = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            json.dumps([{"name": "Strong", "sku": "S-1"}]),
            prompt_tokens=fallback_in,
            completion_tokens=fallback_out,
            model="gpt-4o",
        ),
        model="gpt-4o",
    )
    result = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=_always(0.0),
        threshold=0.5,
    ).run(doc, Context())

    cost = result.artifacts["cost"]
    assert isinstance(cost, CostLedger)
    assert cost.total_usd == pytest.approx(pre_cost + primary_cost + fallback_cost)
    assert cost.tokens_in == pre_in + primary_in + fallback_in
    assert cost.tokens_out == pre_out + primary_out + fallback_out
    assert cost.by_stage["parse"].usd == pytest.approx(pre_cost)
    assert cost.by_stage["extract"].usd == pytest.approx(primary_cost + fallback_cost)
    assert result.artifacts["router"][0]["tier"] == "fallback"


def test_non_escalated_cascade_cost_is_primary_only() -> None:
    schema = _extract_schema()
    primary_in, primary_out = 1000, 200
    fallback_in, fallback_out = 2000, 400
    primary = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            json.dumps(_EXTRACT_CANNED),
            prompt_tokens=primary_in,
            completion_tokens=primary_out,
            model="gpt-4o-mini",
        ),
        model="gpt-4o-mini",
    )
    fallback = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            json.dumps([{"name": "Strong", "sku": "S-1"}]),
            prompt_tokens=fallback_in,
            completion_tokens=fallback_out,
            model="gpt-4o",
        ),
        model="gpt-4o",
    )
    primary_cost = estimate_cost("gpt-4o-mini", primary_in, primary_out)
    assert primary_cost is not None

    result = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=_always(1.0),
        threshold=0.5,
    ).run(_doc(text="Widget W-1 9.99"), Context())

    cost = result.artifacts["cost"]
    assert isinstance(cost, CostLedger)
    assert cost.total_usd == pytest.approx(primary_cost)
    assert cost.tokens_in == primary_in
    assert cost.tokens_out == primary_out
    assert result.records[0].fields["name"] == "Widget"
    assert result.artifacts["router"][0]["tier"] == "primary"


def test_passthrough_primary_escalation_keeps_only_fallback_cost() -> None:
    schema = _extract_schema()
    fallback_in, fallback_out = 1500, 300
    fallback_cost = estimate_cost("gpt-4o", fallback_in, fallback_out)
    assert fallback_cost is not None

    primary = ParseStage(parser=PassthroughParser())
    fallback = ExtractStage(
        schema=schema,
        client=_UsageLLMClient(
            json.dumps(_EXTRACT_CANNED),
            prompt_tokens=fallback_in,
            completion_tokens=fallback_out,
            model="gpt-4o",
        ),
        model="gpt-4o",
    )
    result = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=_always(0.0),
        threshold=0.5,
    ).run(_doc(text="short"), Context())

    cost = result.artifacts["cost"]
    assert isinstance(cost, CostLedger)
    assert cost.total_usd == pytest.approx(fallback_cost)
    assert cost.tokens_in == fallback_in
    assert cost.tokens_out == fallback_out
    assert result.artifacts["router"][0]["tier"] == "fallback"


def test_escalation_records_and_markdown_still_come_from_fallback() -> None:
    primary = _TagStage(
        "primary",
        parsed="primary-markdown",
        records=[Record(fields={"name": "from-primary"})],
    )
    fallback = _TagStage(
        "fallback",
        parsed="fallback-markdown",
        records=[Record(fields={"name": "from-fallback"})],
    )
    result = CascadeStage(
        primary=primary,
        fallback=fallback,
        scorer=_always(0.0),
        threshold=0.5,
    ).run(_doc(), Context())

    assert result.artifacts["parsed_markdown"] == "fallback-markdown"
    assert result.records[0].fields["name"] == "from-fallback"
    assert result.artifacts["tag"] == "fallback"
    assert "cost" not in result.artifacts
