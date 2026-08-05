"""How prismdoc reads PDFs — one interface, swappable engine.

Two operations sit behind this: turning a PDF into pages of text and layout
blocks (the ingest stage), and pulling embedded figures out for the figure
sub-pipeline. Both used to call PyMuPDF directly, which made an AGPL-3.0 package
a core dependency of an MIT project — so `pip install prismdoc` carried a
copyleft obligation that LICENSE did not mention, and any consumer with a licence
gate could not install it at all.

The default engine is permissive: pdfplumber (MIT, via pdfminer.six) for text,
geometry and figure placement, and pypdfium2 (Apache-2.0 / BSD-3, wrapping
Google's PDFium) for rasterising. PyMuPDF remains available as an opt-in engine
for anyone who already has it and wants its speed.

Figure pixels come from *rasterising the region of the page the figure occupies*,
not from pulling the embedded image stream. That is a deliberate difference from
the PyMuPDF path, and the better behaviour for what figures are used for here —
feeding an image to a vision model:

- It produces a consistent RGB PNG no matter how the source encoded the image.
  Raw-stream extraction has to decode CCITT, JBIG2, CMYK JPEG and indexed
  colourspaces itself, and gets several of them subtly wrong.
- It captures what the figure *looks like on the page*, including anything drawn
  over or under it, rather than the isolated asset.
- Resolution is chosen per figure from the embedded image's own pixel size, so a
  600-DPI scan placed in a small box is not silently downsampled.

The cost is that rasterising is slower than copying bytes out, so only pages that
actually carry figures are touched.

Figures drawn as vector paths have no image XObject to enumerate, so they are
invisible to all of the above. `PdfPlumberEngine(detect_vector_figures=True)`
looks for them by clustering page geometry — see `_vector_regions`. It is off by
default and stays that way: everything else here is extraction, where a figure
either is in the file or is not, while this is inference that can be wrong in
both directions, and a wrong guess costs a model call and a paragraph of noise in
the retrieval index. The PyMuPDF engine does not implement it.

Coordinates are top-left origin, y increasing downward, in PDF points — matching
what PyMuPDF reports, so `Block.bbox` means the same thing whichever engine ran.
"""

from __future__ import annotations

import base64
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from prismdoc.errors import UnreadableDocumentError
from prismdoc.models import Block, Page

logger = logging.getLogger(__name__)

# Floor for figure render scale. Vector diagrams have no native resolution to
# match, so they get this: enough that a small schematic stays legible to a
# vision model.
FIGURE_RENDER_SCALE = 2.0

# Ceiling on the long edge of a rendered figure, in pixels. Bounds the cost of
# one pathological placement — a poster-sized image dropped into a page — without
# constraining ordinary scans, which land far below it.
MAX_FIGURE_PIXELS = 4000

# --- Vector figure detection (opt-in) ----------------------------------------
#
# A schematic drawn with lines and fills has no image to extract, so it is
# invisible to everything above. Finding it means guessing from page geometry,
# and every constant here exists to make that guess conservative: a false
# positive costs a model call and puts a paragraph about a page border into the
# retrieval index, which is worse than missing a diagram.

# Primitives a cluster must contain. Three is the smallest real diagram — two
# boxes and a connector — so the bar cannot go higher without missing the shape
# most architecture drawings take. It is also why the size and table filters
# below carry most of the weight.
MIN_VECTOR_PRIMITIVES = 3
# Points. Smaller than this is an icon or a checkbox.
MIN_VECTOR_SIDE = 40
# Fraction of the page a cluster may cover. A full-page border merges everything
# on the page into one "diagram"; refusing that is cheaper than untangling it.
MAX_VECTOR_PAGE_FRACTION = 0.8
# Points. Primitives closer than this are treated as parts of one drawing.
VECTOR_CLUSTER_GAP = 18
# A rule is long and flat: a header underline, a table edge, a page border. The
# ratio is deliberately extreme so a genuinely wide, short diagram survives.
RULE_ASPECT_RATIO = 25
# Pages with more geometry than this are maps, dense charts or CAD exports. The
# clustering is quadratic and the result on such a page is one giant blob, so
# skipping is both faster and more accurate than trying.
MAX_VECTOR_PRIMITIVES_PER_PAGE = 1500

_PYMUPDF_EXTRA_HINT = (
    "The 'pymupdf' PDF engine requires the optional extra: "
    "pip install 'prismdoc[pymupdf]'. It is optional because PyMuPDF is AGPL-3.0 "
    "(commercial licence available from Artifex); prismdoc's default engine is "
    "permissive and needs no extra."
)


@dataclass
class ExtractedImage:
    """One embedded figure, as raw bytes plus where it sat on the page.

    Deliberately not a `Figure`: that type belongs to the figure stage and
    carries ids and processing results. Keeping the engine's output neutral is
    what stops `prismdoc.pdf` and `prismdoc.stages.figures` importing each other.
    """

    page_index: int
    bbox: tuple[float, float, float, float] | None
    width: int
    height: int
    data: bytes
    mime: str = "image/png"

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


