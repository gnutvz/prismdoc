"""Tests for T-030 cross-field business-rule validation."""

from __future__ import annotations

import prismdoc
from prismdoc import (
    Context,
    Document,
    Record,
    RuleValidateStage,
    Source,
    build_pipeline,
    get_rule,
    register_rule,
    registry,
)
from prismdoc.stages.rules import register_plugins as register_rules


def _doc(*field_dicts: dict) -> Document:
    return Document(
        source=Source(path="/tmp/rules.md"),
        records=[Record(fields=dict(fields)) for fields in field_dicts],
    )


def test_sum_equals_pass_and_mapping_error() -> None:
    stage = RuleValidateStage(
        rules=[
            {
                "type": "sum_equals",
                "fields": ["subtotal", "tax"],
                "target": "total",
                "tolerance": 0.01,
            }
        ]
    )

    ok = stage.run(
        _doc({"subtotal": 100, "tax": 10, "total": 110}),
        Context(),
    )
    assert ok.artifacts["rule_violations"] == []
    assert ok.artifacts["rules"] == {"checked": 1, "violations": 0}

    bad = stage.run(
        _doc({"subtotal": 110, "tax": 10, "total": 110}),
        Context(),
    )
    assert bad.artifacts["rules"]["violations"] == 1
    assert bad.artifacts["rule_violations"] == [
        {
            "record": 0,
            "rule": "sum_equals",
            "detail": bad.artifacts["rule_violations"][0]["detail"],
        }
    ]
    detail = bad.artifacts["rule_violations"][0]["detail"]
    assert "sum" in detail.lower() or "total" in detail


def test_in_set_pass_and_violate() -> None:
    stage = RuleValidateStage(
        rules=[{"type": "in_set", "field": "currency", "values": ["USD", "EUR"]}]
    )

    ok = stage.run(_doc({"currency": "USD"}), Context())
    assert ok.artifacts["rule_violations"] == []

    # case-insensitive
    ok_case = stage.run(_doc({"currency": "usd"}), Context())
    assert ok_case.artifacts["rule_violations"] == []

    bad = stage.run(_doc({"currency": "XYZ"}), Context())
    assert len(bad.artifacts["rule_violations"]) == 1
    assert bad.artifacts["rule_violations"][0]["rule"] == "in_set"


def test_range_and_non_negative() -> None:
    stage = RuleValidateStage(
        rules=[
            {"type": "range", "field": "qty", "min": 1, "max": 100},
            {"type": "non_negative", "field": "price"},
        ]
    )

    ok = stage.run(_doc({"qty": 5, "price": 0}), Context())
    assert ok.artifacts["rule_violations"] == []

    bad = stage.run(_doc({"qty": 0, "price": -1.5}), Context())
    rules = {v["rule"] for v in bad.artifacts["rule_violations"]}
    assert rules == {"range", "non_negative"}
    assert bad.artifacts["rules"]["checked"] == 2
    assert bad.artifacts["rules"]["violations"] == 2


def test_missing_and_non_numeric_produce_clear_violation() -> None:
    stage = RuleValidateStage(
        rules=[
            {
                "type": "sum_equals",
                "fields": ["subtotal", "tax"],
                "target": "total",
            },
            {"type": "range", "field": "qty", "min": 0},
            {"type": "non_negative", "field": "price"},
            {"type": "in_set", "field": "currency", "values": ["USD"]},
        ]
    )
    result = stage.run(
        _doc(
            {
                "subtotal": 100,
                "tax": "abc",
                "total": 110,
                "qty": "n/a",
                # price missing
                # currency missing
            }
        ),
        Context(),
    )
    violations = result.artifacts["rule_violations"]
    assert len(violations) == 4
    by_rule = {v["rule"]: v["detail"] for v in violations}
    assert "missing/non-numeric" in by_rule["sum_equals"]
    assert "tax" in by_rule["sum_equals"]
    assert "missing/non-numeric" in by_rule["range"]
    assert "missing/non-numeric" in by_rule["non_negative"]
    assert "missing" in by_rule["in_set"]
    assert result.artifacts["rules"] == {"checked": 4, "violations": 4}


def test_stage_aggregates_multiple_rules_and_records() -> None:
    stage = RuleValidateStage(
        rules=[
            {
                "type": "sum_equals",
                "fields": ["subtotal", "tax"],
                "target": "total",
            },
            {"type": "in_set", "field": "currency", "values": ["USD", "EUR"]},
        ]
    )
    result = stage.run(
        _doc(
            {"subtotal": 100, "tax": 10, "total": 110, "currency": "USD"},
            {"subtotal": 110, "tax": 10, "total": 110, "currency": "XYZ"},
            {"subtotal": 50, "tax": 5, "total": 55, "currency": "EUR"},
        ),
        Context(),
    )
    assert result.artifacts["rules"] == {"checked": 6, "violations": 2}
    records = {v["record"] for v in result.artifacts["rule_violations"]}
    assert records == {1}
    rules = {v["rule"] for v in result.artifacts["rule_violations"]}
    assert rules == {"sum_equals", "in_set"}


def test_config_builds_rules_default_from_yaml() -> None:
    config = {
        "schema": {"fields": []},
        "pipeline": [
            {
                "rules.default": {
                    "rules": [
                        {
                            "type": "sum_equals",
                            "fields": ["subtotal", "tax"],
                            "target": "total",
                            "tolerance": 0.01,
                        },
                        {
                            "type": "in_set",
                            "field": "currency",
                            "values": ["USD", "EUR"],
                        },
                    ]
                }
            }
        ],
    }
    pipeline, _ = build_pipeline(config)
    assert len(pipeline.stages) == 1
    stage = pipeline.stages[0]
    assert isinstance(stage, RuleValidateStage)
    assert len(stage.rules) == 2
    assert stage.rules[0]["type"] == "sum_equals"
    assert stage.rules[1]["type"] == "in_set"

    result = stage.run(
        _doc({"subtotal": 110, "tax": 10, "total": 110, "currency": "USD"}),
        Context(),
    )
    assert result.artifacts["rules"]["violations"] == 1
    assert result.artifacts["rule_violations"][0]["rule"] == "sum_equals"


def test_exports_and_registry() -> None:
    register_rules()
    assert "rules.default" in registry.get_keys()
    stage = registry.create(
        "rules.default",
        rules=[{"type": "non_negative", "field": "amount"}],
    )
    assert isinstance(stage, RuleValidateStage)

    assert callable(prismdoc.RuleValidateStage)
    assert callable(prismdoc.register_rule)
    assert callable(prismdoc.get_rule)
    assert get_rule("sum_equals") is not None
    register_rule("sum_equals", get_rule("sum_equals"))  # idempotent
