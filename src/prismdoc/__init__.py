"""prismdoc — cost-aware, schema-driven document extraction pipeline."""

from prismdoc import registry
from prismdoc.models import Block, Document, Page, Record, Source
from prismdoc.pipeline import Pipeline
from prismdoc.stages.base import Context, Stage

__version__ = "0.0.0"

__all__ = [
    "Block",
    "Context",
    "Document",
    "Page",
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
