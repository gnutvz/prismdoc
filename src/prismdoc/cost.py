"""Token/USD cost ledger for document extraction stages."""

from __future__ import annotations

from typing import Any

from prismdoc.models import Document

# model -> (usd_per_1k_input_tokens, usd_per_1k_output_tokens)
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "default": (0.001, 0.002),
}


class BudgetExceededError(Exception):
    """Raised when a document's cost ledger exceeds the configured USD budget."""


def _resolve_pricing_key(model: str) -> str:
    """Normalize model id and pick the longest matching ``PRICING`` key."""
    name = model.strip()
    # Strip a leading provider prefix (e.g. ``bedrock/``, ``openai/``).
    if "/" in name:
        name = name.split("/", 1)[1]

    known = [key for key in PRICING if key != "default"]
    matches = [key for key in known if name == key or name.startswith(key)]
    if not matches:
        matches = [key for key in known if key in name]
    if matches:
        return max(matches, key=len)
    return "default"


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return estimated USD cost for ``tokens_in`` / ``tokens_out`` on ``model``."""
    key = _resolve_pricing_key(model)
    per_1k_in, per_1k_out = PRICING[key]
    return (tokens_in / 1000.0) * per_1k_in + (tokens_out / 1000.0) * per_1k_out


def record_cost(
    doc: Document,
    stage: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Accumulate token usage and USD cost into ``doc.artifacts["cost"]``."""
    usd = estimate_cost(model, tokens_in, tokens_out)
    existing = doc.artifacts.get("cost")
    if isinstance(existing, dict):
        ledger: dict[str, Any] = existing
    else:
        ledger = {
            "total_usd": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "by_stage": {},
        }
        doc.artifacts["cost"] = ledger

    by_stage = ledger.setdefault("by_stage", {})
    if not isinstance(by_stage, dict):
        by_stage = {}
        ledger["by_stage"] = by_stage

    entry = by_stage.get(stage)
    if not isinstance(entry, dict):
        entry = {
            "usd": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "model": model,
        }
        by_stage[stage] = entry

    entry["usd"] = float(entry.get("usd", 0.0)) + usd
    entry["tokens_in"] = int(entry.get("tokens_in", 0)) + tokens_in
    entry["tokens_out"] = int(entry.get("tokens_out", 0)) + tokens_out
    entry["model"] = model

    ledger["total_usd"] = float(ledger.get("total_usd", 0.0)) + usd
    ledger["tokens_in"] = int(ledger.get("tokens_in", 0)) + tokens_in
    ledger["tokens_out"] = int(ledger.get("tokens_out", 0)) + tokens_out


def check_budget(doc: Document, budget_usd: float) -> None:
    """Raise ``BudgetExceededError`` if the ledger's ``total_usd`` exceeds budget."""
    ledger = doc.artifacts.get("cost")
    if not isinstance(ledger, dict):
        return
    total = float(ledger.get("total_usd", 0.0))
    if total > budget_usd:
        raise BudgetExceededError(
            f"Document cost ${total:.6f} exceeds budget ${budget_usd:.6f}"
        )
