"""prismdoc — cost-aware, schema-driven document extraction pipeline."""

from prismdoc import registry
from prismdoc.models import Block, Document, Page, Record, Source
from prismdoc.pipeline import Pipeline
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import ExtractStage, LLMClient
from prismdoc.stages.ingest import IngestStage, Loader
from prismdoc.stages.normalize import NormalizeStage
from prismdoc.stages.parse import ParseStage, Parser, PassthroughParser
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
    "TargetSchema",
    "ValidateStage",
    "registry",
    "__version__",
    "hello",
]


def hello() -> str:
    return "prismdoc ready"
