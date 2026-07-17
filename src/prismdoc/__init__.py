"""prismdoc — cost-aware, schema-driven document extraction pipeline."""

from prismdoc import registry
from prismdoc.models import Block, Document, Page, Record, Source
from prismdoc.pipeline import Pipeline
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.ingest import IngestStage, Loader
from prismdoc.stages.parse import ParseStage, Parser, PassthroughParser

__version__ = "0.0.0"

__all__ = [
    "Block",
    "Context",
    "Document",
    "IngestStage",
    "Loader",
    "Page",
    "ParseStage",
    "Parser",
    "PassthroughParser",
    "Pipeline",
    "Record",
    "Source",
    "Stage",
    "registry",
    "__version__",
    "hello",
]


def hello() -> str:
    return "prismdoc ready"
