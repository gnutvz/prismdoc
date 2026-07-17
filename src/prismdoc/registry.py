"""Plugin registry for factories keyed by ``<kind>.<engine>``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_RegistryFactory = Callable[..., Any]

_REGISTRY: dict[str, _RegistryFactory] = {}


def register(key: str, factory: _RegistryFactory) -> None:
    """Register a factory under ``key`` (e.g. ``\"parser.docling\"``)."""
    _REGISTRY[key] = factory


def get_factory(key: str) -> _RegistryFactory:
    """Return the registered factory for ``key``.

    Raises:
        KeyError: if ``key`` is not registered.
    """
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise KeyError(
            f"Unknown stage key {key!r}; registered: {sorted(_REGISTRY)}"
        ) from exc


def create(key: str, **kwargs: Any) -> Any:
    """Instantiate an object from a registered factory.

    Raises:
        KeyError: if ``key`` is not registered.
    """
    return get_factory(key)(**kwargs)


def get_keys() -> list[str]:
    """Return sorted registered keys."""
    return sorted(_REGISTRY)


def clear() -> None:
    """Remove all registered factories."""
    _REGISTRY.clear()
