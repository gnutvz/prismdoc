"""Document archetype classifier and router stage.

Archetypes (flat / visual / mixed / tabular / hierarchical) are a transparent
heuristic — thresholds below are not ML and are intentionally simple. Full
archetype-specific engines (table spans, hierarchical section trees) remain
roadmap; this module only classifies and dispatches existing verifiers.
"""

from __future__ import annotations

import enum
import re

from prismdoc.models import Document
from prismdoc.registry import register
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.verify import (
    LabelVerifyStage,
    TableColumnVerifyStage,
    parse_markdown_tables,
)

_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


class DocumentArchetype(str, enum.Enum):
    """High-level document shape used for strategy routing."""

    FLAT = "flat"
    VISUAL = "visual"
    MIXED = "mixed"
    TABULAR = "tabular"
    HIERARCHICAL = "hierarchical"


def classify_archetype(doc: Document) -> tuple[DocumentArchetype, dict]:
    """Deterministic heuristic classifier; returns ``(archetype, signals)``."""
    text = str(doc.artifacts.get("parsed_markdown") or doc.full_text)
    has_figures = bool(doc.artifacts.get("figures"))
    n_tables = len(parse_markdown_tables(text))
    n_headings = sum(1 for line in text.splitlines() if _HEADING_RE.match(line))
    text_len = len(text)
    signals = {
        "has_figures": has_figures,
        "n_tables": n_tables,
        "n_headings": n_headings,
        "text_len": text_len,
    }

    if has_figures and text_len >= 800:
        return DocumentArchetype.MIXED, signals
    if has_figures:
        return DocumentArchetype.VISUAL, signals
    if n_headings >= 5:
        return DocumentArchetype.HIERARCHICAL, signals
    if n_tables >= 1:
        return DocumentArchetype.TABULAR, signals
    if n_headings >= 2:
        return DocumentArchetype.HIERARCHICAL, signals
    return DocumentArchetype.FLAT, signals


class ArchetypeRouterStage(Stage):
    """Classify a document's archetype and optionally dispatch a verifier."""

    name = "archetype"

    def __init__(
        self,
        schema: TargetSchema | None = None,
        verify: bool = False,
    ) -> None:
        self.schema = schema
        self.verify = verify

    def run(self, doc: Document, ctx: Context) -> Document:
        archetype, signals = classify_archetype(doc)
        doc.artifacts["archetype"] = archetype.value
        doc.artifacts["archetype_signals"] = signals

        if self.verify and self.schema is not None:
            if archetype is DocumentArchetype.TABULAR:
                TableColumnVerifyStage().run(doc, ctx)
                verifier = "verify.column"
            else:
                LabelVerifyStage().run(doc, ctx)
                verifier = "verify.label"
            doc.artifacts["archetype_route"] = {
                "archetype": archetype.value,
                "verifier": verifier,
            }

        return doc


def register_plugins() -> None:
    """Register the archetype router in the plugin registry."""
    register("archetype.router", ArchetypeRouterStage)


register_plugins()
