"""prismdoc — cost-aware, schema-driven document extraction pipeline."""

from __future__ import annotations

from typing import Any

from prismdoc import registry
from prismdoc.config import build_pipeline, load_pipeline
from prismdoc.cost import BudgetExceededError, CostLedger, estimate_cost, record_cost
from prismdoc.errors import InputTooLargeError, UnreadableDocumentError
from prismdoc.models import Block, Document, FieldProvenance, Page, Record, Source
from prismdoc.pipeline import Pipeline
from prismdoc.resilience import with_retry
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.cascade import CascadeStage, get_scorer, register_scorer
from prismdoc.stages.confidence import ConfidenceStage
from prismdoc.stages.extract import ExtractStage, LLMClient
from prismdoc.stages.figures import (
    Figure,
    FigureExtractStage,
    FigureMergeStage,
    FigureProcessStage,
    FigureProcessor,
)
from prismdoc.stages.ingest import IngestStage, Loader
from prismdoc.stages.normalize import NormalizeStage
from prismdoc.stages.parse import ParseStage, Parser, PassthroughParser
from prismdoc.stages.provenance import ProvenanceStage
from prismdoc.stages.rules import RuleValidateStage, get_rule, register_rule
from prismdoc.stages.table_extract import TableExtractStage
from prismdoc.stages.validate import ValidateStage

__version__ = "0.3.0"

__all__ = [
    "Block",
    "BudgetExceededError",
    "CascadeStage",
    "ConfidenceStage",
    "Context",
    "CostLedger",
    "Document",
    "ExtractStage",
    "FieldProvenance",
    "FieldSpec",
    "Figure",
    "FigureExtractStage",
    "FigureMergeStage",
    "FigureProcessStage",
    "FigureProcessor",
    "IngestStage",
    "InputTooLargeError",
    "LLMClient",
    "Loader",
    "NormalizeStage",
    "Page",
    "ParseStage",
    "Parser",
    "PassthroughParser",
    "Pipeline",
    "ProvenanceStage",
    "Record",
    "RuleValidateStage",
    "Source",
    "Stage",
    "TableExtractStage",
    "TargetSchema",
    "UnreadableDocumentError",
    "ValidateStage",
    "build_pipeline",
    "cli_main",
    "estimate_cost",
    "get_rule",
    "get_scorer",
    "load_pipeline",
    "record_cost",
    "register_rule",
    "register_scorer",
    "registry",
    "with_retry",
    "__version__",
    "hello",
]


def __getattr__(name: str) -> Any:
    # Lazy: avoid importing cli when ``python -m prismdoc.cli`` runs.
    if name == "cli_main":
        from prismdoc.cli import main as cli_main

        return cli_main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def hello() -> str:
    return "prismdoc ready"
