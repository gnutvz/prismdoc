"""Target schema definitions for schema-driven extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FieldSpec(BaseModel):
    """One field in a target extraction schema."""

    name: str
    type: Literal["string", "number", "integer", "boolean"] = "string"
    description: str = ""
    required: bool = False


class TargetSchema(BaseModel):
    """Collection of field specs describing records to extract."""

    fields: list[FieldSpec] = Field(default_factory=list)

    def field_names(self) -> list[str]:
        """Return the ordered list of field names."""
        return [field.name for field in self.fields]

    def describe(self) -> str:
        """Return a human/LLM-readable description of the fields."""
        lines: list[str] = []
        for field in self.fields:
            req = "required" if field.required else "optional"
            desc = field.description.strip() or "(no description)"
            lines.append(f"- {field.name} ({field.type}, {req}): {desc}")
        return "\n".join(lines)
