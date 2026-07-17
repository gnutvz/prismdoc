"""Tests for T-015/T-022 resilience: with_retry and LiteLLMClient retry settings."""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from prismdoc import (
    Context,
    Document,
    ExtractStage,
    FieldSpec,
    Source,
    TargetSchema,
    with_retry,
)
from prismdoc.stages import extract as extract_mod
from prismdoc.stages.extract import (
    Completion,
    LiteLLMClient,
    LLMClient,
    _is_transient,
)


def test_with_retry_succeeds_after_transient_failures() -> None:
    calls = 0
    sleeps: list[float] = []

    def fn() -> str:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("transient")
        return "ok"

    result = with_retry(
        fn,
        max_retries=2,
        backoff_base=0.5,
        sleep=sleeps.append,
    )

    assert result == "ok"
    assert calls == 3
    assert sleeps == [0.5, 1.0]


def test_with_retry_raises_after_exhausting_retries() -> None:
    calls = 0
    sleeps: list[float] = []

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("always fails")

    with pytest.raises(RuntimeError, match="always fails"):
        with_retry(
            fn,
            max_retries=2,
            backoff_base=0.5,
            sleep=sleeps.append,
        )

    assert calls == 3
    assert sleeps == [0.5, 1.0]


def test_with_retry_no_retry_when_retry_on_false() -> None:
    calls = 0
    sleeps: list[float] = []

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        with_retry(
            fn,
            max_retries=2,
            retry_on=lambda exc: False,
            sleep=sleeps.append,
        )

    assert calls == 1
    assert sleeps == []


def test_with_retry_reraises_keyboard_interrupt_immediately() -> None:
    calls = 0
    sleeps: list[float] = []
    retries: list[tuple[int, BaseException]] = []

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        with_retry(
            fn,
            max_retries=2,
            sleep=sleeps.append,
            on_retry=lambda attempt, exc: retries.append((attempt, exc)),
        )

    assert calls == 1
    assert sleeps == []
    assert retries == []


def test_with_retry_applies_jitter_with_fixed_rng() -> None:
    calls = 0
    sleeps: list[float] = []

    def fn() -> str:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("transient")
        return "ok"

    # delay = backoff_base * (2 ** attempt) * (1 + jitter * rng())
    # attempt 0: 0.5 * 1 * (1 + 0.5 * 0.5) = 0.5 * 1.25 = 0.625
    # attempt 1: 0.5 * 2 * (1 + 0.5 * 0.5) = 1.0 * 1.25 = 1.25
    result = with_retry(
        fn,
        max_retries=2,
        backoff_base=0.5,
        jitter=0.5,
        rng=lambda: 0.5,
        sleep=sleeps.append,
    )

    assert result == "ok"
    assert sleeps == [0.625, 1.25]


def test_with_retry_invokes_on_retry_once_per_retry() -> None:
    calls = 0
    retries: list[tuple[int, BaseException]] = []

    def fn() -> str:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError(f"fail-{calls}")
        return "ok"

    result = with_retry(
        fn,
        max_retries=2,
        sleep=lambda _: None,
        on_retry=lambda attempt, exc: retries.append((attempt, exc)),
    )

    assert result == "ok"
    assert [attempt for attempt, _ in retries] == [0, 1]
    assert all(isinstance(exc, RuntimeError) for _, exc in retries)


def test_litellm_client_stores_resilience_settings() -> None:
    client = LiteLLMClient(
        model="gpt-4o-mini",
        timeout=30.0,
        max_retries=3,
        backoff_base=0.25,
        jitter=0.3,
        temperature=0.1,
    )

    assert client.model == "gpt-4o-mini"
    assert client.timeout == 30.0
    assert client.max_retries == 3
    assert client.backoff_base == 0.25
    assert client.jitter == 0.3
    assert client.opts == {"temperature": 0.1}


def test_litellm_client_default_jitter_is_on() -> None:
    assert LiteLLMClient().jitter == 0.5


def test_litellm_client_complete_passes_jitter_to_with_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Msg:
        content = '{"records": []}'

    class _Choice:
        message = _Msg()

    class _Response:
        choices = [_Choice()]
        usage = None

    def fake_with_retry(fn: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return _Response()

    monkeypatch.setitem(
        sys.modules, "litellm", types.SimpleNamespace(completion=lambda **_: None)
    )
    monkeypatch.setattr(extract_mod, "with_retry", fake_with_retry)

    LiteLLMClient(jitter=0.4).complete("hello")

    assert captured["jitter"] == 0.4
    assert captured["jitter"] != 0.0

    captured.clear()
    LiteLLMClient().complete("hello")
    assert captured["jitter"] == 0.5


def test_is_transient_fallback_whitelists_connection_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extract_mod, "_transient_exception_types", lambda: None)
    assert _is_transient(ConnectionError("down")) is True
    assert _is_transient(TimeoutError("slow")) is True
    assert _is_transient(RuntimeError("network")) is False
    assert _is_transient(ValueError("bad input")) is False


def test_is_transient_litellm_path_uses_exception_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extract_mod, "_transient_exception_types", lambda: (ConnectionError,)
    )
    assert _is_transient(ConnectionError("down")) is True
    assert _is_transient(RuntimeError("network")) is False


def test_litellm_client_completion_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _Msg:
        content = '{"records": []}'

    class _Choice:
        message = _Msg()

    class _Response:
        choices = [_Choice()]
        usage = None

    def fake_completion(**_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ConnectionError("transient")
        return _Response()

    monkeypatch.setitem(
        sys.modules, "litellm", types.SimpleNamespace(completion=fake_completion)
    )
    monkeypatch.setattr(
        extract_mod, "_transient_exception_types", lambda: (ConnectionError,)
    )
    real_with_retry = extract_mod.with_retry

    def with_retry_fast(fn: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("sleep", lambda _: None)
        return real_with_retry(fn, **kwargs)

    monkeypatch.setattr(extract_mod, "with_retry", with_retry_fast)

    completion = LiteLLMClient(max_retries=2).complete("hello")
    assert completion.attempts == 2
    assert calls == 3


def test_extract_stage_records_llm_attempts() -> None:
    class AttemptClient(LLMClient):
        def complete(
            self, prompt: str, *, response_format: dict | None = None
        ) -> Completion:
            return Completion(
                text=json.dumps(
                    [{"name": "Widget A", "sku": "W-001", "price": 9.99}]
                ),
                attempts=3,
            )

    schema = TargetSchema(
        fields=[
            FieldSpec(name="name", type="string", required=True),
            FieldSpec(name="sku", type="string", required=True),
            FieldSpec(name="price", type="number", required=True),
        ]
    )
    doc = Document(
        source=Source(path="/tmp/catalog.md"),
        artifacts={"parsed_markdown": "catalog text"},
    )
    result = ExtractStage(schema=schema, client=AttemptClient()).run(doc, Context())

    assert result.artifacts["llm"]["attempts"] == 3
