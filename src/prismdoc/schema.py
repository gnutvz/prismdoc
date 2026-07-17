"""Target schema definitions for schema-driven extraction."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

_JSON_TYPES: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


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

    def json_schema(self) -> dict[str, Any]:
        """Build a JSON Schema for ``{"records": [record, ...]}``."""
        properties: dict[str, dict[str, str]] = {}
        required: list[str] = []
        for field in self.fields:
            properties[field.name] = {"type": _JSON_TYPES[field.type]}
            if field.required:
                required.append(field.name)
        return {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }
            },
            "required": ["records"],
        }
