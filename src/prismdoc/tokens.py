"""Token counting helpers (litellm when available, heuristic otherwise)."""

from __future__ import annotations


def count_tokens(text: str, model: str | None = None) -> int:
    """Return an estimated token count for ``text``.

    Uses ``litellm.token_counter`` when litellm is importable; otherwise falls
    back to a deterministic offline heuristic (``max(1, len(text) // 4)``).
    """
    try:
        import litellm
    except ImportError:
        return max(1, len(text) // 4)
    return int(litellm.token_counter(model=model or "gpt-4o-mini", text=text))
