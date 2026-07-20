"""Tests for T-051 CliLLMClient and TextLoader adapters."""

from __future__ import annotations

import sys
from pathlib import Path

from prismdoc import CliLLMClient, Context, Document, IngestStage, Source, registry
from prismdoc.stages.extract import Completion
from prismdoc.stages.ingest import TextLoader, register_plugins


def test_cli_llm_client_passes_prompt_as_final_argv() -> None:
    client = CliLLMClient(
        [sys.executable, "-c", "import sys; print(sys.argv[-1])"]
    )
    completion = client.complete("hi")

    assert isinstance(completion, Completion)
    assert completion.text == "hi"
    assert completion.usage is None


def test_cli_llm_client_missing_binary_returns_empty() -> None:
    completion = CliLLMClient(["definitely-not-a-binary-xyz"]).complete("hi")

    assert isinstance(completion, Completion)
    assert completion.text == ""
    assert completion.usage is None


def test_text_loader_txt_and_md(tmp_path: Path) -> None:
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("plain text body", encoding="utf-8")
    md_path = tmp_path / "notes.md"
    md_path.write_text("# heading\nmarkdown body", encoding="utf-8")

    txt_pages = TextLoader().load(Source(path=str(txt_path), mime="text/plain"))
    md_pages = TextLoader().load(Source(path=str(md_path), mime="text/markdown"))

    assert len(txt_pages) == 1
    assert txt_pages[0].index == 0
    assert txt_pages[0].text == "plain text body"

    assert len(md_pages) == 1
    assert md_pages[0].index == 0
    assert md_pages[0].text == "# heading\nmarkdown body"


def test_ingest_stage_defaults_include_text_loader(tmp_path: Path) -> None:
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("ingested via default loaders", encoding="utf-8")
    doc = Document(source=Source(path=str(txt_path), mime="text/plain"))

    result = IngestStage().run(doc, Context())

    assert len(result.pages) == 1
    assert result.pages[0].text == "ingested via default loaders"


def test_loader_text_registered() -> None:
    register_plugins()
    assert "loader.text" in registry.get_keys()
    loader = registry.create("loader.text")
    assert isinstance(loader, TextLoader)
    assert loader.name == "text"
    assert loader.extensions == (".txt", ".md")


def test_cli_llm_client_export() -> None:
    from prismdoc import CliLLMClient as Exported

    assert Exported is CliLLMClient
