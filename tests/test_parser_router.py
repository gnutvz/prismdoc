"""Tests for T-055 provider router (classify_source + ParserRouterStage)."""

from __future__ import annotations

from pathlib import Path

import fitz

from prismdoc import (
    Context,
    Document,
    Page,
    ParserRouterStage,
    Source,
    classify_source,
    registry,
)
from prismdoc.config import _ensure_plugins
from prismdoc.stages.parse import register_plugins


def test_classify_source_image_text_spreadsheet_unknown(tmp_path: Path) -> None:
    png = tmp_path / "scan.png"
    png.write_bytes(b"")
    assert classify_source(Source(path=str(png))) == "image_scan"

    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    assert classify_source(Source(path=str(txt))) == "text"

    xlsx = tmp_path / "sheet.xlsx"
    xlsx.write_bytes(b"")
    assert classify_source(Source(path=str(xlsx))) == "spreadsheet"

    unknown = tmp_path / "blob.xyz"
    unknown.write_bytes(b"")
    assert classify_source(Source(path=str(unknown))) == "unknown"


def test_classify_source_pdf_digital_vs_scan(tmp_path: Path) -> None:
    digital_path = tmp_path / "digital.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Invoice total 12.50")
    pdf.save(digital_path)
    pdf.close()
    assert classify_source(Source(path=str(digital_path))) == "pdf_digital"

    scan_path = tmp_path / "scan.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.draw_rect(fitz.Rect(50, 50, 200, 200), color=(0, 0, 0), width=2)
    pdf.save(scan_path)
    pdf.close()
    assert classify_source(Source(path=str(scan_path))) == "pdf_scan"


def test_router_routes_text_to_passthrough() -> None:
    stage = ParserRouterStage(
        routes={"text": "parse.passthrough", "unknown": "parse.passthrough"}
    )
    doc = Document(
        source=Source(path="/tmp/notes.txt", mime="text/plain"),
        pages=[Page(index=0, text="hello world")],
    )
    result = stage.run(doc, Context())
    assert result.artifacts["parser_route"] == {
        "doc_type": "text",
        "parser": "parse.passthrough",
    }
    assert "parsed_markdown" in result.artifacts
    assert "hello world" in result.artifacts["parsed_markdown"]


def test_default_routes_map_pdf_and_image() -> None:
    routes = ParserRouterStage().routes
    assert routes["pdf_digital"] == "parse.pdfplumber"
    assert routes["pdf_scan"] == "parse.docling"
    assert routes["image_scan"] == "parse.docling"


def test_registry_config_and_export() -> None:
    register_plugins()
    assert "parse.router" in registry.get_keys()
    stage = registry.create("parse.router")
    assert isinstance(stage, ParserRouterStage)

    registry.clear()
    _ensure_plugins()
    assert "parse.router" in registry.get_keys()

    from prismdoc import ParserRouterStage as ExportedRouter
    from prismdoc import classify_source as exported_classify

    assert ExportedRouter is ParserRouterStage
    assert exported_classify is classify_source
