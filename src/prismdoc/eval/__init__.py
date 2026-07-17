"""Offline evaluation harness: per-field accuracy vs ground truth."""

from __future__ import annotations

from prismdoc.eval.dataset import EvalCase, EvalDataset, load_dataset
from prismdoc.eval.metrics import align_records, field_metrics, values_match
from prismdoc.eval.runner import CaseResult, EvalReport, run_case, run_eval

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalDataset",
    "EvalReport",
    "align_records",
    "field_metrics",
    "load_dataset",
    "run_case",
    "run_eval",
    "values_match",
]
