"""Tests for T-052 declarative policy engine."""

from __future__ import annotations

import pytest

import prismdoc
from prismdoc import Context, CostLedger, Document, PolicyHaltError, Record, Source
from prismdoc.config import _ensure_plugins
from prismdoc import registry
from prismdoc.stages.policy import PolicyStage, register_plugins


def _doc(
    *,
    confidence: dict[str, float] | None = None,
    artifacts: dict | None = None,
) -> Document:
    return Document(
        source=Source(path="/tmp/policy.md"),
        records=[Record(fields={"total": 10}, confidence=confidence or {})],
        artifacts=dict(artifacts or {}),
    )


def test_flag_review_on_low_confidence() -> None:
    doc = _doc(confidence={"total": 0.3})
    stage = PolicyStage(
        policies=[{"when": {"min_confidence": 0.5}, "action": "flag_review"}]
    )
    result = stage.run(doc, Context())
    assert result.artifacts["policy"]["review"] is True
    assert result.artifacts["policy"]["triggered"] == [
        {"when": {"min_confidence": 0.5}, "action": "flag_review"}
    ]


def test_halt_on_budget() -> None:
    ledger = CostLedger(total_usd=1.0)
    doc = _doc(artifacts={"cost": ledger})
    stage = PolicyStage(
        policies=[{"when": {"max_total_usd": 0.5}, "action": "halt"}]
    )
    with pytest.raises(PolicyHaltError):
        stage.run(doc, Context())
    assert doc.artifacts["policy"]["halted"] is True
    assert doc.artifacts["policy"]["triggered"] == [
        {"when": {"max_total_usd": 0.5}, "action": "halt"}
    ]


def test_tag_on_violations() -> None:
    doc = _doc(
        artifacts={
            "rule_violations": [
                {"record": 0, "rule": "non_negative", "detail": "price < 0"}
            ]
        }
    )
    stage = PolicyStage(
        policies=[
            {
                "when": {"has_rule_violations": True},
                "action": "tag",
                "tag": "needs_fix",
            }
        ]
    )
    result = stage.run(doc, Context())
    assert "needs_fix" in result.artifacts["policy"]["tags"]
    assert result.artifacts["policy"]["triggered"] == [
        {"when": {"has_rule_violations": True}, "action": "tag"}
    ]


def test_max_review_fields() -> None:
    policy = {"when": {"max_review_fields": 2}, "action": "flag_review"}
    stage = PolicyStage(policies=[policy])

    triggered = stage.run(
        _doc(
            artifacts={
                "low_confidence": [
                    {"record": 0, "field": "a", "confidence": 0.1},
                    {"record": 0, "field": "b", "confidence": 0.2},
                    {"record": 0, "field": "c", "confidence": 0.3},
                ]
            }
        ),
        Context(),
    )
    assert triggered.artifacts["policy"]["review"] is True

    quiet = stage.run(
        _doc(
            artifacts={
                "low_confidence": [
                    {"record": 0, "field": "a", "confidence": 0.1},
                ]
            }
        ),
        Context(),
    )
    assert quiet.artifacts["policy"]["review"] is False
    assert quiet.artifacts["policy"]["triggered"] == []


def test_no_match_noop() -> None:
    doc = _doc(confidence={"total": 0.9})
    stage = PolicyStage(
        policies=[
            {"when": {"min_confidence": 0.5}, "action": "flag_review"},
            {"when": {"max_total_usd": 0.5}, "action": "halt"},
            {
                "when": {"has_rule_violations": True},
                "action": "tag",
                "tag": "x",
            },
        ]
    )
    result = stage.run(doc, Context())
    assert result.artifacts["policy"]["review"] is False
    assert result.artifacts["policy"]["tags"] == []
    assert result.artifacts["policy"]["triggered"] == []
    assert result.artifacts["policy"]["halted"] is False


def test_unknown_condition_key_raises_at_init() -> None:
    with pytest.raises(ValueError, match="Unknown policy condition"):
        PolicyStage(
            policies=[{"when": {"unknown_key": 1}, "action": "flag_review"}]
        )


def test_registry_and_config() -> None:
    register_plugins()
    assert "policy.default" in registry.get_keys()

    registry.clear()
    _ensure_plugins()
    assert "policy.default" in registry.get_keys()

    stage = registry.create(
        "policy.default",
        policies=[{"when": {"min_confidence": 0.5}, "action": "flag_review"}],
    )
    assert isinstance(stage, PolicyStage)

    assert prismdoc.PolicyHaltError is PolicyHaltError
    from prismdoc import PolicyHaltError as Imported

    assert Imported is PolicyHaltError
