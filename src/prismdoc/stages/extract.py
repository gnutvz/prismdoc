"""Extract stage: schema-driven extraction via an injectable LLM client."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage

_LLM_EXTRA_HINT = "Install the 'llm' extra: pip install prismdoc[llm]"

_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


class LLMClient(ABC):
    """Minimal interface for prompt completion (injectable for offline tests)."""

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Return the model response text for ``prompt``."""
        ...


class LiteLLMClient(LLMClient):
    """Optional litellm-backed client (requires ``pip install prismdoc[llm]``)."""

    def __init__(self, model: str = "gpt-4o-mini", **opts: Any) -> None:
        self.model = model
        self.opts = opts

    def complete(self, prompt: str) -> str:
        try:
            import litellm
        except ImportError as exc:
            raise ImportError(_LLM_EXTRA_HINT) from exc

        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **self.opts,
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("litellm returned empty message content")
        return str(content)


class ExtractStage(Stage):
    """Call an LLM to extract records matching a ``TargetSchema``."""

    name = "extract"

    def __init__(
        self,
        schema: TargetSchema,
        client: LLMClient | None = None,
        model: str = "gpt-4o-mini",
        **opts: Any,
    ) -> None:
        self.schema = schema
        self.client = (
            client if client is not None else LiteLLMClient(model=model, **opts)
        )

    def run(self, doc: Document, ctx: Context) -> Document:
        text = doc.artifacts.get("parsed_markdown") or doc.full_text
        prompt = _build_prompt(str(text), self.schema)
        raw = self.client.complete(prompt)
        parsed = _parse_records_json(raw)
        doc.records = [Record(fields=obj) for obj in parsed]
        return doc


def _build_prompt(text: str, schema: TargetSchema) -> str:
    fields_desc = schema.describe() or "(no fields defined)"
    names = ", ".join(schema.field_names()) or "(none)"
    return (
        "Extract ALL records from the document below as a JSON array.\n"
        "Each element must be an object with exactly these fields: "
        f"{names}.\n"
        "Field specifications:\n"
        f"{fields_desc}\n\n"
        "Return ONLY a JSON array (no commentary). Example shape: "
        '[{"field": "value"}, ...].\n\n'
        "Document:\n"
        f"{text}"
    )


def _parse_records_json(raw: str) -> list[dict[str, Any]]:
    """Parse a JSON array from LLM output; strip fences if present."""
    candidates = _json_candidates(raw)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, list):
            records: list[dict[str, Any]] = []
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError(
                        "Extract stage expected a JSON array of objects; "
                        f"got element of type {type(item).__name__}"
                    )
                records.append(item)
            return records
        last_error = ValueError(
            f"Extract stage expected a JSON array; got {type(data).__name__}"
        )

    detail = f" ({last_error})" if last_error else ""
    raise ValueError(
        "Extract stage could not parse a JSON array from LLM output"
        f"{detail}. Raw response (truncated): {raw[:500]!r}"
    )


def _json_candidates(raw: str) -> list[str]:
    """Yield likely JSON substrings: fenced blocks, then first array, then raw."""
    text = raw.strip()
    out: list[str] = []
    for match in _FENCE_RE.finditer(text):
        out.append(match.group(1).strip())
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        out.append(text[start : end + 1])
    out.append(text)
    # Preserve order, drop duplicates
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def register_plugins() -> None:
    """Register default extractor and extract stage in the plugin registry."""
    register("extractor.litellm", LiteLLMClient)
    register("extract.default", ExtractStage)


register_plugins()
