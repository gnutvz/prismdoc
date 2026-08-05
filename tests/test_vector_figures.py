"""Finding diagrams that were drawn rather than embedded.

Every other figure path is extraction: the picture is a file inside the
document, and it is either there or it is not. This one is inference. A
schematic and a table are both lines and rectangles, and so are a page border, a
header underline and a form field — so the question is not "can we find shapes"
but "can we tell a drawing from furniture".

That asymmetry decides how it is tuned. A missed diagram costs one diagram. A
false positive costs a model call and puts a paragraph describing a page border
into the retrieval index, where it will be returned as if it were content. So
these tests care more about what is *rejected* than about what is found, and the
feature is off unless asked for.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz", reason="fixtures are built with PyMuPDF")

from prismdoc.pdf import PdfPlumberEngine  # noqa: E402


def render(tmp_path, name: str, draw) -> str:
    document = fitz.open()
    page = document.new_page()
    draw(page)
    path = tmp_path / name
    document.save(path)
    document.close()
    return str(path)


def diagram(page, x: float = 80, y: float = 100) -> None:
    """Two boxes and a connector — the shape most architecture drawings take."""
    page.draw_rect(fitz.Rect(x, y, x + 120, y + 50), width=1)
    page.insert_text((x + 15, y + 30), "API Gateway")
    page.draw_rect(fitz.Rect(x + 200, y, x + 320, y + 50), width=1)
    page.insert_text((x + 220, y + 30), "Lambda")
    page.draw_line(fitz.Point(x + 120, y + 25), fitz.Point(x + 200, y + 25))


def ruled_table(page, top: float = 250) -> None:
    for row in range(4):
        page.draw_line(fitz.Point(80, top + row * 20), fitz.Point(400, top + row * 20))
    for col in range(4):
        page.draw_line(fitz.Point(80 + col * 107, top), fitz.Point(80 + col * 107, top + 60))
    page.insert_text((90, top + 15), "SKU")
    page.insert_text((200, top + 15), "Price")


def found(path: str, detect: bool = True):
    return PdfPlumberEngine(detect_vector_figures=detect).extract_images(path)


class TestOffByDefault:
    def test_a_drawing_is_invisible_unless_asked_for(self, tmp_path):
        path = render(tmp_path, "d.pdf", diagram)

        assert found(path, detect=False) == []
        assert len(found(path)) == 1

    def test_the_default_engine_does_not_detect(self):
        """Inference must be opted into, never inherited from a default."""
        assert PdfPlumberEngine().detect_vector_figures is False


class TestWhatItFinds:
    def test_a_two_box_diagram(self, tmp_path):
        path = render(tmp_path, "d.pdf", diagram)

        images = found(path)

        assert len(images) == 1
        # The region spans both boxes and the connector between them.
        assert images[0].bbox[0] == pytest.approx(80, abs=3)
        assert images[0].bbox[2] == pytest.approx(400, abs=3)
        assert images[0].data.startswith(b"\x89PNG")

    def test_two_separate_drawings_stay_separate(self, tmp_path):
        def draw(page):
            diagram(page, y=100)
            diagram(page, y=400)

        assert len(found(render(tmp_path, "two.pdf", draw))) == 2


class TestWhatItRejects:
    """The half that decides whether this is usable on a real corpus."""

    def test_a_ruled_table_is_not_a_diagram(self, tmp_path):
        """Tables are lines and rectangles too — the main source of false hits."""
        assert found(render(tmp_path, "t.pdf", ruled_table)) == []

    def test_a_table_beside_a_diagram_leaves_only_the_diagram(self, tmp_path):
        def draw(page):
            diagram(page)
            ruled_table(page)

        images = found(render(tmp_path, "both.pdf", draw))

        assert len(images) == 1
        assert images[0].bbox[3] < 200, "the table was swept into the region"

    def test_a_header_rule_is_not_a_diagram(self, tmp_path):
        def draw(page):
            page.insert_text((72, 60), "Quarterly report")
            page.draw_line(fitz.Point(72, 70), fitz.Point(523, 70))
            page.draw_line(fitz.Point(72, 780), fitz.Point(523, 780))

        assert found(render(tmp_path, "rules.pdf", draw)) == []

    def test_a_page_border_does_not_swallow_the_page(self, tmp_path):
        """Otherwise every bordered page becomes one page-sized 'figure'."""
        def draw(page):
            page.draw_rect(fitz.Rect(40, 40, 555, 800), width=1)
            page.insert_text((72, 100), "Body text.")

        assert found(render(tmp_path, "border.pdf", draw)) == []

    def test_a_lone_box_is_not_a_diagram(self, tmp_path):
        """One rectangle is a callout, a form field or a stamp."""
        def draw(page):
            page.draw_rect(fitz.Rect(80, 100, 200, 150), width=1)

        assert found(render(tmp_path, "one.pdf", draw)) == []

    def test_a_tiny_cluster_is_not_a_diagram(self, tmp_path):
        """Checkbox groups and icons cluster too, and are not worth a model call."""
        def draw(page):
            for i in range(4):
                page.draw_rect(fitz.Rect(80 + i * 6, 100, 84 + i * 6, 104), width=1)

        assert found(render(tmp_path, "tiny.pdf", draw)) == []

    def test_a_page_of_text_finds_nothing(self, tmp_path):
        def draw(page):
            for i in range(20):
                page.insert_text((72, 80 + i * 20), f"Line {i} of ordinary prose.")

        assert found(render(tmp_path, "text.pdf", draw)) == []


class TestOrdering:
    def test_embedded_images_keep_their_numbering(self, tmp_path):
        """An inferred figure must never renumber an extracted one — ids are
        already in the Markdown by the time these are appended."""
        from PIL import Image

        png = tmp_path / "a.png"
        Image.new("RGB", (80, 60), (0, 128, 255)).save(png)

        def draw(page):
            page.insert_image(fitz.Rect(80, 60, 160, 120), filename=str(png))
            diagram(page, y=300)

        images = found(render(tmp_path, "mixed.pdf", draw))

        assert len(images) == 2
        # Embedded first, inferred second, regardless of position on the page.
        assert images[0].bbox[1] < 200
        assert images[1].bbox[1] > 200
