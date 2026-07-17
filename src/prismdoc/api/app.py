"""FastAPI service: health check and document extraction endpoint."""

from __future__ import annotations

import functools
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile

from prismdoc import __version__
from prismdoc.config import load_pipeline
from prismdoc.cost import CostLedger
from prismdoc.models import Document, Source
from prismdoc.pipeline import Pipeline
from prismdoc.stages.base import Context
from prismdoc.stages.ingest import IngestStage

app = FastAPI(
    title="prismdoc",
    description="Schema-driven document extraction microservice",
    version=__version__,
)

_DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
_DEFAULT_MAX_PAGES = 200


@functools.lru_cache(maxsize=1)
def get_runtime() -> tuple[Pipeline, Context]:
    """Build ``(Pipeline, Context)`` from server-side YAML config.

    Reads ``PRISMDOC_CONFIG`` (required). The result is cached so repeated
    requests reuse one pipeline instance. Declared as a FastAPI dependency so
    tests can inject a fake-LLM pipeline via ``app.dependency_overrides``.
    """
    config_path = os.environ.get("PRISMDOC_CONFIG")
    if not config_path:
        raise RuntimeError("PRISMDOC_CONFIG is not set")
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
    max_upload_bytes = _env_int(
        "PRISMDOC_MAX_UPLOAD_BYTES", _DEFAULT_MAX_UPLOAD_BYTES
    )
    max_pages = _env_int("PRISMDOC_MAX_PAGES", _DEFAULT_MAX_PAGES)

    suffix = Path(file.filename or "upload").suffix
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            tmp_path = handle.name
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Upload size {total} bytes exceeds limit of "
                            f"{max_upload_bytes} bytes"
                        ),
                    )
                handle.write(chunk)

        doc = Document(
            source=Source(path=tmp_path, mime=file.content_type),
        )
        try:
            preview = IngestStage().run(
                Document(source=Source(path=tmp_path, mime=file.content_type)),
                ctx,
            )
        except Exception as exc:
            detail = _pipeline_error_detail(exc)
            raise HTTPException(status_code=422, detail=detail) from None

        page_count = len(preview.pages)
        if page_count > max_pages:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Document has {page_count} pages, which exceeds the limit "
                    f"of {max_pages} pages"
                ),
            )

        try:
            doc = pipeline.run(doc, ctx)
        except Exception as exc:
            detail = _pipeline_error_detail(exc)
            raise HTTPException(status_code=422, detail=detail) from None

        cost = doc.artifacts.get("cost")
        return {
            "records": [record.fields for record in doc.records],
            "validation": doc.artifacts.get("validation"),
            "normalize": doc.artifacts.get("normalize"),
            "confidence": [record.confidence for record in doc.records],
            "low_confidence": doc.artifacts.get("low_confidence"),
            "trace": [
                {
                    "stage": entry.stage,
                    "ok": entry.ok,
                    "duration_ms": entry.duration_ms,
                }
                for entry in doc.trace
            ],
            "cost": cost.model_dump() if isinstance(cost, CostLedger) else cost,
        }
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _pipeline_error_detail(exc: BaseException) -> str:
    """Return a clear client-facing message without a stack trace."""
    parts = [str(exc).strip() or type(exc).__name__]
    cause = exc.__cause__
    if cause is not None:
        cause_msg = str(cause).strip() or type(cause).__name__
        if cause_msg not in parts[0]:
            parts.append(cause_msg)
    return ": ".join(parts)
