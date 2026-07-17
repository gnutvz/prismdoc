"""OCR-recall: does parse/OCR text contain ground-truth field values?"""

from __future__ import annotations

import re
from typing import Any

from prismdoc.matching import normalize_text, value_in_text

# Split on whitespace and punctuation; keep alphanumeric runs.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> list[str]:
    """Significant tokens: lowercase, split on whitespace/punctuation, drop len <= 2."""
    return [t for t in _TOKEN_RE.findall(s.lower()) if len(t) > 2]


def value_found(value: str, text: str) -> bool:
    """Return True if ``value`` appears in ``text`` under OCR-recall rules.

    Delegates to :func:`prismdoc.matching.value_in_text` (shared with confidence
    grounding): normalized substring match, plus number-token float match for
    numeric-looking values (``12.5`` finds ``12.50``, not digit-soup ``1250``).
    """
    return value_in_text(value, text)


def token_recall(value: str, text: str) -> float | None:
    """Fraction of ``value``'s significant tokens present in ``text``.

    Uses normalized text. Returns ``None`` when ``value`` has fewer than 2
    significant tokens (token-overlap is only meaningful for multi-token
    values; short/atomic fields should be read via exact match).
    """
    value_tokens = _tokens(value)
    if len(value_tokens) < 2:
        return None
    text_token_set = set(_tokens(normalize_text(text)))
    hits = sum(1 for t in value_tokens if t in text_token_set)
    return hits / len(value_tokens)


def sample_recall(ocr_text: str, fields: dict[str, str]) -> dict[str, Any]:
    """Per-field exact + token recall, plus sample-level mean exact / mean token.

    Per-field ``token`` may be ``None`` when token-overlap is not measurable.
    ``mean_token`` averages only non-None token values (or ``None`` if none).
    """
    per_field: dict[str, dict[str, bool | float | None]] = {
        name: {
            "exact": value_found(value, ocr_text),
            "token": token_recall(value, ocr_text),
        }
        for name, value in fields.items()
    }
    n = len(per_field)
    mean_exact = (
        sum(bool(v["exact"]) for v in per_field.values()) / n if n else 0.0
    )
    token_values = [
        float(v["token"]) for v in per_field.values() if v["token"] is not None
    ]
    mean_token: float | None = (
        sum(token_values) / len(token_values) if token_values else None
    )
    return {
        "per_field": per_field,
        "mean_exact": mean_exact,
        "mean_token": mean_token,
    }
