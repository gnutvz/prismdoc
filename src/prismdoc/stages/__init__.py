"""Pipeline stages package."""

from prismdoc.stages.base import Context, Stage
from prismdoc.stages.cascade import (
    CascadeStage,
    get_scorer,
    register_scorer,
    text_length,
)
from prismdoc.stages.confidence import ConfidenceStage
from prismdoc.stages.extract import ExtractStage, LLMClient, LiteLLMClient
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

__all__ = [
    "CascadeStage",
    "ConfidenceStage",
    "Context",
    "ExtractStage",
    "Figure",
    "FigureExtractStage",
    "FigureMergeStage",
    "FigureProcessStage",
    "FigureProcessor",
    "IngestStage",
    "LLMClient",
    "LiteLLMClient",
    "Loader",
    "NormalizeStage",
    "ParseStage",
    "Parser",
    "PassthroughParser",
    "ProvenanceStage",
    "RuleValidateStage",
    "Stage",
    "TableExtractStage",
    "ValidateStage",
    "get_rule",
    "get_scorer",
    "register_rule",
    "register_scorer",
    "text_length",
]
