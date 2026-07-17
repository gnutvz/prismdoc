"""Parse stage: normalize loaded Document pages into markdown text."""

from __future__ import annotations

from abc import ABC, abstractmethod

from prismdoc.models import Document
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_DOCLING_EXTRA_HINT = "Install the 'docling' extra: pip install prismdoc[docling]"


class Parser(ABC):
    """Converts a loaded Document into markdown/text."""

    name: str

    @abstractmethod
    def parse(self, doc: Document) -> str:
        """Return markdown/text for ``doc``."""
        ...


class PassthroughParser(Parser):
    """Build simple markdown from pages already populated by loaders."""

    name = "passthrough"

    def parse(self, doc: Document) -> str:
        sections: list[str] = []
        for page in doc.pages:
            parts: list[str] = [f"## Page {page.index}"]
            if page.text:
                parts.append(page.text)
            if page.blocks:
                block_text = "\n".join(
                    block.text for block in page.blocks if block.text
                )
                if block_text and block_text not in (page.text or ""):
                    parts.append(block_text)
            sections.append("\n\n".join(parts))
        return "\n\n".join(sections)


class DoclingParser(Parser):
    """Optional Docling-backed parser (requires the ``docling`` extra)."""

    name = "docling"

    def parse(self, doc: Document) -> str:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise ImportError(_DOCLING_EXTRA_HINT) from exc

        converter = DocumentConverter()
        result = converter.convert(doc.source.path)
        return result.document.export_to_markdown()


class ParseStage(Stage):
    """Run a Parser and store the result in ``doc.artifacts["parsed_markdown"]``."""

    name = "parse"

    def __init__(self, parser: Parser | None = None) -> None:
        self.parser = parser or PassthroughParser()

    def run(self, doc: Document, ctx: Context) -> Document:
        doc.artifacts["parsed_markdown"] = self.parser.parse(doc)
        return doc


def register_plugins() -> None:
    """Register default parsers and parse stage in the plugin registry."""
    register("parser.passthrough", PassthroughParser)
    register("parser.docling", DoclingParser)
    register("parse.default", ParseStage)


register_plugins()
