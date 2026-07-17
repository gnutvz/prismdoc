"""Generic retry helpers for transient failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 2,
    backoff_base: float = 0.5,
    retry_on: Callable[[BaseException], bool] = lambda exc: True,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` with bounded retries and exponential backoff on failure."""
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:
            if not retry_on(exc) or attempt >= max_retries:
                raise
            sleep(backoff_base * (2**attempt))
            attempt += 1
