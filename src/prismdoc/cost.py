"""Token/USD cost ledger for document extraction stages."""

from __future__ import annotations

from pydantic import BaseModel, Field

from prismdoc.models import Document

# model -> (usd_per_1k_input_tokens, usd_per_1k_output_tokens)
# Fallback when litellm is unavailable or does not know the model.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
}


class BudgetExceededError(Exception):
    """Raised when a document's cost ledger exceeds the configured USD budget."""


class StageCost(BaseModel):
    """Per-stage token usage and USD cost (or honest unpriced/unmetered flags)."""

    usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    unpriced: bool = False
    unmetered: bool = False


class CostLedger(BaseModel):
    """Document-level cost ledger; ``total_usd`` sums only priced amounts."""

    total_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    unpriced_calls: int = 0
    unmetered_calls: int = 0
    by_stage: dict[str, StageCost] = Field(default_factory=dict)

    def add(
        self,
        stage: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        usd: float | None,
        unpriced: bool = False,
        unmetered: bool = False,
    ) -> None:
        """Accumulate one LLM call into this ledger."""
        if usd is not None:
            self.total_usd += usd
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        if unpriced:
            self.unpriced_calls += 1
        if unmetered:
            self.unmetered_calls += 1

        existing = self.by_stage.get(stage)
        if existing is None:
            self.by_stage[stage] = StageCost(
                usd=usd,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                model=model,
                unpriced=unpriced,
                unmetered=unmetered,
            )
            return

        if usd is not None:
            existing.usd = (existing.usd or 0.0) + usd
        existing.tokens_in += tokens_in
        existing.tokens_out += tokens_out
        existing.model = model
        existing.unpriced = existing.unpriced or unpriced
        existing.unmetered = existing.unmetered or unmetered


def _resolve_pricing_key(
    model: str,
    pricing: dict[str, tuple[float, float]],
) -> str | None:
    """Normalize model id and pick the longest matching pricing key, or None."""
    name = model.strip()
    # Strip a leading provider prefix (e.g. ``bedrock/``, ``openai/``).
    if "/" in name:
        name = name.split("/", 1)[1]

    known = list(pricing)
    matches = [key for key in known if name == key or name.startswith(key)]
    if not matches:
        matches = [key for key in known if key in name]
    if matches:
        return max(matches, key=len)
    return None


def _litellm_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Return litellm USD cost when importable and the model is priced."""
    try:
        from litellm import cost_per_token
    except ImportError:
        return None
    try:
        prompt_cost, completion_cost = cost_per_token(
            model=model,
            prompt_tokens=tokens_in,
            completion_tokens=tokens_out,
        )
    except Exception:
        return None
    return float(prompt_cost) + float(completion_cost)


def estimate_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """Return estimated USD cost, or ``None`` when the model has no known price.

    Prefer litellm's maintained pricing when available and no ``pricing`` override
    is passed; otherwise use the local table (or the override). Unknown models
    return ``None`` — never a fabricated default.
    """
    if pricing is None:
        litellm_usd = _litellm_cost(model, tokens_in, tokens_out)
        if litellm_usd is not None:
            return litellm_usd
        table = PRICING
    else:
        table = pricing

    key = _resolve_pricing_key(model, table)
    if key is None:
        return None
    per_1k_in, per_1k_out = table[key]
    return (tokens_in / 1000.0) * per_1k_in + (tokens_out / 1000.0) * per_1k_out


def _get_or_create_ledger(doc: Document) -> CostLedger:
    existing = doc.artifacts.get("cost")
    if isinstance(existing, CostLedger):
        return existing
    ledger = CostLedger()
    doc.artifacts["cost"] = ledger
    return ledger


def record_cost(
    doc: Document,
    stage: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Accumulate token usage and USD cost into ``doc.artifacts["cost"]``."""
    usd = estimate_cost(model, tokens_in, tokens_out)
    unpriced = usd is None
    ledger = _get_or_create_ledger(doc)
    ledger.add(
        stage,
        model,
        tokens_in,
        tokens_out,
        usd,
        unpriced=unpriced,
        unmetered=False,
    )


def record_unmetered(doc: Document, stage: str, model: str) -> None:
    """Record an LLM call that returned no usage metadata."""
    ledger = _get_or_create_ledger(doc)
    ledger.add(
        stage,
        model,
        tokens_in=0,
        tokens_out=0,
        usd=None,
        unpriced=False,
        unmetered=True,
    )


def check_budget(doc: Document, budget_usd: float) -> None:
    """Raise ``BudgetExceededError`` if the ledger's ``total_usd`` exceeds budget."""
    ledger = doc.artifacts.get("cost")
    if not isinstance(ledger, CostLedger):
        return
    if ledger.total_usd > budget_usd:
        raise BudgetExceededError(
            f"Document cost ${ledger.total_usd:.6f} exceeds budget "
            f"${budget_usd:.6f}"
        )
