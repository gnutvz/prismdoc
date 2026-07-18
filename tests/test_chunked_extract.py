"""Tests for T-035 chunked extract (chunk → extract → merge/dedup)."""

from __future__ import annotations

import json

from prismdoc import (
    ChunkedExtractStage,
    Context,
    Document,
    FieldSpec,
    LLMClient,
    Page,
    Source,
    TargetSchema,
    chunk_text,
    registry,
)
from prismdoc.stages.chunked_extract import register_plugins
from prismdoc.stages.extract import Completion


def _product_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
        ]
    )


class SpyLLMClient(LLMClient):
    """Returns a different canned payload per call; records call count."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        self.prompts.append(prompt)
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return Completion(text=self.responses[idx])


def test_chunk_text_splits_at_newlines_preserves_order() -> None:
    text = "aaa\nbbb\nccc\nddd\neee"
    chunks = chunk_text(text, max_chars=7)

    assert all(len(c) <= 7 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")
    # Boundaries only at newlines: each original line is intact inside some chunk.
    for line in text.splitlines():
        assert any(line in chunk for chunk in chunks)
    # Order preserved across chunks.
    flat = "\n".join(chunks)
    assert flat == "aaa\nbbb\nccc\nddd\neee" or flat.startswith("aaa")
    positions = [flat.find(line) for line in ["aaa", "bbb", "ccc", "ddd", "eee"]]
    assert positions == sorted(positions)


def test_chunk_text_long_line_becomes_own_chunk() -> None:
    long = "x" * 20
    text = f"short\n{long}\nok"
    chunks = chunk_text(text, max_chars=10)

    assert long in chunks
    assert all(len(c) <= 10 or c == long for c in chunks)
    assert chunks.index(long) == 1


def test_chunk_text_drops_empty_chunks() -> None:
    assert chunk_text("", 10) == []


def test_chunked_extract_concatenates_and_dedups() -> None:
    # Two chunks: first has A+B, second has B (dup) + C.
    responses = [
        json.dumps(
            {
                "records": [
                    {"name": "A", "sku": "1"},
                    {"name": "B", "sku": "2"},
                ]
            }
        ),
        json.dumps(
            {
                "records": [
                    {"name": "B", "sku": "2"},
                    {"name": "C", "sku": "3"},
                ]
            }
        ),
    ]
    client = SpyLLMClient(responses)
    # Force two chunks with small max_chunk_chars.
    doc = Document(
        source=Source(path="/tmp/long.md"),
        artifacts={"parsed_markdown": "line-one-aaaa\nline-two-bbbb"},
    )
    result = ChunkedExtractStage(
        schema=_product_schema(),
        client=client,
        max_chunk_chars=12,
    ).run(doc, Context())

    assert client.calls == 2
    assert len(result.records) == 3
    assert [r.fields["name"] for r in result.records] == ["A", "B", "C"]
    chunking = result.artifacts["chunking"]
    assert chunking["chunks"] == 2
    assert chunking["records_before_dedup"] == 4
    assert chunking["records_after"] == 3


def test_chunked_extract_single_chunk_like_normal() -> None:
    payload = json.dumps(
        {"records": [{"name": "Solo", "sku": "S-1"}, {"name": "Duo", "sku": "S-2"}]}
    )
    client = SpyLLMClient([payload])
    doc = Document(
        source=Source(path="/tmp/short.md"),
        pages=[Page(index=0, text="short catalog")],
    )
    result = ChunkedExtractStage(
        schema=_product_schema(),
        client=client,
        max_chunk_chars=8000,
    ).run(doc, Context())

    assert client.calls == 1
    assert result.artifacts["chunking"]["chunks"] == 1
    assert result.artifacts["chunking"]["records_before_dedup"] == 2
    assert result.artifacts["chunking"]["records_after"] == 2
    assert len(result.records) == 2
    assert result.records[0].fields["sku"] == "S-1"
    assert result.records[1].fields["name"] == "Duo"


def test_chunked_extract_client_called_once_per_chunk() -> None:
    responses = [
        json.dumps({"records": [{"name": f"N{i}", "sku": str(i)}]}) for i in range(3)
    ]
    client = SpyLLMClient(responses)
    doc = Document(
        source=Source(path="/tmp/multi.md"),
        artifacts={"parsed_markdown": "aaaa\nbbbb\ncccc"},
    )
    ChunkedExtractStage(
        schema=_product_schema(),
        client=client,
        max_chunk_chars=4,
    ).run(doc, Context())

    assert len(chunk_text("aaaa\nbbbb\ncccc", 4)) == 3
    assert client.calls == 3


def test_chunked_extract_exports_and_registry() -> None:
    import prismdoc

    assert prismdoc.ChunkedExtractStage is ChunkedExtractStage
    assert prismdoc.chunk_text is chunk_text

    register_plugins()
    assert "extract.chunked" in registry.get_keys()
    stage = registry.create("extract.chunked", schema=_product_schema())
    assert isinstance(stage, ChunkedExtractStage)
