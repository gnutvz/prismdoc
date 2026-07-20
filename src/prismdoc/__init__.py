"""prismdoc — cost-aware, schema-driven document extraction pipeline."""

from __future__ import annotations

from typing import Any

from prismdoc import registry
from prismdoc.config import build_pipeline, load_pipeline
from prismdoc.cost import BudgetExceededError, CostLedger, estimate_cost, record_cost
from prismdoc.errors import InputTooLargeError, UnreadableDocumentError
from prismdoc.models import Block, Document, FieldProvenance, Page, Record, Source
from prismdoc.observability import aggregate_metrics, document_metrics
from prismdoc.pipeline import Pipeline
from prismdoc.resilience import with_retry
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.cascade import (
    CascadeStage,
    char_validity,
    get_scorer,
    make_composite,
    register_scorer,
)
from prismdoc.stages.chunked_extract import ChunkedExtractStage, chunk_text
from prismdoc.stages.confidence import ConfidenceStage
from prismdoc.stages.ensemble import EnsembleExtractStage
from prismdoc.stages.extract import ExtractStage, LLMClient
from prismdoc.stages.hybrid_extract import HybridExtractStage
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
from prismdoc.stages.repair import RepairStage
from prismdoc.stages.rules import RuleValidateStage, get_rule, register_rule
from prismdoc.stages.table_extract import TableExtractStage
from prismdoc.stages.validate import ValidateStage
from prismdoc.stages.verify import LabelVerifyStage, TableColumnVerifyStage

__version__ = "0.4.0"

__all__ = [
    "Block",
    "BudgetExceededError",
    "CascadeStage",
    "ChunkedExtractStage",
    "ConfidenceStage",
    "Context",
    "CostLedger",
    "Document",
    "EnsembleExtractStage",
    "ExtractStage",
    "FieldProvenance",
    "FieldSpec",
    "Figure",
    "FigureExtractStage",
    "FigureMergeStage",
    "FigureProcessStage",
    "FigureProcessor",
    "HybridExtractStage",
    "IngestStage",
    "LabelVerifyStage",
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
    "RepairStage",
    "RuleValidateStage",
    "Source",
    "Stage",
    "TableColumnVerifyStage",
    "TableExtractStage",
    "TargetSchema",
    "UnreadableDocumentError",
    "ValidateStage",
    "aggregate_metrics",
    "build_pipeline",
    "char_validity",
    "chunk_text",
    "cli_main",
    "document_metrics",
    "estimate_cost",
    "get_rule",
    "get_scorer",
    "load_pipeline",
    "make_composite",
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
