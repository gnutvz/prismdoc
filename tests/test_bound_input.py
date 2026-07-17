"""Tests for T-017 bound input + pre-flight budget."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from prismdoc import (
    BudgetExceededError,
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    InputTooLargeError,
    LLMClient,
    Page,
    Pipeline,
    Source,
    TargetSchema,
)
from prismdoc.api.app import app, get_runtime
from prismdoc.stages.extract import Completion
from prismdoc.tokens import count_tokens


def _product_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
            FieldSpec(name="price", type="number", required=True),
        ]
    )


_CANNED = [{"name": "Widget", "sku": "W-1", "price": 9.99}]


class TrackingLLMClient(LLMClient):
    """Records whether ``complete`` was invoked."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        self.calls += 1
        return Completion(text=self.response)


def test_count_tokens_heuristic_without_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "litellm" or name.startswith("litellm."):
            raise ImportError("forced offline heuristic")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    text = "abcd" * 10  # 40 chars -> 10 tokens via // 4
    assert count_tokens(text) == 10
    assert count_tokens("") == 1
    assert count_tokens("ab") == 1


def test_extract_stage_max_input_tokens_raises_before_client() -> None:
    doc = Document(
        source=Source(path="/tmp/big.md"),
        pages=[Page(index=0, text="x" * 4000)],
    )
    client = TrackingLLMClient(json.dumps(_CANNED))
    stage = ExtractStage(
        schema=_product_schema(),
        client=client,
        max_input_tokens=10,
    )

    with pytest.raises(InputTooLargeError, match="max_input_tokens"):
        stage.run(doc, Context())

    assert client.calls == 0


def test_extract_stage_preflight_budget_blocks_before_client() -> None:
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        pages=[Page(index=0, text="Widget W-1 9.99")],
    )
    client = TrackingLLMClient(json.dumps(_CANNED))
    stage = ExtractStage(
        schema=_product_schema(),
        client=client,
        model="gpt-4o",
    )
    ctx = Context(options={"budget_usd": 1e-12})

    with pytest.raises(BudgetExceededError, match="Projected cost"):
        stage.run(doc, ctx)

    assert client.calls == 0


def test_extract_stage_preflight_budget_allows_large_budget() -> None:
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        pages=[Page(index=0, text="Widget W-1 9.99")],
    )
    client = TrackingLLMClient(json.dumps(_CANNED))
    stage = ExtractStage(
        schema=_product_schema(),
        client=client,
        model="gpt-4o-mini",
    )
    ctx = Context(options={"budget_usd": 1000.0})

    result = stage.run(doc, ctx)

    assert client.calls == 1
    assert len(result.records) == 1


def test_input_too_large_error_exported() -> None:
    import prismdoc

    assert issubclass(prismdoc.InputTooLargeError, Exception)
    assert prismdoc.InputTooLargeError is InputTooLargeError


def _make_pdf(path: Path, pages: int = 1, text: str = "hello") -> None:
    pdf = fitz.open()
    for _ in range(pages):
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    pdf.save(path)
    pdf.close()


def _offline_runtime() -> tuple[Pipeline, Context]:
    from prismdoc import IngestStage, ParseStage

    schema = _product_schema()
    pipeline = Pipeline(
        [
            IngestStage(),
            ParseStage(),
            ExtractStage(
                schema=schema,
                client=TrackingLLMClient(json.dumps(_CANNED)),
            ),
        ]
    )
    return pipeline, Context(target_schema=schema)


def test_api_oversize_upload_returns_413(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRISMDOC_MAX_UPLOAD_BYTES", "32")
    pdf_path = tmp_path / "big.pdf"
    _make_pdf(pdf_path, text="this content is definitely larger than 32 bytes")

    app.dependency_overrides[get_runtime] = _offline_runtime
    try:
        client = TestClient(app)
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={"file": ("big.pdf", handle, "application/pdf")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 413
    assert "exceeds limit" in response.json()["detail"]


def test_api_too_many_pages_returns_413(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Over-max_pages is enforced inside ingest (once), mapped to HTTP 413."""
    from prismdoc.stages.ingest import PdfLoader

    monkeypatch.setenv("PRISMDOC_MAX_PAGES", "2")
    monkeypatch.setenv("PRISMDOC_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
    pdf_path = tmp_path / "many.pdf"
    _make_pdf(pdf_path, pages=3, text="page")

    load_calls = 0
    original_load = PdfLoader.load

    def counting_load(self: PdfLoader, source: Source) -> list:
        nonlocal load_calls
        load_calls += 1
        return original_load(self, source)

    monkeypatch.setattr(PdfLoader, "load", counting_load)

    app.dependency_overrides[get_runtime] = _offline_runtime
    try:
        client = TestClient(app)
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={"file": ("many.pdf", handle, "application/pdf")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "3 pages" in detail
    assert "2 pages" in detail
    assert load_calls == 1
