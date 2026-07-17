"""Plugin registry for Stage factories keyed by ``<kind>.<engine>``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prismdoc.stages.base import Stage

_RegistryFactory = Callable[..., Stage]

_REGISTRY: dict[str, _RegistryFactory] = {}


def register(key: str, factory: _RegistryFactory) -> None:
    """Register a Stage factory under ``key`` (e.g. ``\"parser.docling\"``)."""
    _REGISTRY[key] = factory


def create(key: str, **kwargs: Any) -> Stage:
    """Instantiate a Stage from a registered factory.

    Raises:
        KeyError: if ``key`` is not registered.
    """
    try:
        factory = _REGISTRY[key]
    except KeyError as exc:
        raise KeyError(
            f"Unknown stage key {key!r}; registered: {sorted(_REGISTRY)}"
        ) from exc
    return factory(**kwargs)


def get_keys() -> list[str]:
    """Return sorted registered keys."""
    return sorted(_REGISTRY)
