"""Chunked extract: split long text, extract per chunk, merge/dedup records."""

from __future__ import annotations

from prismdoc.cost import check_budget, merge_cost
from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import ExtractStage, LLMClient


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into chunks each ``<= max_chars``, breaking at newlines only.

    A single line longer than ``max_chars`` becomes its own chunk. Order is
    preserved; empty chunks are dropped.
    """
    if max_chars <= 0:
        raise ValueError(f"max_chars must be positive, got {max_chars}")
    if not text:
        return []

    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        if len(line) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            chunks.append(line)
            continue

        # Joining with "\n" adds one char between lines when current is non-empty.
        added = len(line) + (1 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += added

    if current:
        chunks.append("\n".join(current))

    return [chunk for chunk in chunks if chunk]


class ChunkedExtractStage(Stage):
    """Extract records from long documents via chunk → extract → merge/dedup.

    Targets record-list (line-item) extraction where concatenating across chunks
    is meaningful. Header-like fields that repeat in every chunk are deduped —
    acceptable for this stage.
    """

    name = "extract"

    def __init__(
        self,
        schema: TargetSchema,
        client: LLMClient | None = None,
        max_chunk_chars: int = 8000,
    ) -> None:
        self.schema = schema
        self.max_chunk_chars = max_chunk_chars
        self._extractor = ExtractStage(schema=schema, client=client)

    def run(self, doc: Document, ctx: Context) -> Document:
        text = doc.artifacts.get("parsed_markdown") or doc.full_text
        chunks = chunk_text(str(text), self.max_chunk_chars)

        budget = ctx.options.get("budget_usd")
        all_records: list[Record] = []
        for chunk in chunks:
            temp = Document(
                source=doc.source,
                artifacts={"parsed_markdown": chunk},
            )
            extracted = self._extractor.run(temp, ctx)
            all_records.extend(extracted.records)
            # Roll the per-chunk LLM cost up into the parent ledger, then enforce
            # the overall budget across chunks (each chunk runs on a fresh temp doc).
            merge_cost(doc, extracted)
            if budget is not None:
                check_budget(doc, float(budget))

        records_before = len(all_records)
        merged = _dedup_records(all_records)
        doc.records = merged
        doc.artifacts["chunking"] = {
            "chunks": len(chunks),
            "records_before_dedup": records_before,
            "records_after": len(merged),
        }
        return doc


def _dedup_records(records: list[Record]) -> list[Record]:
    """Drop records whose ``fields`` exactly equals one already kept (order-preserving)."""
    unique: list[Record] = []
    for record in records:
        if any(existing.fields == record.fields for existing in unique):
            continue
        unique.append(record)
    return unique


def register_plugins() -> None:
    """Register the chunked extract stage in the plugin registry."""
    register("extract.chunked", ChunkedExtractStage)


register_plugins()
