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


class FieldRecall(BaseModel):
    """Per-field aggregated exact and token-overlap recall."""

    exact_recall: float
    token_recall: float


class BenchReport(BaseModel):
    """Aggregated OCR-recall metrics over a sample set."""

    n_samples: int
    per_field: dict[str, FieldRecall] = Field(default_factory=dict)
    overall_exact: float = 0.0
    overall_token: float = 0.0


def run_ocr_recall(samples: list[BenchSample], parser: Parser) -> BenchReport:
    """Ingest + parse each sample; measure whether OCR text contains GT fields.

    ``parser`` is injectable: use ``DoclingParser`` for a live run, or a fake
    parser returning canned text in unit tests.
    """
    pipeline = Pipeline([IngestStage(), ParseStage(parser=parser)])
    ctx = Context()

    field_exact_hits: dict[str, int] = {}
    field_token_sums: dict[str, float] = {}
    field_totals: dict[str, int] = {}
    sample_mean_exacts: list[float] = []
    sample_mean_tokens: list[float] = []

    for sample in samples:
        doc = Document(source=Source(path=sample.image_path))
        doc = pipeline.run(doc, ctx)
        ocr_text = str(doc.artifacts.get("parsed_markdown") or "")
        result = sample_recall(ocr_text, sample.fields)
        per_field = result["per_field"]
        sample_mean_exacts.append(float(result["mean_exact"]))
        sample_mean_tokens.append(float(result["mean_token"]))
        for name, metrics in per_field.items():
            field_totals[name] = field_totals.get(name, 0) + 1
            if metrics["exact"]:
                field_exact_hits[name] = field_exact_hits.get(name, 0) + 1
            field_token_sums[name] = (
                field_token_sums.get(name, 0.0) + float(metrics["token"])
            )

    n_samples = len(samples)
    per_field_report = {
        name: FieldRecall(
            exact_recall=(
                (field_exact_hits.get(name, 0) / total) if total else 0.0
            ),
            token_recall=(
                (field_token_sums.get(name, 0.0) / total) if total else 0.0
            ),
        )
        for name, total in field_totals.items()
    }
    overall_exact = (
        sum(sample_mean_exacts) / n_samples if n_samples else 0.0
    )
    overall_token = (
        sum(sample_mean_tokens) / n_samples if n_samples else 0.0
    )
    return BenchReport(
        n_samples=n_samples,
        per_field=per_field_report,
        overall_exact=overall_exact,
        overall_token=overall_token,
    )
