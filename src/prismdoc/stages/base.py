"""Stage ABC and shared pipeline Context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from prismdoc.models import Document


@dataclass
class Context:
    """Shared runtime config for a pipeline run.

    ``schema`` is a placeholder until T-004 introduces a typed target schema.
    """

    schema: dict | None = None
    options: dict = field(default_factory=dict)


class Stage(ABC):
    """A single, pluggable step in the document pipeline.

    Stages are stateless: configuration belongs on the instance (constructor).
    ``run`` takes a ``Document``, enriches it, and returns it. Side effects
    beyond ``doc`` / ``ctx`` are not allowed.
    """

    name: str

    @abstractmethod
    def run(self, doc: Document, ctx: Context) -> Document:
        """Process ``doc`` and return the enriched document."""
        ...