class PdfEngine(ABC):
    """Reads a PDF. Implement both halves and register under `pdf.<name>`."""

    name: str

    @abstractmethod
    def load_pages(self, path: str) -> list[Page]:
        """Pages with plain text and layout blocks."""

    @abstractmethod
    def extract_images(self, path: str) -> list[ExtractedImage]:
        """Embedded figures, in page then placement order."""


class PdfPlumberEngine(PdfEngine):
    """Permissive default: pdfplumber (MIT) + pypdfium2 (Apache-2.0 / BSD-3).

    Args:
        detect_vector_figures: also return regions that *look like* drawings —
            clusters of lines and fills with no embedded image behind them. Off
            by default, and deliberately so: everything else this engine returns
            is extraction, where a figure either is in the file or is not. This
            is inference, it can be wrong in both directions, and being wrong
            costs a model call plus a paragraph of noise in the index.
    """

    name = "pdfplumber"

    def __init__(self, detect_vector_figures: bool = False) -> None:
        self.detect_vector_figures = detect_vector_figures

    def load_pages(self, path: str) -> list[Page]:
        pdfplumber = _import_pdfplumber()

        try:
            with pdfplumber.open(path) as pdf:
                return [
                    Page(
                        index=index,
                        text=page.extract_text() or "",
                        blocks=_blocks_from_lines(page),
                    )
                    for index, page in enumerate(pdf.pages)
                ]
        except UnreadableDocumentError:
            raise
        except Exception as exc:
            raise _as_unreadable(path, exc) from exc

    def extract_images(self, path: str) -> list[ExtractedImage]:
        pdfplumber = _import_pdfplumber()
        pdfium = _import_pdfium()

        images: list[ExtractedImage] = []
        try:
            with pdfplumber.open(path) as pdf:
                rendered = pdfium.PdfDocument(path)
                try:
                    for index, page in enumerate(pdf.pages):
                        placements = list(page.images)
                        for placement in placements:
                            image = _render_figure(rendered[index], page, index, placement)
                            if image is not None:
                                images.append(image)

                        if not self.detect_vector_figures:
                            continue
                        # Appended after the embedded images on the page, so an
                        # inferred figure never renumbers an extracted one.
                        for box in _vector_regions(page, placements):
                            image = _render_region(rendered[index], page, index, box)
                            if image is not None:
                                images.append(image)
                finally:
                    rendered.close()
        except UnreadableDocumentError:
            raise
        except Exception as exc:
            raise _as_unreadable(path, exc) from exc

        return images


class PyMuPDFEngine(PdfEngine):
    """Opt-in engine. Faster, but AGPL-3.0 — see `_PYMUPDF_EXTRA_HINT`."""

    name = "pymupdf"

    def load_pages(self, path: str) -> list[Page]:
        fitz = _import_fitz()
        try:
            with fitz.open(path) as pdf:
                if pdf.needs_pass:
                    raise UnreadableDocumentError(
                        f"Cannot read {path!r}: PDF is encrypted/password-protected"
                    )
                return [
                    Page(index=i, text=page.get_text(), blocks=_blocks_from_fitz(page))
                    for i, page in enumerate(pdf)
                ]
        except UnreadableDocumentError:
            raise
        except Exception as exc:
            raise UnreadableDocumentError(f"Cannot read {path!r}: {exc}") from exc

    def extract_images(self, path: str) -> list[ExtractedImage]:
        fitz = _import_fitz()
        images: list[ExtractedImage] = []
        with fitz.open(path) as pdf:
            for page_index, page in enumerate(pdf):
                try:
                    embedded = page.get_images(full=True)
                except Exception:
                    continue
                for img in embedded:
                    try:
                        xref = int(img[0])
                        extracted = pdf.extract_image(xref)
                        images.append(
                            ExtractedImage(
                                page_index=page_index,
                                bbox=_first_rect(page.get_image_rects(xref)),
                                width=int(extracted["width"]),
                                height=int(extracted["height"]),
                                data=extracted["image"],
                                mime=_mime_for_ext(str(extracted.get("ext", "png"))),
                            )
                        )
                    except Exception:
                        # One unreadable image must not cost the whole page.
                        continue
        return images


def default_engine() -> PdfEngine:
    """The permissive engine. Deterministic on purpose.

    Falling back to PyMuPDF when it happens to be installed would make the
    licence of a build depend on what else was in the environment, and make two
    machines disagree about what `prismdoc` pulled in. Choosing the AGPL engine
    is explicit: construct `PyMuPDFEngine`, or resolve `pdf.pymupdf`.
    """
    return PdfPlumberEngine()


