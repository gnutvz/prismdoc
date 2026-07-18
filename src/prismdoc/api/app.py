"""FastAPI service: health check and document extraction endpoint."""

from __future__ import annotations

import functools
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from prismdoc import __version__
from prismdoc.config import load_pipeline
from prismdoc.cost import CostLedger
from prismdoc.errors import InputTooLargeError
from prismdoc.models import Document, Source
from prismdoc.pipeline import Pipeline
from prismdoc.stages.base import Context

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
        # Per-request context so concurrent extracts do not race on shared options.
        request_ctx = Context(
            target_schema=ctx.target_schema,
            options={**ctx.options, "max_pages": max_pages},
        )
        try:
            doc = await run_in_threadpool(pipeline.run, doc, request_ctx)
        except Exception as exc:
            detail = _pipeline_error_detail(exc)
            status = 413 if _caused_by_input_too_large(exc) else 422
            raise HTTPException(status_code=status, detail=detail) from None

        cost = doc.artifacts.get("cost")
        return {
            "records": [record.fields for record in doc.records],
            "validation": doc.artifacts.get("validation"),
            "normalize": doc.artifacts.get("normalize"),
            "confidence": [record.confidence for record in doc.records],
            "low_confidence": doc.artifacts.get("low_confidence"),
            "provenance": [
                {
                    field: prov.model_dump()
                    for field, prov in record.provenance.items()
                }
                for record in doc.records
            ],
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


def _caused_by_input_too_large(exc: BaseException) -> bool:
    """True if ``exc`` or any ``__cause__`` is an ``InputTooLargeError``."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, InputTooLargeError):
            return True
        current = current.__cause__
    return False


def _pipeline_error_detail(exc: BaseException) -> str:
    """Return a clear client-facing message without a stack trace."""
    parts = [str(exc).strip() or type(exc).__name__]
    cause = exc.__cause__
    if cause is not None:
        cause_msg = str(cause).strip() or type(cause).__name__
        if cause_msg not in parts[0]:
            parts.append(cause_msg)
    return ": ".join(parts)
