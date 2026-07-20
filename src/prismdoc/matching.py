"""Shared value-in-text matching for confidence grounding and OCR-recall."""

from __future__ import annotations

import re
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_NUMBER_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")
# Locale number token: digits with . / , decimals, and spaces only as
# thousands separators (space + exactly three digits) so "1 12.5" stays two tokens.
_LOCALE_NUMBER_RE = re.compile(r"\d(?:[\d.,]| (?=\d{3}(?:[^\d]|$)))*\d|\d")
# Digits with optional decimal; allows surrounding currency / separators.
_NUMERIC_LOOKING_RE = re.compile(
    r"^[\s$€£¥₹]*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[\s$€£¥₹]*$"
)


def normalize_text(s: str) -> str:
    """Lowercase, collapse whitespace, strip."""
    return _WHITESPACE_RE.sub(" ", s.lower()).strip()


def normalize_alphanumeric(s: str) -> str:
    """Lowercase and strip every non-alphanumeric character.

    Used by eval string equality so formatting (spaces, punctuation) does not
    cause false mismatches while content order remains significant.
    """
    return _NON_ALNUM_RE.sub("", s.lower())


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


def _number_candidates(token: str) -> set[float]:
    """Plausible float interpretations of a numeric token under US and EU conventions."""
    s = re.sub(r"[ \t]", "", token.strip())  # spaces = thousands sep -> drop
    s = re.sub(r"[$€£¥₹%]", "", s)
    cands: set[float] = set()
    for variant in (
        s.replace(",", ""),  # US: comma=thousands, dot=decimal
        s.replace(".", "").replace(",", "."),  # EU: dot=thousands, comma=decimal
    ):
        try:
            cands.add(float(variant))
        except ValueError:
            pass
    return cands


def value_in_text(value: Any, text: str) -> bool:
    """Return True if ``value`` appears in ``text`` under shared matching rules.

    Primary check: normalized substring match. For numeric-looking values
    (e.g. totals), also accept a number-token float match so that ``12.5`` is
    found in text containing ``12.50``, but not via digit-soup (``125`` in
    ``1250``). Locale-aware: also accepts EU comma-decimals and thousands
    separators (``8,25``, ``1.767,34``, ``57 483,07``).
    """
    raw = str(value) if not isinstance(value, str) else value
    if not raw:
        return False

    normalized_value = normalize_text(raw)
    normalized_text = normalize_text(text)
    if normalized_value and normalized_value in normalized_text:
        # Numeric substring can be digit-soup (``125`` inside ``1250``); confirm
        # via float token match below instead of returning early.
        if not _looks_numeric(raw):
            return True

    if not _looks_numeric(raw):
        return False

    target = _parse_number(raw)
    if target is None:
        return False

    for m in _LOCALE_NUMBER_RE.finditer(normalized_text):
        if any(abs(cand - target) <= 1e-6 for cand in _number_candidates(m.group())):
            return True
    return False