# --- pdfplumber helpers ------------------------------------------------------


def _blocks_from_lines(page) -> list[Block]:
    """One block per text line.

    Finer-grained than PyMuPDF's paragraph blocks. That is the honest mapping:
    pdfminer exposes lines and words, and inventing paragraph grouping here would
    be a layout heuristic pretending to be extraction.
    """
    blocks: list[Block] = []
    for line in page.extract_text_lines():
        text = (line.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            Block(
                text=text,
                bbox=(
                    float(line["x0"]),
                    float(line["top"]),
                    float(line["x1"]),
                    float(line["bottom"]),
                ),
            )
        )
    return blocks


def _render_figure(rendered_page, page, page_index: int, placement: dict):
    """Render just this figure's region of the page, at its own scale.

    Rendering the whole page once and cropping is the obvious implementation and
    the wrong one: the scale then has to serve every figure on the page at once,
    so a scan placed small gets downsampled to keep a poster elsewhere on the
    page from exhausting memory. PDFium can rasterise a sub-region directly, so
    each figure gets the resolution it deserves and costs only its own pixels.
    """
    box = _bbox(placement)
    width_pt, height_pt = box[2] - box[0], box[3] - box[1]
    if width_pt <= 0 or height_pt <= 0:
        return None

    scale = _figure_scale(placement, width_pt, height_pt)
    # PDFium takes the amount to cut off each edge, not a rectangle. Negative is
    # rejected, and a placement can sit fractionally outside the page box.
    crop = (
        max(0.0, box[0]),
        max(0.0, float(page.height) - box[3]),
        max(0.0, float(page.width) - box[2]),
        max(0.0, box[1]),
    )

    try:
        image = rendered_page.render(scale=scale, crop=crop).to_pil()
    except Exception:
        # A single unrenderable placement must not cost the rest of the page.
        return None
    if image.width < 1 or image.height < 1:
        return None

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return ExtractedImage(
        page_index=page_index,
        bbox=box,
        width=image.width,
        height=image.height,
        data=buffer.getvalue(),
        mime="image/png",
    )


def _figure_scale(placement: dict, width_pt: float, height_pt: float) -> float:
    """Enough to reproduce the embedded image's own pixels, within the ceiling.

    `srcsize` is what the PDF actually stores. Matching it means a 600-DPI scan
    placed in a small box comes back at 600 DPI rather than as a blurred copy of
    a page that was captured perfectly well. Vector art has no `srcsize`, so it
    falls to the floor instead.
    """
    source = placement.get("srcsize") or ()
    scale = FIGURE_RENDER_SCALE
    if len(source) == 2 and float(source[0]) > 0:
        scale = max(scale, float(source[0]) / width_pt)

    longest_pt = max(width_pt, height_pt)
    if longest_pt > 0:
        scale = min(scale, MAX_FIGURE_PIXELS / longest_pt)

    return max(scale, 1.0)


def _vector_regions(page, placements: list[dict]) -> list[tuple[float, float, float, float]]:
    """Regions of the page that look like a drawing rather than layout.

    The whole difficulty is that a table is also lines and rectangles, and so is
    a page border, a header rule and an underline. pdfplumber can find tables, so
    those come out by subtraction; the rest come out by shape and by the fact
    that a drawing is a *cluster* — several primitives close together — while
    layout furniture is isolated and long.

    Returns bounding boxes, top-left origin, in points.
    """
    primitives = list(page.lines) + list(page.rects) + list(page.curves)
    if not primitives or len(primitives) > MAX_VECTOR_PRIMITIVES_PER_PAGE:
        if primitives:
            logger.debug(
                "Skipping vector detection on page %s: %d primitives",
                page.page_number,
                len(primitives),
            )
        return []

    excluded = _table_boxes(page) + [_bbox(p) for p in placements]
    page_area = float(page.width) * float(page.height)

    boxes = [
        box
        for primitive in primitives
        if not _is_rule(box := _primitive_box(primitive), page)
        and not any(_contained(box, region) for region in excluded)
    ]
    if not boxes:
        return []

    regions: list[tuple[float, float, float, float]] = []
    for cluster, members in _cluster(boxes):
        if members < MIN_VECTOR_PRIMITIVES:
            continue
        width, height = cluster[2] - cluster[0], cluster[3] - cluster[1]
        if width < MIN_VECTOR_SIDE or height < MIN_VECTOR_SIDE:
            continue
        if page_area and (width * height) / page_area > MAX_VECTOR_PAGE_FRACTION:
            # Almost certainly a border or a background fill that swallowed the
            # page. Describing it would describe the page, not a figure.
            continue
        regions.append(cluster)

    if regions:
        logger.debug("Page %s: %d vector region(s)", page.page_number, len(regions))
    return regions


