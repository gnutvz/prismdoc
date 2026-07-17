"""Figure sub-pipeline: extract embedded images, process, merge back via placeholders."""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from pathlib import Path

import fitz
from pydantic import BaseModel

from prismdoc.models import Document, Source
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_FIGURE_TOKEN_RE = re.compile(r"\[\[FIGURE:([^\]]+)\]\]")
_PAGE_HEADER_RE = re.compile(r"(?m)^## Page (\d+)\s*$")
_DOCLING_EXTRA_HINT = (
    "OcrFigureProcessor requires OCR deps; install the 'docling' extra: "
    "pip install prismdoc[docling]"
)


class Figure(BaseModel):
    """One extracted embedded figure (image/diagram) from a document."""

    id: str
    page_index: int
    bbox: tuple[float, float, float, float] | None = None
    width: int
    height: int
    image_b64: str
    mime: str = "image/png"
    result: str | None = None


class FigureProcessor(ABC):
    """Pluggable method that turns a figure image into text (stub / OCR / VLM)."""

    @abstractmethod
    def process(self, figure: Figure) -> str:
        """Return text that will replace the figure's placeholder token."""
        ...


class StubFigureProcessor(FigureProcessor):
    """Deterministic offline processor for tests and cheap pipelines."""

    def process(self, figure: Figure) -> str:
        return (
            f"[figure {figure.id}: {figure.width}x{figure.height} {figure.mime}]"
        )


class OcrFigureProcessor(FigureProcessor):
    """Optional OCR via RapidOCR (guarded import; requires docling extra)."""

    def process(self, figure: Figure) -> str:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ImportError(_DOCLING_EXTRA_HINT) from exc

        image_bytes = base64.b64decode(figure.image_b64)
        ocr = RapidOCR()
        result, _ = ocr(image_bytes)
        if not result:
            return ""
        lines: list[str] = []
        for item in result:
            # RapidOCR rows: [box, text, confidence]
            if len(item) >= 2 and item[1]:
                lines.append(str(item[1]))
        return "\n".join(lines)


class FigureExtractStage(Stage):
    """Pull embedded PDF images out and leave ``[[FIGURE:<id>]]`` placeholders."""

    name = "figures.extract"

    def run(self, doc: Document, ctx: Context) -> Document:
        if not _is_pdf_source(doc.source):
            doc.artifacts.setdefault("figures", [])
            return doc

        figures, placeholders_by_page = _extract_pdf_figures(doc.source.path)
        markdown = doc.artifacts.get("parsed_markdown")
        if not isinstance(markdown, str):
            markdown = ""
        doc.artifacts["parsed_markdown"] = _insert_placeholders(
            markdown, placeholders_by_page
        )
        doc.artifacts["figures"] = [fig.model_dump() for fig in figures]
        return doc


class FigureProcessStage(Stage):
    """Run a FigureProcessor on each extracted figure and set ``result``."""

    name = "figures.process"

    def __init__(self, processor: FigureProcessor | None = None) -> None:
        self.processor = processor or StubFigureProcessor()

    def run(self, doc: Document, ctx: Context) -> Document:
        raw = doc.artifacts.get("figures") or []
        processed: list[Figure] = []
        for item in raw:
            figure = (
                Figure.model_validate(item) if isinstance(item, dict) else item
            )
            figure.result = self.processor.process(figure)
            processed.append(figure)
        doc.artifacts["figures"] = [fig.model_dump() for fig in processed]
        return doc


class FigureMergeStage(Stage):
    """Replace ``[[FIGURE:<id>]]`` tokens with each figure's ``result`` text."""

    name = "figures.merge"

    def run(self, doc: Document, ctx: Context) -> Document:
        markdown = doc.artifacts.get("parsed_markdown")
        if not isinstance(markdown, str):
            return doc

        by_id: dict[str, Figure] = {}
        for item in doc.artifacts.get("figures") or []:
            figure = (
                Figure.model_validate(item) if isinstance(item, dict) else item
            )
            by_id[figure.id] = figure

        def _replace(match: re.Match[str]) -> str:
            fig_id = match.group(1)
            figure = by_id.get(fig_id)
            if figure is not None and figure.result is not None:
                return figure.result
            return f"[unprocessed figure {fig_id}]"

        doc.artifacts["parsed_markdown"] = _FIGURE_TOKEN_RE.sub(_replace, markdown)
        return doc


