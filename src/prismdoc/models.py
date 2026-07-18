"""Core document data models for the prismdoc pipeline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Source(BaseModel):
    """Source metadata for an input document."""

    path: str
    mime: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Block(BaseModel):
    """One layout block within a page."""

    text: str
    bbox: tuple[float, float, float, float] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel):
    """A single page of a document."""

    index: int
    text: str = ""
    blocks: list[Block] = Field(default_factory=list)
    image_ref: str | None = None


class FieldProvenance(BaseModel):
    """Where an extracted field value was located in the source document."""

    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    source_text: str = ""


class Record(BaseModel):
    """One extracted structured record."""

    fields: dict[str, Any]
    confidence: dict[str, float] = Field(default_factory=dict)
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)


class TraceEntry(BaseModel):
    """Log entry for one Stage run."""

    stage: str
    ok: bool
    duration_ms: float
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    """Main data carrier flowing through the pipeline."""

    source: Source
    pages: list[Page] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    records: list[Record] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)

    def add_trace(self, entry: TraceEntry) -> None:
        """Append a Stage run entry to the document trace."""
        self.trace.append(entry)

    @property
    def full_text(self) -> str:
        """Concatenate text from all pages, separated by newlines."""
        return "\n".join(page.text for page in self.pages)