def _cluster(boxes: list[tuple[float, float, float, float]]):
    """Merge boxes that sit within `VECTOR_CLUSTER_GAP` of each other.

    Yields `(bounding_box, member_count)`. Repeated passes rather than a proper
    union-find: page counts are small once rules and tables are gone, and the
    naive version is the one a reader can check.
    """
    clusters: list[tuple[tuple[float, float, float, float], int]] = [(b, 1) for b in boxes]

    merged = True
    while merged:
        merged = False
        result: list[tuple[tuple[float, float, float, float], int]] = []
        for box, count in clusters:
            for index, (other, other_count) in enumerate(result):
                if _near(box, other):
                    result[index] = (_union(box, other), other_count + count)
                    merged = True
                    break
            else:
                result.append((box, count))
        clusters = result

    return clusters


def _primitive_box(primitive: dict) -> tuple[float, float, float, float]:
    return (
        float(primitive["x0"]),
        float(primitive["top"]),
        float(primitive["x1"]),
        float(primitive["bottom"]),
    )


def _table_boxes(page) -> list[tuple[float, float, float, float]]:
    try:
        return [tuple(float(v) for v in table.bbox) for table in page.find_tables()]
    except Exception:  # noqa: BLE001 — detection is best-effort, not required
        return []


def _is_rule(box: tuple[float, float, float, float], page) -> bool:
    """A long flat line: header underline, table edge, page border."""
    width, height = box[2] - box[0], box[3] - box[1]
    if width <= 0 or height <= 0:
        # A zero-thickness line is exactly what a rule is; judge it by length.
        return max(width, height) > float(page.width) * 0.5
    longest, shortest = max(width, height), min(width, height)
    return shortest > 0 and longest / shortest > RULE_ASPECT_RATIO


def _contained(box, region) -> bool:
    return (
        box[0] >= region[0] - 2
        and box[1] >= region[1] - 2
        and box[2] <= region[2] + 2
        and box[3] <= region[3] + 2
    )


def _near(a, b) -> bool:
    gap = VECTOR_CLUSTER_GAP
    return not (
        a[0] > b[2] + gap or a[2] < b[0] - gap or a[1] > b[3] + gap or a[3] < b[1] - gap
    )


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _render_region(rendered_page, page, page_index: int, box):
    """Rasterise an inferred region. No `srcsize` to match, so use the floor."""
    return _render_figure(
        rendered_page,
        page,
        page_index,
        {"x0": box[0], "top": box[1], "x1": box[2], "bottom": box[3]},
    )


def _bbox(placement: dict) -> tuple[float, float, float, float]:
    return (
        float(placement["x0"]),
        float(placement["top"]),
        float(placement["x1"]),
        float(placement["bottom"]),
    )


# --- PyMuPDF helpers ---------------------------------------------------------


def _blocks_from_fitz(page) -> list[Block]:
    blocks: list[Block] = []
    for raw in page.get_text("dict").get("blocks", []):
        if raw.get("type") != 0:
            continue
        text = "".join(
            span.get("text", "")
            for line in raw.get("lines", [])
            for span in line.get("spans", [])
        )
        bbox_raw = raw.get("bbox")
        bbox = (
            tuple(float(v) for v in bbox_raw)
            if bbox_raw is not None and len(bbox_raw) == 4
            else None
        )
        blocks.append(Block(text=text, bbox=bbox))
    return blocks


def _first_rect(rects) -> tuple[float, float, float, float] | None:
    if not rects:
        return None
    rect = rects[0]
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _mime_for_ext(ext: str) -> str:
    normalized = ext.lower().lstrip(".")
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized == "png":
        return "image/png"
    if normalized:
        return f"image/{normalized}"
    return "image/png"


# --- imports and errors ------------------------------------------------------


def _import_pdfplumber():
    import pdfplumber

    return pdfplumber


def _import_pdfium():
    import pypdfium2

    return pypdfium2


def _import_fitz():
    try:
        import fitz
    except ImportError as exc:
        raise ImportError(_PYMUPDF_EXTRA_HINT) from exc
    return fitz


def _as_unreadable(path: str, exc: Exception) -> UnreadableDocumentError:
    """Map an engine's failure onto prismdoc's vocabulary.

    Encryption gets its own wording because it is the one case an operator can
    act on — supplying the file again unlocked, rather than filing a bug.
    """
    name = type(exc).__name__
    if "Password" in name or "password" in str(exc).lower():
        return UnreadableDocumentError(
            f"Cannot read {path!r}: PDF is encrypted/password-protected"
        )
    return UnreadableDocumentError(f"Cannot read {path!r}: {exc}")


def register_plugins() -> None:
    """Register the PDF engines under `pdf.*`, so config can name one."""
    from prismdoc.registry import register

    register("pdf.pdfplumber", PdfPlumberEngine)
    register("pdf.pymupdf", PyMuPDFEngine)


register_plugins()
