"""Run OCR-recall over bench samples with an injectable parser."""

from __future__ import annotations

from pydantic import BaseModel, Field

from prismdoc.bench.dataset import BenchSample
from prismdoc.bench.ocr_recall import sample_recall
from prismdoc.models import Document, Source
from prismdoc.pipeline import Pipeline
from prismdoc.stages.base import Context
from prismdoc.stages.ingest import IngestStage
from prismdoc.stages.parse import ParseStage, Parser


class BenchReport(BaseModel):
    """Aggregated OCR-recall metrics over a sample set."""

    n_samples: int
    per_field: dict[str, float] = Field(default_factory=dict)
    overall_recall: float = 0.0


def run_ocr_recall(samples: list[BenchSample], parser: Parser) -> BenchReport:
    """Ingest + parse each sample; measure whether OCR text contains GT fields.

    ``parser`` is injectable: use ``DoclingParser`` for a live run, or a fake
    parser returning canned text in unit tests.
    """
    pipeline = Pipeline([IngestStage(), ParseStage(parser=parser)])
    ctx = Context()

    field_hits: dict[str, int] = {}
    field_totals: dict[str, int] = {}
    sample_fractions: list[float] = []

    for sample in samples:
        doc = Document(source=Source(path=sample.image_path))
        doc = pipeline.run(doc, ctx)
        ocr_text = str(doc.artifacts.get("parsed_markdown") or "")
        result = sample_recall(ocr_text, sample.fields)
        found = result["found"]
        sample_fractions.append(float(result["fraction"]))
        for name, ok in found.items():
            field_totals[name] = field_totals.get(name, 0) + 1
            if ok:
                field_hits[name] = field_hits.get(name, 0) + 1

    n_samples = len(samples)
    per_field = {
        name: (field_hits.get(name, 0) / total) if total else 0.0
        for name, total in field_totals.items()
    }
    overall_recall = (
        sum(sample_fractions) / n_samples if n_samples else 0.0
    )
    return BenchReport(
        n_samples=n_samples,
        per_field=per_field,
        overall_recall=overall_recall,
    )
