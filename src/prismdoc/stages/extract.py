"""Extract stage: schema-driven extraction via an injectable LLM client."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from prismdoc.cost import (
    BudgetExceededError,
    CostLedger,
    check_budget,
    estimate_cost,
    record_cost,
    record_unmetered,
)
from prismdoc.errors import InputTooLargeError
from prismdoc.models import Document, Record
from prismdoc.registry import register
from prismdoc.resilience import with_retry
from prismdoc.schema import TargetSchema
from prismdoc.stages.base import Context, Stage
from prismdoc.tokens import count_tokens

_LLM_EXTRA_HINT = "Install the 'llm' extra: pip install prismdoc[llm]"

_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


class Completion(BaseModel):
    """Stateless LLM completion result (text + optional usage metadata)."""

    text: str
    usage: dict[str, int] | None = None
    model: str | None = None
    attempts: int = 0


class LLMClient(ABC):
    """Minimal interface for prompt completion (injectable for offline tests)."""

    @abstractmethod
    def complete(
        self, prompt: str, *, response_format: dict[str, Any] | None = None
    ) -> Completion:
        """Return the model response for ``prompt``.

        ``response_format`` is optional structured-output hint (ignored by
        clients that do not support it).
        """
        ...


def _transient_exception_types() -> tuple[type[BaseException], ...] | None:
    try:
        from litellm.exceptions import (
            APIConnectionError,
            InternalServerError,
            RateLimitError,
            ServiceUnavailableError,
            Timeout,
        )
    except ImportError:
        return None
    return (
        Timeout,
        RateLimitError,
        APIConnectionError,
        InternalServerError,
        ServiceUnavailableError,
    )


def _is_transient(exc: BaseException) -> bool:
    types = _transient_exception_types()
    if types is None:
        return isinstance(exc, (ConnectionError, TimeoutError))
    return isinstance(exc, types)


class LiteLLMClient(LLMClient):
    """Optional litellm-backed client (requires ``pip install prismdoc[llm]``)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        jitter: float = 0.5,
        **opts: Any,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.jitter = jitter
        self.opts = opts

    def complete(
        self, prompt: str, *, response_format: dict[str, Any] | None = None
    ) -> Completion:
        try:
            import litellm
        except ImportError as exc:
            raise ImportError(_LLM_EXTRA_HINT) from exc

        def _call() -> Any:
            kwargs: dict[str, Any] = dict(self.opts)
            if response_format is not None:
                kwargs["response_format"] = response_format
            return litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=self.timeout,
                **kwargs,
            )

        attempts = 0

        def _on_retry(attempt: int, _exc: BaseException) -> None:
            nonlocal attempts
            attempts = attempt + 1

        response = with_retry(
            _call,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            jitter=self.jitter,
            retry_on=_is_transient,
            on_retry=_on_retry,
        )
        usage_obj = getattr(response, "usage", None)
        usage: dict[str, int] | None = None
        if usage_obj is not None:
            usage = {
                "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                "completion_tokens": int(
                    getattr(usage_obj, "completion_tokens", 0) or 0
                ),
            }
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("litellm returned empty message content")
        return Completion(
            text=str(content), usage=usage, model=self.model, attempts=attempts
        )


