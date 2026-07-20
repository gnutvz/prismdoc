"""Tests for T-049 PyMuPDF4LLMParser (engine-swap-by-config proof)."""

from __future__ import annotations

import builtins
from pathlib import Path

import fitz
import pytest

from prismdoc import Document, ParseStage, Source, registry
from prismdoc.stages.parse import PyMuPDF4LLMParser, register_plugins


def _make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_pymupdf4llm_parser_extracts_text_and_total(tmp_path: Path) -> None:
    pytest.importorskip("pymupdf4llm")
    pdf_path = tmp_path / "invoice.pdf"
    _make_pdf(pdf_path, "Invoice line item\nTotal 8.25")

    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    md = PyMuPDF4LLMParser().parse(doc)

    assert "Invoice line item" in md
    assert "8.25" in md


def test_pymupdf4llm_registry_keys() -> None:
    register_plugins()
    keys = registry.get_keys()
    assert "parser.pymupdf4llm" in keys
    assert "parse.pymupdf4llm" in keys

    parser = registry.create("parser.pymupdf4llm")
    assert isinstance(parser, PyMuPDF4LLMParser)
    assert parser.name == "pymupdf4llm"

    stage = registry.create("parse.pymupdf4llm")
    assert isinstance(stage, ParseStage)
    assert isinstance(stage.parser, PyMuPDF4LLMParser)


def test_pymupdf4llm_raises_clear_import_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pymupdf4llm" or name.startswith("pymupdf4llm."):
            raise ImportError("No module named 'pymupdf4llm'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    pdf_path = tmp_path / "missing.pdf"
    _make_pdf(pdf_path, "x")
    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    with pytest.raises(ImportError, match=r"prismdoc\[pymupdf4llm\]"):
        PyMuPDF4LLMParser().parse(doc)
