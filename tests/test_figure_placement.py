"""Where a figure's text lands in the Markdown, and when it cannot land right.

Extraction and description were the visible half of the figure pipeline. This is
the half that fails quietly: a page-1 diagram appended after page 40's prose is
still "extracted", still "described", and still wrong — the chunker pairs the
description with unrelated text, and retrieval returns a figure explanation
attached to the wrong subject.

Placement needs page boundaries in the Markdown, so it is the *parser* that
decides whether it can work. PassthroughParser and PdfPlumberParser emit
"## Page N"; DoclingParser returns whole-document Markdown with no page concept.
That is a real constraint rather than a bug — what would be a bug is losing
placement without saying so.
"""

from __future__ import annotations

import logging

import pytest

from prismdoc.stages.figures import _insert_placeholders


def markdown_with_pages(count: int) -> str:
    return "\n\n".join(f"## Page {i}\n\nBody of page {i}." for i in range(count))


class TestPlacementWithPageMarkers:
    def test_a_figure_lands_on_its_own_page(self):
        result = _insert_placeholders(markdown_with_pages(3), {0: ["fig_0_0"]})

        assert result.index("[[FIGURE:fig_0_0]]") < result.index("## Page 1")

    def test_figures_from_different_pages_do_not_pool_at_the_end(self):
        result = _insert_placeholders(
            markdown_with_pages(3), {0: ["fig_0_0"], 2: ["fig_2_0"]}
        )

        assert result.index("[[FIGURE:fig_0_0]]") < result.index("## Page 1")
        assert result.index("[[FIGURE:fig_2_0]]") > result.index("## Page 2")

    def test_several_figures_on_one_page_keep_their_order(self):
        result = _insert_placeholders(
            markdown_with_pages(2), {0: ["fig_0_0", "fig_0_1"]}
        )

        assert result.index("[[FIGURE:fig_0_0]]") < result.index("[[FIGURE:fig_0_1]]")


class TestPlacementWithoutPageMarkers:
    """The degraded path — allowed, but not allowed to be silent."""

    def test_figures_are_appended_rather_than_dropped(self):
        result = _insert_placeholders("Flat markdown, no page headers.", {0: ["fig_0_0"]})

        assert "[[FIGURE:fig_0_0]]" in result
        assert result.startswith("Flat markdown")

    def test_it_says_so(self, caplog):
        """Silence here is what makes the quality loss invisible downstream."""
        with caplog.at_level(logging.WARNING, logger="prismdoc.stages.figures"):
            _insert_placeholders("Flat markdown.", {0: ["fig_0_0"], 5: ["fig_5_0"]})

        assert "No page markers" in caplog.text
        assert "2 figure(s)" in caplog.text
        # An operator reading this needs the fix, not just the diagnosis.
        assert "pdfplumber" in caplog.text

    def test_no_warning_when_there_are_no_figures_to_place(self, caplog):
        with caplog.at_level(logging.WARNING, logger="prismdoc.stages.figures"):
            _insert_placeholders("Flat markdown.", {})

        assert caplog.text == ""


class TestParsersThatSupportPlacement:
    @pytest.mark.parametrize("parser_key", ["parser.passthrough", "parser.pdfplumber"])
    def test_parser_emits_page_markers(self, parser_key, tmp_path):
        """Pinning the contract the merge stage depends on, per parser."""
        from prismdoc import registry
        from prismdoc.models import Document, Page, Source

        parser = registry.create(parser_key)
        if parser.name == "passthrough":
            doc = Document(source=Source(path="x.pdf"))
            doc.pages = [Page(index=0, text="one"), Page(index=1, text="two")]
            assert "## Page 0" in parser.parse(doc)
            return

        pdf = tmp_path / "two_pages.pdf"
        pdf.write_bytes(
            (
                __import__("pathlib").Path(__file__).parent / "fixtures" / "page_with_figure.pdf"
            ).read_bytes()
        )
        assert "## Page 0" in parser.parse(Document(source=Source(path=str(pdf))))
