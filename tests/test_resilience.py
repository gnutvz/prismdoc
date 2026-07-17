"""Tests for T-015 resilience: with_retry and LiteLLMClient retry settings."""

from __future__ import annotations

import pytest

from prismdoc import with_retry
from prismdoc.stages import extract as extract_mod
from prismdoc.stages.extract import LiteLLMClient, _is_transient


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


def test_litellm_client_stores_resilience_settings() -> None:
    client = LiteLLMClient(
        model="gpt-4o-mini",
        timeout=30.0,
        max_retries=3,
        backoff_base=0.25,
        temperature=0.1,
    )

    assert client.model == "gpt-4o-mini"
    assert client.timeout == 30.0
    assert client.max_retries == 3
    assert client.backoff_base == 0.25
    assert client.opts == {"temperature": 0.1}


def test_is_transient_fallback_skips_value_and_type_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extract_mod, "_transient_exception_types", lambda: None)
    assert _is_transient(RuntimeError("network")) is True
    assert _is_transient(ValueError("bad input")) is False
    assert _is_transient(TypeError("bad type")) is False


def test_is_transient_litellm_path_uses_exception_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extract_mod, "_transient_exception_types", lambda: (ConnectionError,)
    )
    assert _is_transient(ConnectionError("down")) is True
    assert _is_transient(RuntimeError("network")) is False
