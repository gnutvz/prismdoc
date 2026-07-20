"""Declarative policy stage: gate the pipeline by accumulated document state.

Policies read confidence, cost, review flags, and rule violations, then apply a
terminal action — flag for review, tag, or halt. Config-driven and bounded;
this is not a rules DSL and does not trigger other stages.
"""

from __future__ import annotations

from typing import Any

from prismdoc.cost import CostLedger
from prismdoc.models import Document
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_KNOWN_CONDITIONS = frozenset(
    {
        "min_confidence",
        "max_total_usd",
        "max_review_fields",
        "has_rule_violations",
    }
)
_KNOWN_ACTIONS = frozenset({"flag_review", "halt", "tag"})


class PolicyHaltError(Exception):
    """Raised when a ``halt`` policy matches (deliberate pipeline stop)."""


class PolicyStage(Stage):
    """Evaluate declarative policies against document state and apply actions."""

    name = "policy"

    def __init__(self, policies: list[dict]) -> None:
        self.policies = [_validate_policy(p) for p in policies]

    def run(self, doc: Document, ctx: Context) -> Document:
        doc.artifacts["policy"] = {
            "triggered": [],
            "review": False,
            "tags": [],
            "halted": False,
        }
        policy_art: dict[str, Any] = doc.artifacts["policy"]

        for policy in self.policies:
            when = policy["when"]
            if not _matches(doc, when):
                continue

            action = policy["action"]
            entry = {"when": when, "action": action}
            policy_art["triggered"].append(entry)

            if action == "flag_review":
                policy_art["review"] = True
            elif action == "tag":
                policy_art["tags"].append(policy["tag"])
            elif action == "halt":
                policy_art["halted"] = True
                raise PolicyHaltError(_halt_reason(when))

        return doc


def _validate_policy(policy: dict) -> dict:
    if not isinstance(policy, dict):
        raise ValueError(f"Policy must be a dict, got {type(policy).__name__}")
    if "when" not in policy or "action" not in policy:
        raise ValueError("Policy requires 'when' and 'action'")
    when = policy["when"]
    if not isinstance(when, dict):
        raise ValueError(
            f"Policy 'when' must be a dict, got {type(when).__name__}"
        )
    unknown = set(when) - _KNOWN_CONDITIONS
    if unknown:
        raise ValueError(
            f"Unknown policy condition key(s): {sorted(unknown)}"
        )
    action = policy["action"]
    if action not in _KNOWN_ACTIONS:
        raise ValueError(
            f"Unknown policy action {action!r}; "
            f"expected one of {sorted(_KNOWN_ACTIONS)}"
        )
    if action == "tag" and "tag" not in policy:
        raise ValueError("Policy action 'tag' requires a 'tag' string")
    return policy


def _matches(doc: Document, when: dict[str, Any]) -> bool:
    for key, value in when.items():
        if key == "min_confidence":
            if not _any_confidence_below(doc, float(value)):
                return False
        elif key == "max_total_usd":
            if not _cost_exceeds(doc, float(value)):
                return False
        elif key == "max_review_fields":
            low = doc.artifacts.get("low_confidence") or []
            if not (len(low) > int(value)):
                return False
        elif key == "has_rule_violations":
            violations = doc.artifacts.get("rule_violations") or []
            if bool(value):
                if not violations:
                    return False
            elif violations:
                return False
    return True


def _any_confidence_below(doc: Document, threshold: float) -> bool:
    for record in doc.records:
        for score in record.confidence.values():
            if score < threshold:
                return True
    return False


def _cost_exceeds(doc: Document, max_usd: float) -> bool:
    ledger = doc.artifacts.get("cost")
    if not isinstance(ledger, CostLedger):
        return False
    return ledger.total_usd > max_usd


def _halt_reason(when: dict[str, Any]) -> str:
    return f"Policy halt triggered by conditions: {when!r}"


def register_plugins() -> None:
    """Register the default policy stage in the plugin registry."""
    register("policy.default", PolicyStage)


register_plugins()
