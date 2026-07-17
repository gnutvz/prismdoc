"""FastAPI service: health check and document extraction endpoint."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile

from prismdoc.config import load_pipeline
from prismdoc.models import Document, Source
from prismdoc.pipeline import Pipeline
from prismdoc.stages.base import Context

_DEFAULT_CONFIG = "examples/retail/pipeline.yaml"

app = FastAPI(
    title="prismdoc",
    description="Schema-driven document extraction microservice",
    version="0.0.0",
)


def get_runtime() -> tuple[Pipeline, Context]:
    """Build ``(Pipeline, Context)`` from server-side YAML config.

    Default config is ``examples/retail/pipeline.yaml``; override with the
    ``PRISMDOC_CONFIG`` environment variable. Declared as a FastAPI dependency
    so tests can inject a fake-LLM pipeline via ``app.dependency_overrides``.
    """
    config_path = os.environ.get("PRISMDOC_CONFIG", _DEFAULT_CONFIG)
    return load_pipeline(config_path)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe; does not touch the LLM or pipeline config."""
    return {"status": "ok"}


@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    runtime: tuple[Pipeline, Context] = Depends(get_runtime),
) -> dict[str, Any]:
    """Upload a document and return structured extraction results."""
    pipeline, ctx = runtime
    suffix = Path(file.filename or "upload").suffix
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            tmp_path = handle.name
            handle.write(await file.read())

        doc = Document(
            source=Source(path=tmp_path, mime=file.content_type),
        )
        try:
            doc = pipeline.run(doc, ctx)
        except Exception as exc:
            detail = _pipeline_error_detail(exc)
            raise HTTPException(status_code=422, detail=detail) from None

        return {
            "records": [record.fields for record in doc.records],
            "validation": doc.artifacts.get("validation"),
            "normalize": doc.artifacts.get("normalize"),
            "trace": [
                {
                    "stage": entry.stage,
                    "ok": entry.ok,
                    "duration_ms": entry.duration_ms,
                }
                for entry in doc.trace
            ],
        }
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


def _pipeline_error_detail(exc: BaseException) -> str:
    """Return a clear client-facing message without a stack trace."""
    parts = [str(exc).strip() or type(exc).__name__]
    cause = exc.__cause__
    if cause is not None:
        cause_msg = str(cause).strip() or type(cause).__name__
        if cause_msg not in parts[0]:
            parts.append(cause_msg)
    return ": ".join(parts)
