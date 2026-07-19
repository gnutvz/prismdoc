"""Repair stage: re-prompt the LLM for only failed (missing / low-confidence) fields."""

from __future__ import annotations

import json
from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import FieldSpec, TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.stages.extract import LLMClient, LiteLLMClient, _parse_records_json
from prismdoc.stages.validate import _is_missing_or_empty


class RepairStage(Stage):
    """Re-extract only failed fields and merge corrections into existing records."""

    name = "repair"

    def __init__(
        self,
        schema: TargetSchema,
        client: LLMClient | None = None,
        max_rounds: int = 1,
    ) -> None:
        self.schema = schema
        self.client = client if client is not None else LiteLLMClient()
        self.max_rounds = max_rounds

    def _failed_fields(
        self,
        record: Record,
        doc: Document,
        already_repaired: set[str],
    ) -> list[str]:
        """Return schema field names that are missing/empty or low-confidence.

        The ``low_confidence`` artifact is a snapshot from the pre-repair
        ConfidenceStage; it is NOT recomputed here. So a field already repaired
        in an earlier round must not be re-selected via that stale signal —
        ``already_repaired`` excludes it. The missing/empty check stays live each
        round (if a repair failed to fill a required field, it is retried).
        """
        schema_names = set(self.schema.field_names())
        failed: list[str] = []
        seen: set[str] = set()

        for name in self.schema.field_names():
            if _is_missing_or_empty(record.fields.get(name)):
                failed.append(name)
                seen.add(name)

        index = next(
            (i for i, r in enumerate(doc.records) if r is record),
            None,
        )
        low = doc.artifacts.get("low_confidence")
        if index is not None and isinstance(low, list):
            for entry in low:
                if not isinstance(entry, dict):
                    continue
                if entry.get("record") != index:
                    continue
                field = entry.get("field")
                if (
                    isinstance(field, str)
                    and field in schema_names
                    and field not in seen
                    and field not in already_repaired
                ):
                    failed.append(field)
                    seen.add(field)

        return failed

    def run(self, doc: Document, ctx: Context) -> Document:
        text = doc.artifacts.get("parsed_markdown") or doc.full_text
        if not isinstance(text, str):
            text = doc.full_text
        repair_log: list[dict[str, Any]] = []

        for record_index, record in enumerate(doc.records):
            already_repaired: set[str] = set()
            for round_num in range(1, self.max_rounds + 1):
                failed = self._failed_fields(record, doc, already_repaired)
                if not failed:
                    break
                specs = [s for s in self.schema.fields if s.name in failed]
                prompt = _build_repair_prompt(str(text), record, specs)
                completion = self.client.complete(prompt)
                parsed = _parse_records_json(completion.text)
                corrections = parsed[0] if parsed else {}
                repaired_names: list[str] = []
                for name in failed:
                    if name in corrections:
                        record.fields[name] = corrections[name]
                        repaired_names.append(name)
                        already_repaired.add(name)
                repair_log.append(
                    {
                        "record": record_index,
                        "round": round_num,
                        "fields": repaired_names,
                    }
                )

        doc.artifacts["repair"] = repair_log
        return doc


def _build_repair_prompt(
    text: str, record: Record, specs: list[FieldSpec]
) -> str:
    names = ", ".join(spec.name for spec in specs) or "(none)"
    fields_desc = "\n".join(_describe_field(spec) for spec in specs) or (
        "(no fields)"
    )
    current = json.dumps(record.fields, ensure_ascii=False, default=str)
    return (
        "Some fields in an extracted record are missing or unreliable.\n"
        "Using the document text and the current record, return JSON with "
        "ONLY these fields: "
        f"{names}.\n"
        "Field specifications:\n"
        f"{fields_desc}\n\n"
        "Current record:\n"
        f"{current}\n\n"
        "Return ONLY a JSON object with those field names as keys "
        "(no commentary, no other fields).\n\n"
        "Document:\n"
        f"{text}"
    )


def _describe_field(spec: FieldSpec) -> str:
    req = "required" if spec.required else "optional"
    desc = spec.description.strip() or "(no description)"
    return f"- {spec.name} ({spec.type}, {req}): {desc}"


def register_plugins() -> None:
    """Register the default repair stage in the plugin registry."""
    register("repair.default", RepairStage)


register_plugins()
