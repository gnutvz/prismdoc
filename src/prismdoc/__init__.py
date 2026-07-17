"""prismdoc — cost-aware, schema-driven document extraction pipeline."""

from __future__ import annotations

from typing import Any

from prismdoc import registry
from prismdoc.config import build_pipeline, load_pipeline
from prismdoc.errors import UnreadableDocumentError
from prismdoc.models import Block, Document, Page, Record, Source
from prismdoc.pipeline import Pipeline
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import ExtractStage, LLMClient
from prismdoc.stages.ingest import IngestStage, Loader
from prismdoc.stages.normalize import NormalizeStage
from prismdoc.stages.parse import ParseStage, Parser, PassthroughParser
from prismdoc.stages.table_extract import TableExtractStage
from prismdoc.stages.validate import ValidateStage

__version__ = "0.0.0"

__all__ = [
    "Block",
    "Context",
    "Document",
    "ExtractStage",
    "FieldSpec",
    "IngestStage",
    "LLMClient",
    "Loader",
    "NormalizeStage",
    "Page",
    "ParseStage",
    "Parser",
    "PassthroughParser",
    "Pipeline",
    "Record",
    "Source",
    "Stage",
    "TableExtractStage",
    "TargetSchema",
    "UnreadableDocumentError",
    "ValidateStage",
    "build_pipeline",
    "cli_main",
    "load_pipeline",
    "registry",
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
