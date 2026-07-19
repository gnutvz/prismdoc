"""Provenance stage: attach per-field lineage back to the source document.

Two paths, in order of reliability:

1. **Evidence-first** — when ExtractStage(evidence=True) had the model cite the exact
   source span for each field (``record.field_evidence``), we locate *that span*. This
   is unambiguous even when the bare value (e.g. ``10.00``) appears in several places,
   because the cited span carries its own context (``TOTAL 10.00`` vs ``CASH 10.00``).
2. **Value search** — with no cited evidence, fall back to reverse-locating the value.
   Best-effort: the first block containing the value wins, which can be ambiguous.

Locations are never fabricated. A cited span that does not actually appear in the
document (a hallucinated quote) is not trusted — we fall back to value search rather
than inventing a location.
"""

from __future__ import annotations

import re
from typing import Any

from prismdoc.matching import value_in_text
from prismdoc.models import Document, FieldProvenance
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_SNIPPET_MAX = 120


class ProvenanceStage(Stage):
    """Attach per-field page / bbox / source span + cited evidence."""

    name = "provenance"

    def run(self, doc: Document, ctx: Context) -> Document:
        for record in doc.records:
            for field, value in record.fields.items():
                if _is_empty(value):
                    continue
                evidence = record.field_evidence.get(field, "")
                located = None
                if evidence:
                    located = _locate_evidence(evidence, doc)
                if located is None:
                    located = _locate_value(value, doc)
                if located is not None:
                    record.provenance[field] = located
        return doc


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _normalize(text: str) -> str:
    """Casefold and collapse whitespace so a verbatim-ish quote still matches."""
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _contains(haystack: str, needle: str) -> bool:
    """Word-boundary-aware containment: ``needle`` must not begin or end inside a
    larger word/number. Prevents a cited ``Total 10.00`` from matching the
    ``Subtotal 10.00`` block, or ``10.00`` from matching inside ``10.005``."""
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return False
        before = haystack[i - 1] if i > 0 else " "
        after_i = i + len(needle)
        after = haystack[after_i] if after_i < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = i + 1


def _locate_evidence(evidence: str, doc: Document) -> FieldProvenance | None:
    """Locate the model-cited span. Returns None if the span is not in the document
    (treated as a hallucinated quote — the caller then falls back to value search)."""
    needle = _normalize(evidence)
    if not needle:
        return None
    for page in doc.pages:
        for block in page.blocks:
            if _contains(_normalize(block.text), needle):
                return FieldProvenance(
                    page=page.index,
                    bbox=block.bbox,
                    source_text=block.text,
                    evidence=evidence,
                    method="evidence",
                )
        if _contains(_normalize(page.text), needle):
            return FieldProvenance(
                page=page.index,
                bbox=None,
                source_text=_evidence_snippet(page.text, evidence),
                evidence=evidence,
                method="evidence",
            )
    return None


def _locate_value(value: Any, doc: Document) -> FieldProvenance | None:
    """Return provenance for ``value`` if found; otherwise ``None`` (best-effort)."""
    for page in doc.pages:
        for block in page.blocks:
            if value_in_text(value, block.text):
                return FieldProvenance(
                    page=page.index,
                    bbox=block.bbox,
                    source_text=block.text,
                    method="value_search",
                )
        if value_in_text(value, page.text):
            return FieldProvenance(
                page=page.index,
                bbox=None,
                source_text=_page_snippet(page.text),
                method="value_search",
            )
    return None


def _evidence_snippet(text: str, evidence: str) -> str:
    """A snippet of ``text`` centered on the cited evidence (falls back to the head)."""
    idx = _normalize(text).find(_normalize(evidence))
    if idx < 0:
        return _page_snippet(text)
    start = max(0, idx - _SNIPPET_MAX // 2)
    return text[start:start + _SNIPPET_MAX]


def _page_snippet(text: str) -> str:
    """Return a short source snippet for a page-level (no-block) match."""
    if not text:
        return ""
    if len(text) <= _SNIPPET_MAX:
        return text
    return text[:_SNIPPET_MAX]


def register_plugins() -> None:
    """Register the default provenance stage in the plugin registry."""
    register("provenance.default", ProvenanceStage)


register_plugins()