class ExtractStage(Stage):
    """Call an LLM to extract records matching a ``TargetSchema``."""

    name = "extract"

    def __init__(
        self,
        schema: TargetSchema,
        client: LLMClient | None = None,
        model: str = "gpt-4o-mini",
        max_input_tokens: int | None = None,
        expected_output_tokens: int = 512,
        **opts: Any,
    ) -> None:
        self.schema = schema
        self.model = model
        self.max_input_tokens = max_input_tokens
        self.expected_output_tokens = expected_output_tokens
        self.client = (
            client if client is not None else LiteLLMClient(model=model, **opts)
        )

    def run(self, doc: Document, ctx: Context) -> Document:
        text = doc.artifacts.get("parsed_markdown") or doc.full_text
        prompt = _build_prompt(str(text), self.schema)
        tokens_in = count_tokens(prompt, self.model)
        if (
            self.max_input_tokens is not None
            and tokens_in > self.max_input_tokens
        ):
            raise InputTooLargeError(
                f"Prompt is {tokens_in} tokens, which exceeds max_input_tokens="
                f"{self.max_input_tokens}"
            )
        budget = ctx.options.get("budget_usd")
        if budget is not None:
            projected = _projected_cost(
                doc, self.model, tokens_in, self.expected_output_tokens
            )
            budget_usd = float(budget)
            if projected > budget_usd:
                raise BudgetExceededError(
                    f"Projected cost ${projected:.6f} exceeds budget "
                    f"${budget_usd:.6f}"
                )
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": "records",
                "schema": self.schema.json_schema(),
            },
        }
        completion = self.client.complete(prompt, response_format=response_format)
        doc.artifacts.setdefault("llm", {})["attempts"] = completion.attempts
        model_name = str(
            completion.model
            or getattr(self.client, "model", None)
            or self.model
        )
        usage = completion.usage
        if usage is not None:
            record_cost(
                doc,
                self.name,
                model_name,
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
            if budget is not None:
                check_budget(doc, float(budget))
        else:
            record_unmetered(doc, self.name, model_name)
        parsed = _parse_structured_records(completion.text)
        if parsed is None:
            parsed = _parse_records_json(completion.text)
        doc.records = [Record(fields=obj) for obj in parsed]
        return doc


def _projected_cost(
    doc: Document,
    model: str,
    tokens_in: int,
    expected_output_tokens: int,
) -> float:
    """Estimate total USD if the upcoming call is charged on top of the ledger."""
    ledger = doc.artifacts.get("cost")
    current = ledger.total_usd if isinstance(ledger, CostLedger) else 0.0
    upcoming = estimate_cost(model, tokens_in, expected_output_tokens)
    if upcoming is None:
        return current
    return current + upcoming


def _build_prompt(text: str, schema: TargetSchema) -> str:
    fields_desc = schema.describe() or "(no fields defined)"
    names = ", ".join(schema.field_names()) or "(none)"
    return (
        "Extract ALL records from the document below and return them as JSON.\n"
        "Respond with a JSON object whose \"records\" value is an array.\n"
        "Each element must be an object with exactly these fields: "
        f"{names}.\n"
        "Field specifications:\n"
        f"{fields_desc}\n\n"
        "Return ONLY JSON (no commentary). Example shape: "
        '{"records": [{"field": "value"}, ...]}.\n\n'
        "Document:\n"
        f"{text}"
    )


def _parse_structured_records(raw: str) -> list[dict[str, Any]] | None:
    """Parse ``{"records": [...]}``; return None if shape does not match."""
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    records = data.get("records")
    if not isinstance(records, list):
        return None
    out: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            raise ValueError(
                "Extract stage expected records to be objects; "
                f"got element of type {type(item).__name__}"
            )
        out.append(item)
    return out


def _parse_records_json(raw: str) -> list[dict[str, Any]]:
    """Parse a JSON array or single object from LLM output; strip fences if present.

    A top-level object ``{field: value, ...}`` is treated as one record.
    """
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
        if isinstance(data, dict):
            return [data]
        last_error = ValueError(
            f"Extract stage expected a JSON array or object; "
            f"got {type(data).__name__}"
        )

    detail = f" ({last_error})" if last_error else ""
    raise ValueError(
        "Extract stage could not parse a JSON array or object from LLM output"
        f"{detail}. Raw response (truncated): {raw[:500]!r}"
    )


def _json_candidates(raw: str) -> list[str]:
    """Yield likely JSON substrings: fenced blocks, then array/object, then raw."""
    text = raw.strip()
    out: list[str] = []
    for match in _FENCE_RE.finditer(text):
        out.append(match.group(1).strip())
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        out.append(text[start : end + 1])
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
        out.append(text[obj_start : obj_end + 1])
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