def _is_pdf_source(source: Source) -> bool:
    if Path(source.path).suffix.lower() == ".pdf":
        return True
    if source.mime and source.mime.lower() == "application/pdf":
        return True
    return False


def _mime_for_ext(ext: str) -> str:
    normalized = ext.lower().lstrip(".")
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized == "png":
        return "image/png"
    if normalized:
        return f"image/{normalized}"
    return "image/png"


def _bbox_from_rects(
    rects: list,
) -> tuple[float, float, float, float] | None:
    if not rects:
        return None
    rect = rects[0]
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _extract_pdf_figures(
    path: str,
) -> tuple[list[Figure], dict[int, list[str]]]:
    """Return figures and per-page ordered figure ids for placeholder insertion."""
    figures: list[Figure] = []
    placeholders_by_page: dict[int, list[str]] = {}
    with fitz.open(path) as pdf:
        for page_index, page in enumerate(pdf):
            try:
                images = page.get_images(full=True)
            except Exception:
                continue
            for n, img in enumerate(images):
                try:
                    xref = int(img[0])
                    extracted = pdf.extract_image(xref)
                    image_bytes = extracted["image"]
                    fig_id = f"fig_{page_index}_{n}"
                    rects = page.get_image_rects(xref)
                    figure = Figure(
                        id=fig_id,
                        page_index=page_index,
                        bbox=_bbox_from_rects(rects),
                        width=int(extracted["width"]),
                        height=int(extracted["height"]),
                        image_b64=base64.b64encode(image_bytes).decode("ascii"),
                        mime=_mime_for_ext(str(extracted.get("ext", "png"))),
                    )
                except Exception:
                    continue
                figures.append(figure)
                placeholders_by_page.setdefault(page_index, []).append(fig_id)
    return figures, placeholders_by_page


def _tokens_for(figure_ids: list[str]) -> str:
    return "".join(f"\n[[FIGURE:{fig_id}]]" for fig_id in figure_ids)


def _insert_placeholders(
    markdown: str, placeholders_by_page: dict[int, list[str]]
) -> str:
    """Insert figure tokens at the end of each page section (or append at end)."""
    if not placeholders_by_page:
        return markdown

    if not markdown:
        ordered_ids: list[str] = []
        for page_index in sorted(placeholders_by_page):
            ordered_ids.extend(placeholders_by_page[page_index])
        return _tokens_for(ordered_ids).lstrip("\n")

    headers = list(_PAGE_HEADER_RE.finditer(markdown))
    if not headers:
        all_ids: list[str] = []
        for page_index in sorted(placeholders_by_page):
            all_ids.extend(placeholders_by_page[page_index])
        return markdown + _tokens_for(all_ids)

    # Process pages from last to first so earlier offsets stay valid.
    result = markdown
    for page_index in sorted(placeholders_by_page, reverse=True):
        figure_ids = placeholders_by_page[page_index]
        if not figure_ids:
            continue
        tokens = _tokens_for(figure_ids)
        page_header: re.Match[str] | None = None
        next_start: int | None = None
        for i, match in enumerate(headers):
            if int(match.group(1)) == page_index:
                page_header = match
                if i + 1 < len(headers):
                    next_start = headers[i + 1].start()
                break
        if page_header is None:
            result = result + tokens
            continue
        if next_start is None:
            result = result.rstrip() + tokens
        else:
            before = result[:next_start].rstrip()
            after = result[next_start:]
            result = before + tokens + "\n\n" + after
        # Refresh header matches after mutation when continuing (we go reverse,
        # and only mutate within/after the matched page, so earlier headers'
        # start offsets remain valid — but next_start for earlier pages uses
        # the original ``headers`` list. Rebuild headers for safety.
        headers = list(_PAGE_HEADER_RE.finditer(result))
    return result


def register_plugins() -> None:
    """Register figure extract / process / merge stages in the plugin registry."""
    register("figures.extract", FigureExtractStage)
    register("figures.process", FigureProcessStage)
    register("figures.merge", FigureMergeStage)


register_plugins()
