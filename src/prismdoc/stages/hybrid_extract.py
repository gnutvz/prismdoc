"""Hybrid extract: deterministic regex/matchers first, LLM only for the rest.

Single-record (header) extraction; multi-record alignment is out of scope.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from prismdoc.matching import _NUMBER_TOKEN_RE
from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import ExtractStage, LLMClient

_CURRENCY_CODES = (
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "MYR",
    "CNY",
    "SGD",
    "AUD",
    "CAD",
    "CHF",
    "INR",
    "KRW",
    "THB",
    "VND",
    "NZD",
    "HKD",
    "TWD",
    "PHP",
    "IDR",
)
_CURRENCY_CODE_RE = re.compile(
    r"\b(" + "|".join(_CURRENCY_CODES) + r")\b",
    re.IGNORECASE,
)
_CURRENCY_SYMBOL_MAP: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₩": "KRW",
    "₹": "INR",
    "RM": "MYR",
}
_CURRENCY_SYMBOL_RE = re.compile(
    r"(" + "|".join(re.escape(s) for s in _CURRENCY_SYMBOL_MAP) + r")"
)

_DATE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),  # yyyy-mm-dd
    re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),  # dd/mm/yyyy
    re.compile(r"\b(\d{2}-\d{2}-\d{4})\b"),  # dd-mm-yyyy
    re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b"),  # dd.mm.yyyy
)

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


def _match_number(text: str) -> str | None:
    match = _NUMBER_TOKEN_RE.search(text)
    return match.group(0) if match else None


def _match_currency(text: str) -> str | None:
    code = _CURRENCY_CODE_RE.search(text)
    if code is not None:
        return code.group(1).upper()
    symbol = _CURRENCY_SYMBOL_RE.search(text)
    if symbol is not None:
        return _CURRENCY_SYMBOL_MAP[symbol.group(1)]
    return None


def _match_date(text: str) -> str | None:
    for pattern in _DATE_RES:
        match = pattern.search(text)
        if match is not None:
            return match.group(1)
    return None


def _match_email(text: str) -> str | None:
    match = _EMAIL_RE.search(text)
    return match.group(0) if match else None


DETERMINISTIC_MATCHERS: dict[str, Callable[[str], str | None]] = {
    "number": _match_number,
    "currency": _match_currency,
    "date": _match_date,
    "email": _match_email,
}


def _apply_rule(text: str, rule: dict[str, Any]) -> str | None:
    """Apply a field rule; return the first match or None."""
    if "pattern" in rule:
        pattern = re.compile(str(rule["pattern"]))
        group = int(rule.get("group", 0))
        match = pattern.search(text)
        if match is None:
            return None
        return match.group(group)

    if "matcher" in rule:
        name = str(rule["matcher"])
        matcher = DETERMINISTIC_MATCHERS.get(name)
        if matcher is None:
            known = ", ".join(sorted(DETERMINISTIC_MATCHERS))
            raise ValueError(
                f"Unknown deterministic matcher {name!r}; expected one of: {known}"
            )
        return matcher(text)

    raise ValueError(
        "field_rules entry must have 'pattern' or 'matcher', "
        f"got keys {sorted(rule)!r}"
    )


class HybridExtractStage(Stage):
    """Extract simple fields deterministically; call the LLM only for the rest.

    When every schema field is covered by a deterministic match, the client is
    never invoked (cost $0).
    """

    name = "extract"

    def __init__(
        self,
        schema: TargetSchema,
        field_rules: dict[str, dict[str, Any]],
        client: LLMClient | None = None,
    ) -> None:
        self.schema = schema
        self.field_rules = field_rules
        self.client = client

    def run(self, doc: Document, ctx: Context) -> Document:
        text = str(doc.artifacts.get("parsed_markdown") or doc.full_text)

        deterministic: dict[str, str] = {}
        for field_name, rule in self.field_rules.items():
            value = _apply_rule(text, rule)
            if value is not None:
                deterministic[field_name] = value

        remaining = [
            name for name in self.schema.field_names() if name not in deterministic
        ]

        llm_values: dict[str, Any] = {}
        if remaining:
            subset = TargetSchema(
                fields=[f for f in self.schema.fields if f.name in remaining]
            )
            temp = Document(
                source=doc.source,
                artifacts={"parsed_markdown": text},
            )
            extracted = ExtractStage(schema=subset, client=self.client).run(
                temp, ctx
            )
            if extracted.records:
                llm_values = dict(extracted.records[0].fields)

        merged: dict[str, Any] = {**deterministic, **llm_values}
        doc.records = [Record(fields=merged)]
        doc.artifacts["hybrid"] = {
            "deterministic": [
                name
                for name in self.schema.field_names()
                if name in deterministic
            ],
            "llm": remaining,
        }
        return doc


def register_plugins() -> None:
    """Register the hybrid extract stage in the plugin registry."""
    register("extract.hybrid", HybridExtractStage)


register_plugins()
