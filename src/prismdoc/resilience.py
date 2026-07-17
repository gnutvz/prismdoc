"""Generic retry helpers for transient failures."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 2,
    backoff_base: float = 0.5,
    jitter: float = 0.0,
    rng: Callable[[], float] = random.random,
    retry_on: Callable[[BaseException], bool] = lambda exc: True,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call ``fn`` with bounded retries and exponential backoff on failure."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if not retry_on(exc) or attempt >= max_retries:
                raise
            if on_retry is not None:
                on_retry(attempt, exc)
            delay = backoff_base * (2**attempt) * (1 + jitter * rng())
            sleep(delay)
            attempt += 1
