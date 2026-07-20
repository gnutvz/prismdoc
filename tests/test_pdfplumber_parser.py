"""Tests for T-048 PdfPlumberParser (engine-swap-by-config proof)."""

from __future__ import annotations

import builtins
from pathlib import Path

import fitz
import pytest

from prismdoc import (
    Context,
    Document,
    Page,
    ParseStage,
    Parser,
    PassthroughParser,
    Source,
    registry,
)
from prismdoc.stages.parse import PdfPlumberParser, register_plugins
from prismdoc.stages.verify import parse_markdown_tables


def _make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_pdfplumber_parser_extracts_text_and_total(tmp_path: Path) -> None:
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "invoice.pdf"
    _make_pdf(pdf_path, "Invoice line item\nTotal 8.25")

    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    md = PdfPlumberParser().parse(doc)

    assert "Invoice line item" in md
    assert "8.25" in md


def test_pdfplumber_registry_keys() -> None:
    register_plugins()
    keys = registry.get_keys()
    assert "parser.pdfplumber" in keys
    assert "parse.pdfplumber" in keys

    parser = registry.create("parser.pdfplumber")
    assert isinstance(parser, PdfPlumberParser)
    assert parser.name == "pdfplumber"

    stage = registry.create("parse.pdfplumber")
    assert isinstance(stage, ParseStage)
    assert isinstance(stage.parser, PdfPlumberParser)


def _parse_then_tables(parser: Parser, doc: Document) -> tuple[str, list]:
    """Shared downstream: ParseStage → parse_markdown_tables (unchanged)."""
    out = ParseStage(parser=parser).run(doc, Context())
    md = out.artifacts["parsed_markdown"]
    assert isinstance(md, str)
    return md, parse_markdown_tables(md)


def test_engine_swap_same_downstream(tmp_path: Path) -> None:
    """Only the parser differs; ParseStage + parse_markdown_tables stay the same."""
    pytest.importorskip("pdfplumber")
    pdf_path = tmp_path / "swap.pdf"
    body = "Widget purchase\nTotal 8.25"
    _make_pdf(pdf_path, body)

    pdf_doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    pass_doc = Document(
        source=Source(path=str(pdf_path), mime="application/pdf"),
        pages=[
            Page(
                index=0,
                text=(
                    "Widget purchase\n\n"
                    "| Description | Gross worth |\n"
                    "|---|---|\n"
                    "| Widget | 8.25 |"
                ),
            )
        ],
    )

    md_pp, tables_pp = _parse_then_tables(PdfPlumberParser(), pdf_doc)
    md_pt, tables_pt = _parse_then_tables(PassthroughParser(), pass_doc)

    assert "8.25" in md_pp
    assert "8.25" in md_pt
    assert isinstance(tables_pp, list)
    assert isinstance(tables_pt, list)
    # Passthrough carries an explicit GFM table; both paths used the same helper.
    assert len(tables_pt) == 1
    assert tables_pt[0][0][-1] == "Gross worth"


def test_pdfplumber_raises_clear_import_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pdfplumber" or name.startswith("pdfplumber."):
            raise ImportError("No module named 'pdfplumber'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    pdf_path = tmp_path / "missing.pdf"
    _make_pdf(pdf_path, "x")
    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    with pytest.raises(ImportError, match=r"prismdoc\[pdfplumber\]"):
        PdfPlumberParser().parse(doc)
