"""Figures out of Office files, without rasterising anything.

PDF figures have to be rendered: the page is a canvas, and what a figure looks
like is the result of drawing it. An Office file is the opposite — a `.pptx` is a
zip, and every picture on a slide is stored inside it as the original file. So
extraction here is a read, not a render: no PDFium, no scaling decision, no
resolution to lose.

That matters most for slide decks. A deck's information density lives in its
diagrams; a 40-slide architecture review parsed as text alone yields a handful of
titles and nothing to retrieve. This is the path that makes those decks useful.

**Position is exact, unusually.** Docling emits an `<!-- image -->` marker in its
Markdown at each picture's place, and walking the deck with python-pptx yields
pictures in that same document order. So figure text can be substituted where the
picture actually sat, rather than appended at the end as it must be for a parser
that reports no structure. `figures.py` relies on the ordering guarantee this
module documents and tests.

python-pptx is not a new dependency: it arrives with docling, which is already
required to read `.pptx` at all.
"""

from __future__ import annotations

import logging

from prismdoc.pdf import ExtractedImage

logger = logging.getLogger(__name__)

_PPTX_EXTRA_HINT = (
    "Reading .pptx needs the 'docling' extra: pip install 'prismdoc[docling]'"
)
_DOCX_EXTRA_HINT = (
    "Reading .docx needs the 'docling' extra: pip install 'prismdoc[docling]'"
)

# OOXML namespaces. A picture reference is a <a:blip r:embed="rIdN"/>, wherever
# it sits — inline in a run, or anchored with text wrapped around it.
_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"

# python-pptx reports geometry in English Metric Units. 914400 EMU to the inch,
# 72 points to the inch — so points are EMU / 12700.
EMU_PER_POINT = 12700

# Anything this small is a bullet glyph, a rule, or a spacer. Describing it costs
# a model call and returns noise, and on a themed deck there are many.
MIN_FIGURE_POINTS = 12


def extract_pptx_images(path: str) -> list[ExtractedImage]:
    """Every picture placed on a slide, in the order a reader meets them.

    Slides in order, shapes within a slide in document order, recursing into
    groups. That ordering is the contract: `figures.py` pairs the Nth image here
    with the Nth `<!-- image -->` marker in the parsed Markdown, so a mismatch
    would silently attach one figure's description to another's position.

    Pictures on slide layouts and masters are excluded, because they are template
    furniture — a logo repeated on all forty slides is forty model calls and
    forty copies of the same sentence in the index.
    """
    presentation_type = _import_pptx()

    images: list[ExtractedImage] = []
    presentation = presentation_type(path)
    for slide_index, slide in enumerate(presentation.slides):
        for shape in _walk(slide.shapes):
            image = _as_extracted(shape, slide_index)
            if image is not None:
                images.append(image)
    return images


def extract_docx_images(path: str) -> list[ExtractedImage]:
    """Every picture in the document body, in reading order.

    The body only — headers, footers and footnotes are separate parts, and
    docling writes no marker for them, so including them would shift every
    pairing by the number of times a letterhead logo appears.

    Walks the XML for `<a:blip>` rather than using `inline_shapes`, which sees
    only inline pictures. An anchored image — one with text wrapped around it,
    which is how most report figures are placed — is invisible to that API and
    would be dropped while docling still marked its position.

    `page_index` is 0 throughout: a .docx has no pages until something paginates
    it, and inventing page numbers here would put a number in `Figure.page_index`
    that means nothing.
    """
    document_type = _import_docx()

    document = document_type(path)
    images: list[ExtractedImage] = []
    for element in document.element.body.iter(_BLIP):
        rel_id = element.get(_EMBED)
        if not rel_id:
            # A blip can also carry r:link for an image referenced off-disk;
            # there are no bytes to extract in that case.
            continue
        try:
            part = document.part.related_parts[rel_id]
        except KeyError:
            logger.debug("Dangling image relationship %s", rel_id)
            continue
        images.append(_from_part(part))
    return images


def _from_part(part) -> ExtractedImage:
    """One image part → ExtractedImage, with dimensions if they can be had."""
    width = height = 0
    try:
        width, height = part.image.px_width, part.image.px_height
    except Exception:  # noqa: BLE001 — dimensions are a nicety, the bytes are not
        pass

    return ExtractedImage(
        page_index=0,
        bbox=None,
        width=int(width),
        height=int(height),
        data=part.blob,
        mime=part.content_type or "image/png",
    )


def _walk(shapes):
    """Shapes in document order, descending into groups."""
    for shape in shapes:
        nested = getattr(shape, "shapes", None)
        if nested is not None:
            # A group is a container, not a picture. Its children keep their
            # place in reading order, which is what the marker pairing needs.
            yield from _walk(nested)
        else:
            yield shape


def _as_extracted(shape, slide_index: int) -> ExtractedImage | None:
    try:
        picture = shape.image
    except (AttributeError, ValueError):
        # Not a picture. python-pptx raises ValueError for shapes that look like
        # one but carry no image part.
        return None

    bbox = _bbox(shape)
    if bbox is not None and _too_small(bbox):
        logger.debug("Skipping decorative image on slide %d: %s", slide_index, bbox)
        return None

    try:
        width, height = picture.size
    except Exception:  # noqa: BLE001 — geometry is optional, the bytes are not
        width = height = 0

    return ExtractedImage(
        page_index=slide_index,
        bbox=bbox,
        width=int(width),
        height=int(height),
        data=picture.blob,
        mime=picture.content_type or "image/png",
    )


def _bbox(shape) -> tuple[float, float, float, float] | None:
    """Placement in points, top-left origin — the convention `prismdoc.pdf` uses."""
    try:
        left, top = shape.left, shape.top
        width, height = shape.width, shape.height
    except AttributeError:
        return None
    if None in (left, top, width, height):
        return None

    x0, y0 = left / EMU_PER_POINT, top / EMU_PER_POINT
    return (x0, y0, x0 + width / EMU_PER_POINT, y0 + height / EMU_PER_POINT)


def _too_small(bbox: tuple[float, float, float, float]) -> bool:
    return (bbox[2] - bbox[0]) < MIN_FIGURE_POINTS or (bbox[3] - bbox[1]) < MIN_FIGURE_POINTS


def _import_pptx():
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise ImportError(_PPTX_EXTRA_HINT) from exc
    return Presentation


def _import_docx():
    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError(_DOCX_EXTRA_HINT) from exc
    return Document
