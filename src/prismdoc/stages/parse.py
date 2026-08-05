"""Parse stage: normalize loaded Document pages into markdown text.

Cloud providers (AWS Textract, Azure Document Intelligence, Google Document AI,
Unstructured, …) plug in the same way as Docling or pdfplumber: implement
``Parser.parse(doc) -> str`` and register the adapter under ``parser.*`` /
``parse.*``. One interface; swap the engine by config.

``ParserRouterStage`` picks a ``parse.*`` provider from document type
(``classify_source``), composing the registered parser engines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from prismdoc.models import Document, Source
from prismdoc.registry import create, register
from prismdoc.stages.base import Context, Stage

_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
)

_DEFAULT_PARSER_ROUTES: dict[str, str] = {
    "image_scan": "parse.docling",
    "pdf_scan": "parse.docling",
    "pdf_digital": "parse.pdfplumber",
    "text": "parse.passthrough",
    "spreadsheet": "parse.passthrough",
    "unknown": "parse.passthrough",
}

_DOCLING_EXTRA_HINT = "Install the 'docling' extra: pip install prismdoc[docling]"
_PDFPLUMBER_EXTRA_HINT = (
    "PdfPlumberParser requires the 'pdfplumber' extra: "
    "pip install 'prismdoc[pdfplumber]'"
)
_PYMUPDF4LLM_EXTRA_HINT = (
    "PyMuPDF4LLMParser requires the 'pymupdf4llm' extra: "
    "pip install 'prismdoc[pymupdf4llm]'"
)


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


def _cell_text(cell: object) -> str:
    """Normalize a pdfplumber table cell to a stripped string (``None`` → \"\")."""
    if cell is None:
        return ""
    return str(cell).strip()


def _table_to_gfm(table: list[list[object | None]]) -> str:
    """Render a pdfplumber table as a GitHub-flavored markdown table."""
    if not table:
        return ""
    rows = [[_cell_text(c) for c in row] for row in table]
    header = rows[0]
    n = len(header)
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in range(n)) + "|",
    ]
    for row in rows[1:]:
        # Pad/truncate so every data row matches header width.
        padded = (row + [""] * n)[:n]
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


class PdfPlumberParser(Parser):
    """Optional pdfplumber-backed parser (born-digital PDF text + tables).

    Requires the ``pdfplumber`` extra. Tables are emitted as GFM markdown so
    ``verify.column`` / ``parse_markdown_tables`` can consume them unchanged.
    """

    name = "pdfplumber"

    def parse(self, doc: Document) -> str:
        try:
            import pdfplumber
        except ImportError as exc:
            raise ImportError(_PDFPLUMBER_EXTRA_HINT) from exc

        sections: list[str] = []
        with pdfplumber.open(doc.source.path) as pdf:
            for index, page in enumerate(pdf.pages):
                parts: list[str] = []
                text = page.extract_text()
                if text:
                    parts.append(text)
                for table in page.extract_tables() or []:
                    rendered = _table_to_gfm(table)
                    if rendered:
                        parts.append(rendered)
                if parts:
                    # The page header is not decoration. FigureMergeStage locates
                    # "## Page N" to put a figure's text back on the page it came
                    # from; without it, every figure in the document is appended
                    # at the end, so a diagram from page 1 gets chunked next to
                    # page 40's prose. Emitting it here is what makes figure
                    # placement work with this parser.
                    parts.insert(0, f"## Page {index}")
                    sections.append("\n\n".join(parts))
        return "\n\n".join(sections)


class PyMuPDF4LLMParser(Parser):
    """Optional pymupdf4llm-backed parser (fast PDF → markdown).

    Requires the ``pymupdf4llm`` extra. Builds on the core PyMuPDF dependency;
    returns markdown (text + tables) via ``pymupdf4llm.to_markdown``.
    """

    name = "pymupdf4llm"

    def parse(self, doc: Document) -> str:
        try:
            import pymupdf4llm
        except ImportError as exc:
            raise ImportError(_PYMUPDF4LLM_EXTRA_HINT) from exc

        return pymupdf4llm.to_markdown(doc.source.path)


class ParseStage(Stage):
    """Run a Parser and store the result in ``doc.artifacts["parsed_markdown"]``."""

    name = "parse"

    def __init__(self, parser: Parser | None = None) -> None:
        self.parser = parser or PassthroughParser()

    def run(self, doc: Document, ctx: Context) -> Document:
        doc.artifacts["parsed_markdown"] = self.parser.parse(doc)
        return doc


def classify_source(source: Source) -> str:
    """Deterministic document-type label for parser routing."""
    ext = Path(source.path).suffix.lower()
    if ext in _IMAGE_EXTENSIONS:
        return "image_scan"
    if ext in {".txt", ".md"}:
        return "text"
    if ext == ".xlsx":
        return "spreadsheet"
    if ext == ".pdf":
        try:
            import fitz
        except ImportError:
            return "unknown"
        try:
            with fitz.open(source.path) as pdf:
                for page in pdf:
                    if page.get_text().strip():
                        return "pdf_digital"
            return "pdf_scan"
        except Exception:
            return "unknown"
    return "unknown"


class ParserRouterStage(Stage):
    """Route to a ``parse.*`` provider based on ``classify_source``."""

    name = "parse"

    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self.routes = dict(routes) if routes is not None else dict(_DEFAULT_PARSER_ROUTES)

    def run(self, doc: Document, ctx: Context) -> Document:
        dt = classify_source(doc.source)
        key = self.routes.get(dt) or self.routes.get(
            "unknown", "parse.passthrough"
        )
        stage = create(key)
        doc = stage.run(doc, ctx)
        doc.artifacts["parser_route"] = {"doc_type": dt, "parser": key}
        return doc


def register_plugins() -> None:
    """Register default parsers and parse stage in the plugin registry."""
    register("parser.passthrough", PassthroughParser)
    register("parser.docling", DoclingParser)
    register("parser.pdfplumber", PdfPlumberParser)
    register("parser.pymupdf4llm", PyMuPDF4LLMParser)
    register("parse.default", ParseStage)
    register("parse.passthrough", lambda: ParseStage(parser=PassthroughParser()))
    register("parse.docling", lambda: ParseStage(parser=DoclingParser()))
    register("parse.pdfplumber", lambda: ParseStage(parser=PdfPlumberParser()))
    register(
        "parse.pymupdf4llm", lambda: ParseStage(parser=PyMuPDF4LLMParser())
    )
    register("parse.router", ParserRouterStage)


register_plugins()
