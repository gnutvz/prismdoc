"""Ensemble extract: multi-model consensus with disagreement flags.

Single-record (header) consensus only; multi-record alignment is future work.
"""

from __future__ import annotations

from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import ExtractStage, LLMClient


class EnsembleExtractStage(Stage):
    """Run schema-driven extraction with multiple LLM clients and take consensus.

    Assumes one primary (header) record per document: each client's first record
    is aligned field-by-field. Consensus is majority vote under eval-style
    ``values_match`` normalization; fields where clients disagree are flagged in
    ``doc.artifacts["disagreement"]``.
    """

    name = "extract"

    def __init__(self, schema: TargetSchema, clients: list[LLMClient]) -> None:
        if len(clients) < 2:
            raise ValueError(
                f"EnsembleExtractStage requires at least 2 clients, got {len(clients)}"
            )
        self.schema = schema
        self.clients = clients
        self._extractors = [
            ExtractStage(schema=schema, client=client) for client in clients
        ]

    def run(self, doc: Document, ctx: Context) -> Document:
        text = doc.artifacts.get("parsed_markdown") or doc.full_text
        first_records: list[Record] = []
        for extractor in self._extractors:
            temp = Document(
                source=doc.source,
                artifacts={"parsed_markdown": str(text)},
            )
            extracted = extractor.run(temp, ctx)
            if extracted.records:
                first_records.append(extracted.records[0])
            else:
                first_records.append(Record(fields={}))

        field_types = {field.name: field.type for field in self.schema.fields}
        consensus_fields: dict[str, Any] = {}
        confidence: dict[str, float] = {}
        disagreements: list[dict[str, Any]] = []

        for name in self.schema.field_names():
            values = [record.fields.get(name) for record in first_records]
            surface, agreement = _majority_consensus(
                values, field_types.get(name, "string")
            )
            consensus_fields[name] = surface
            confidence[name] = agreement
            if agreement < 1.0:
                disagreements.append(
                    {
                        "field": name,
                        "values": values,
                        "agreement": agreement,
                    }
                )

        doc.records = [Record(fields=consensus_fields, confidence=confidence)]
        doc.artifacts["disagreement"] = disagreements
        return doc


def _majority_consensus(
    values: list[Any], field_type: str
) -> tuple[Any, float]:
    """Pick majority surface form and agreement ratio under ``values_match``.

    Groups values that match under type-aware normalization; the largest group
    wins (ties keep the earliest group). Agreement is group_size / n_clients.
    """
    n = len(values)
    if n == 0:
        return None, 0.0

    groups: list[list[Any]] = []
    for value in values:
        placed = False
        for group in groups:
            if _values_equivalent(value, group[0], field_type):
                group.append(value)
                placed = True
                break
        if not placed:
            groups.append([value])

    best = max(groups, key=len)
    agreement = len(best) / n
    return best[0], agreement


def _values_equivalent(left: Any, right: Any, field_type: str) -> bool:
    """True when both missing, or both present and ``values_match`` agrees.

    ``values_match`` is imported lazily to avoid a config ↔ eval circular import
    at package load time.
    """
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    from prismdoc.eval.metrics import values_match

    return values_match(left, right, field_type)


def register_plugins() -> None:
    """Register the ensemble extract stage in the plugin registry."""
    register("extract.ensemble", EnsembleExtractStage)


register_plugins()
