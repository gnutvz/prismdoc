"""Figure sub-pipeline: extract embedded images, process, merge back via placeholders."""

from __future__ import annotations

import base64
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from prismdoc.models import Document, Source
from prismdoc.pdf import PdfEngine, default_engine
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

_FIGURE_TOKEN_RE = re.compile(r"\[\[FIGURE:([^\]]+)\]\]")
_PAGE_HEADER_RE = re.compile(r"(?m)^## Page (\d+)\s*$")
# Docling writes this where a picture sat. It is the only positional information
# an Office file's Markdown carries, and it is exact — see _replace_image_markers.
_IMAGE_MARKER_RE = re.compile(r"<!--\s*image\s*-->")

logger = logging.getLogger(__name__)
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
        kind = _figure_source_kind(doc.source)
        if kind is None:
            doc.artifacts.setdefault("figures", [])
            return doc

        markdown = doc.artifacts.get("parsed_markdown")
        if not isinstance(markdown, str):
            markdown = ""

        if kind == "pdf":
            figures, placeholders_by_page = _extract_pdf_figures(doc.source.path)
            markdown = _insert_placeholders(markdown, placeholders_by_page)
        else:
            # Office files carry exact positions: docling marks each picture's
            # place in the Markdown, and the extractor yields pictures in that
            # same order. So the tokens go where the pictures were rather than
            # being appended per page.
            figures, ordered_ids = _extract_office_figures(doc.source.path, kind)
            markdown = _replace_image_markers(markdown, ordered_ids)

        doc.artifacts["parsed_markdown"] = markdown
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


def _figure_source_kind(source: Source) -> str | None:
    """`"pdf"`, `"pptx"`, or None when the format carries no extractable figures.

    The two kinds are genuinely different, not two paths to the same thing: a PDF
    figure has to be rasterised out of a page, an Office picture is a file inside
    a zip. They also differ in what they know about position, which is why the
    placement strategies below are not shared.
    """
    if _is_pdf_source(source):
        return "pdf"
    suffix = Path(source.path).suffix.lower()
    if suffix in (".pptx", ".docx"):
        return suffix.lstrip(".")
    return None


def _extract_office_figures(path: str, kind: str) -> tuple[list[Figure], list[str]]:
    """Figures plus their ids, in the order the pictures appear in the document.

    The order is load-bearing rather than incidental: `_replace_image_markers`
    pairs the Nth id with the Nth marker docling wrote, so reordering here would
    attach one picture's description to another's place with nothing to catch it.
    """
    from prismdoc.ooxml import extract_docx_images, extract_pptx_images

    extract = extract_pptx_images if kind == "pptx" else extract_docx_images

    figures: list[Figure] = []
    ordered_ids: list[str] = []
    per_slide: dict[int, int] = {}

    for image in extract(path):
        seen = per_slide.get(image.page_index, 0)
        per_slide[image.page_index] = seen + 1
        fig_id = f"fig_{image.page_index}_{seen}"
        figures.append(
            Figure(
                id=fig_id,
                page_index=image.page_index,
                bbox=image.bbox,
                width=image.width,
                height=image.height,
                image_b64=image.b64,
                mime=image.mime,
            )
        )
        ordered_ids.append(fig_id)

    return figures, ordered_ids


def _replace_image_markers(markdown: str, ordered_ids: list[str]) -> str:
    """Swap docling's `<!-- image -->` markers for figure tokens, in order.

    Exact placement, which the PDF path cannot manage — there, figures are
    positioned to the page and no finer.

    The counts can disagree. Docling marks pictures this extractor skips, such as
    a decorative rule below the size floor, and it can render a chart as an image
    where python-pptx sees a graphic frame. Neither is worth failing over, so the
    surplus on either side is handled and reported rather than silently dropped.
    """
    if not ordered_ids:
        return markdown

    markers = _IMAGE_MARKER_RE.findall(markdown)
    if not markers:
        logger.warning(
            "No image markers in the parsed Markdown — appending %d figure(s) at the "
            "end. Figure positions will not match the document.",
            len(ordered_ids),
        )
        return markdown + _tokens_for(ordered_ids)

    if len(markers) != len(ordered_ids):
        logger.warning(
            "Found %d image marker(s) but %d extracted figure(s); pairing in order and "
            "handling the remainder.",
            len(markers),
            len(ordered_ids),
        )

    remaining = list(ordered_ids)

    def _swap(_match: re.Match[str]) -> str:
        if not remaining:
            # More markers than figures: leave the marker rather than inventing a
            # token that merge would render as "[unprocessed figure ...]".
            return _match.group(0)
        return f"[[FIGURE:{remaining.pop(0)}]]"

    result = _IMAGE_MARKER_RE.sub(_swap, markdown)
    if remaining:
        result += _tokens_for(remaining)
    return result


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
    path: str, engine: PdfEngine | None = None
) -> tuple[list[Figure], dict[int, list[str]]]:
    """Return figures and per-page ordered figure ids for placeholder insertion.

    The engine reads the PDF; this function owns the naming and ordering that the
    placeholder tokens depend on. Ids stay `fig_<page>_<n>` with `n` counting
    within a page, so a token in the markdown keeps pointing at the same figure
    regardless of which engine produced it.
    """
    engine = engine or default_engine()

    figures: list[Figure] = []
    placeholders_by_page: dict[int, list[str]] = {}
    for image in engine.extract_images(path):
        page_index = image.page_index
        on_page = placeholders_by_page.setdefault(page_index, [])
        fig_id = f"fig_{page_index}_{len(on_page)}"
        figures.append(
            Figure(
                id=fig_id,
                page_index=page_index,
                bbox=image.bbox,
                width=image.width,
                height=image.height,
                image_b64=image.b64,
                mime=image.mime,
            )
        )
        on_page.append(fig_id)
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
        # Placement needs page boundaries, and this markdown has none — so every
        # figure lands at the end, a page-1 diagram included. Downstream that is
        # a real quality loss rather than a cosmetic one: a chunker will pair the
        # figure's text with whatever prose happens to end the document.
        #
        # Said out loud because the failure is otherwise invisible. The parser
        # decides: PassthroughParser and PdfPlumberParser emit "## Page N";
        # DoclingParser returns whole-document markdown with no page concept.
        logger.warning(
            "No page markers in the parsed markdown — appending %d figure(s) at the "
            "end instead of on their own pages. Use a parser that emits '## Page N' "
            "(passthrough, pdfplumber) for per-page placement.",
            len(all_ids),
        )
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
