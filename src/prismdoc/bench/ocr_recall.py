"""OCR-recall: does parse/OCR text contain ground-truth field values?"""

from __future__ import annotations

import re
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_NUMBER_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")
# Digits with optional decimal; allows surrounding currency / separators.
_NUMERIC_LOOKING_RE = re.compile(
    r"^[\s$€£¥₹]*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[\s$€£¥₹]*$"
)
# Split on whitespace and punctuation; keep alphanumeric runs.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(s: str) -> str:
    """Lowercase, collapse whitespace, strip."""
    return _WHITESPACE_RE.sub(" ", s.lower()).strip()


def _tokens(s: str) -> list[str]:
    """Significant tokens: lowercase, split on whitespace/punctuation, drop len <= 2."""
    return [t for t in _TOKEN_RE.findall(s.lower()) if len(t) > 2]


def _looks_numeric(value: str) -> bool:
    """True when ``value`` is primarily a number (e.g. a receipt total)."""
    cleaned = value.strip()
    if not cleaned:
        return False
    return bool(_NUMERIC_LOOKING_RE.match(cleaned))


def _parse_number(value: str) -> float | None:
    """Extract a float from a numeric-looking string, ignoring currency chars."""
    stripped = value.strip().replace(",", "")
    match = _NUMBER_TOKEN_RE.search(stripped)
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def value_found(value: str, text: str) -> bool:
    """Return True if ``value`` appears in ``text`` under OCR-recall rules.

    Primary check: normalized substring match. For numeric-looking values
    (e.g. totals), also accept a digits/decimal-tolerant float match so that
    ``12.5`` is found in text containing ``12.50``.
    """
    if not value:
        return False

    normalized_value = _normalize(value)
    normalized_text = _normalize(text)
    if normalized_value and normalized_value in normalized_text:
        return True

    if not _looks_numeric(value):
        return False

    target = _parse_number(value)
    if target is None:
        return False

    for match in _NUMBER_TOKEN_RE.finditer(normalized_text.replace(",", "")):
        try:
            if float(match.group()) == target:
                return True
        except ValueError:
            continue
    return False


def token_recall(value: str, text: str) -> float | None:
    """Fraction of ``value``'s significant tokens present in ``text``.

    Uses normalized text. Returns ``None`` when ``value`` has fewer than 2
    significant tokens (token-overlap is only meaningful for multi-token
    values; short/atomic fields should be read via exact match).
    """
    value_tokens = _tokens(value)
    if len(value_tokens) < 2:
        return None
    text_token_set = set(_tokens(_normalize(text)))
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
