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


def _normalize(s: str) -> str:
    """Lowercase, collapse whitespace, strip."""
    return _WHITESPACE_RE.sub(" ", s.lower()).strip()


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


def sample_recall(ocr_text: str, fields: dict[str, str]) -> dict[str, Any]:
    """Per-field ``found`` flags plus the fraction of fields found in the sample."""
    found: dict[str, bool] = {
        name: value_found(value, ocr_text) for name, value in fields.items()
    }
    fraction = (sum(found.values()) / len(found)) if found else 0.0
    return {"found": found, "fraction": fraction}
