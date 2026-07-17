"""Tests for T-007 FastAPI serving (offline via dependency override)."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import prismdoc
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from prismdoc import (
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    IngestStage,
    LLMClient,
    NormalizeStage,
    ParseStage,
    Pipeline,
    Stage,
    TargetSchema,
    ValidateStage,
)
from prismdoc.api.app import app, get_runtime
from prismdoc.stages.extract import Completion

_CANNED = [
    {
        "name": "Widget A",
        "sku": "W-001",
        "price": 9.99,
        "currency": "USD",
    }
]


class FakeLLMClient(LLMClient):
    """Offline stand-in that returns a canned JSON array."""

    def __init__(self, response: str) -> None:
        self.response = response

    def complete(
        self, prompt: str, *, response_format: dict | None = None
    ) -> Completion:
        return Completion(text=self.response)


class BoomStage(Stage):
    """Stage that always fails (for the 422 path)."""

    name = "boom"

    def run(self, doc: Document, ctx: Context) -> Document:
        raise ValueError("intentional pipeline failure")


def _product_schema() -> TargetSchema:
    return TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string"),
            FieldSpec(name="price", type="number"),
            FieldSpec(name="currency", type="string"),
        ]
    )


def _offline_runtime() -> tuple[Pipeline, Context]:
    schema = _product_schema()
    pipeline = Pipeline(
        [
            IngestStage(),
            ParseStage(),
            ExtractStage(schema=schema, client=FakeLLMClient(json.dumps(_CANNED))),
            ValidateStage(schema=schema),
            NormalizeStage(),
        ]
    )
    return pipeline, Context(target_schema=schema)


def _failing_runtime() -> tuple[Pipeline, Context]:
    return Pipeline([BoomStage()]), Context()


def _make_pdf(path: Path, text: str) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    pdf.save(path)
    pdf.close()


def _make_xlsx(path: Path) -> None:
    wb = Workbook()
    sheet = wb.active
    assert sheet is not None
    sheet.append(["name", "sku", "price"])
    sheet.append(["Widget A", "W-001", "9.99"])
    wb.save(path)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_extract_with_fake_runtime_pdf(
    client: TestClient, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    _make_pdf(pdf_path, "Widget A W-001 9.99 USD")

    app.dependency_overrides[get_runtime] = _offline_runtime
    try:
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={
                    "file": ("catalog.pdf", handle, "application/pdf"),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["records"] == _CANNED
    assert body["validation"] is not None
    assert body["validation"]["valid"] == 1
    assert body["normalize"] == {"deduped": 0}
    assert [entry["stage"] for entry in body["trace"]] == [
        "ingest",
        "parse",
        "extract",
        "validate",
        "normalize",
    ]
    assert all(entry["ok"] for entry in body["trace"])
    assert all("duration_ms" in entry for entry in body["trace"])
    assert "cost" in body
    assert isinstance(body["cost"], dict)
    assert body["cost"]["unmetered_calls"] == 1
    assert body["cost"]["total_usd"] == 0.0


def test_extract_with_fake_runtime_xlsx(
    client: TestClient, tmp_path: Path
) -> None:
    xlsx_path = tmp_path / "catalog.xlsx"
    _make_xlsx(xlsx_path)

    app.dependency_overrides[get_runtime] = _offline_runtime
    try:
        with xlsx_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={
                    "file": (
                        "catalog.xlsx",
                        handle,
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet",
                    ),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["records"][0]["sku"] == "W-001"


def test_extract_pipeline_failure_returns_422(
    client: TestClient, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "fail.pdf"
    _make_pdf(pdf_path, "anything")

    app.dependency_overrides[get_runtime] = _failing_runtime
    try:
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={"file": ("fail.pdf", handle, "application/pdf")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Stage boom failed" in detail
    assert "intentional pipeline failure" in detail
    assert "Traceback" not in detail


def test_app_exported_from_prismdoc_api() -> None:
    from prismdoc.api import app as exported_app
    from prismdoc.api import get_runtime as exported_get_runtime

    assert exported_app is app
    assert exported_get_runtime is get_runtime


def test_app_version_matches_package() -> None:
    assert app.version == prismdoc.__version__


def test_get_runtime_raises_when_config_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRISMDOC_CONFIG", raising=False)
    get_runtime.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PRISMDOC_CONFIG is not set"):
            get_runtime()
    finally:
        get_runtime.cache_clear()


def test_get_runtime_returns_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    config_path = repo / "examples" / "retail" / "pipeline.yaml"
    monkeypatch.setenv("PRISMDOC_CONFIG", str(config_path))
    get_runtime.cache_clear()
    try:
        first = get_runtime()
        second = get_runtime()
        assert first is second
    finally:
        get_runtime.cache_clear()


def test_health_ok_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRISMDOC_CONFIG", raising=False)
    get_runtime.cache_clear()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_extract_runs_pipeline_via_run_in_threadpool(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipeline must leave the event loop via ``run_in_threadpool``."""
    import sys

    pdf_path = tmp_path / "catalog.pdf"
    _make_pdf(pdf_path, "Widget A W-001 9.99 USD")

    awaited: list[object] = []

    async def spy_run_in_threadpool(
        func: object, *args: object, **kwargs: object
    ) -> object:
        awaited.append(func)
        return func(*args, **kwargs)  # type: ignore[operator]

    # ``prismdoc.api.app`` attribute is the FastAPI instance; patch the module.
    app_module = sys.modules["prismdoc.api.app"]
    monkeypatch.setattr(app_module, "run_in_threadpool", spy_run_in_threadpool)

    app.dependency_overrides[get_runtime] = _offline_runtime
    try:
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/extract",
                files={"file": ("catalog.pdf", handle, "application/pdf")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(awaited) == 1
    run_fn = awaited[0]
    assert getattr(run_fn, "__name__", "") == "run"
    assert isinstance(getattr(run_fn, "__self__", None), Pipeline)
