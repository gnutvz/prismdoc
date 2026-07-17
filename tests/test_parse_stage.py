"""Tests for T-003 parse stage (passthrough default, Docling optional)."""

from __future__ import annotations

import builtins

import pytest

from prismdoc import (
    Block,
    Context,
    Document,
    Page,
    ParseStage,
    Parser,
    PassthroughParser,
    Pipeline,
    Source,
    registry,
)
from prismdoc.stages.parse import DoclingParser, register_plugins


def _two_page_doc() -> Document:
    return Document(
        source=Source(path="/tmp/sample.pdf", mime="application/pdf"),
        pages=[
            Page(
                index=0,
                text="Alpha page text",
                blocks=[Block(text="Alpha block")],
            ),
            Page(index=1, text="Beta page text"),
        ],
    )


def test_passthrough_parser_includes_both_pages() -> None:
    md = PassthroughParser().parse(_two_page_doc())

    assert "## Page 0" in md
    assert "## Page 1" in md
    assert "Alpha page text" in md
    assert "Beta page text" in md
    assert "Alpha block" in md


def test_parse_stage_via_pipeline_sets_artifact() -> None:
    doc = _two_page_doc()
    result = Pipeline([ParseStage()]).run(doc, Context())

    parsed = result.artifacts.get("parsed_markdown")
    assert isinstance(parsed, str)
    assert parsed.strip()
    assert "Alpha page text" in parsed
    assert "Beta page text" in parsed
    assert len(result.trace) == 1
    assert result.trace[0].stage == "parse"
    assert result.trace[0].ok is True
    assert result.trace[0].duration_ms >= 0


def test_docling_parser_skipped_without_extra() -> None:
    pytest.importorskip("docling")

    # With docling installed, ensure the adapter is constructible; full
    # conversion against a real file is outside the lightweight-env DoD.
    parser = DoclingParser()
    assert parser.name == "docling"
    assert isinstance(parser, Parser)


def test_docling_parser_raises_clear_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "docling" or name.startswith("docling."):
            raise ImportError("No module named 'docling'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    doc = Document(source=Source(path="/tmp/sample.pdf", mime="application/pdf"))
    with pytest.raises(ImportError, match=r"prismdoc\[docling\]"):
        DoclingParser().parse(doc)


def test_parse_exports_and_registry() -> None:
    assert issubclass(ParseStage, object)
    assert issubclass(Parser, object)
    assert issubclass(PassthroughParser, Parser)

    register_plugins()
    keys = registry.get_keys()
    assert "parser.passthrough" in keys
    assert "parser.docling" in keys
    assert "parse.default" in keys

    stage = registry.create("parse.default")
    assert isinstance(stage, ParseStage)
    assert isinstance(registry.create("parser.passthrough"), PassthroughParser)
    assert isinstance(registry.create("parser.docling"), DoclingParser)
