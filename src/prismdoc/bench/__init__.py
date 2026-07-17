"""Offline OCR-recall benchmark harness (dataset-format-agnostic)."""

from __future__ import annotations

from prismdoc.bench.dataset import BenchSample, load_manifest
from prismdoc.bench.ocr_recall import sample_recall, value_found
from prismdoc.bench.runner import BenchReport, run_ocr_recall

__all__ = [
    "BenchReport",
    "BenchSample",
    "load_manifest",
    "run_ocr_recall",
    "sample_recall",
    "value_found",
]
