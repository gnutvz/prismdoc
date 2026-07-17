"""Sequential pipeline runner with per-stage tracing."""

from __future__ import annotations

import time

from prismdoc.models import Document, TraceEntry
from prismdoc.stages.base import Context, Stage


class Pipeline:
    """Runs a linear list of Stages, recording a TraceEntry for each."""

    def __init__(self, stages: list[Stage]) -> None:
        self.stages = stages

    def run(self, doc: Document, ctx: Context) -> Document:
        """Execute stages sequentially; re-raise failures with stage context."""
        for stage in self.stages:
            started = time.perf_counter()
            try:
                doc = stage.run(doc, ctx)
            except Exception as exc:
                duration_ms = (time.perf_counter() - started) * 1000.0
                doc.add_trace(
                    TraceEntry(
                        stage=stage.name,
                        ok=False,
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                )
                raise RuntimeError(f"Stage {stage.name} failed") from exc
            duration_ms = (time.perf_counter() - started) * 1000.0
            doc.add_trace(
                TraceEntry(
                    stage=stage.name,
                    ok=True,
                    duration_ms=duration_ms,
                )
            )
        return doc
