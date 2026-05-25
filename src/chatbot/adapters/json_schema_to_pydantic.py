"""Build a Pydantic model dynamically from a JSON schema object.

The chatbot platform's skills declare their tool args as OpenAI-format JSON
schemas (see `Skill.prepare_tools`). LangChain's `StructuredTool` wants a
Pydantic `BaseModel` instead. This helper covers the subset we actually use:
object with scalar (string/integer/number/boolean) and array-of-scalar
properties, optional `enum`, optional `required`, optional `description`.

Not a general JSON-schema implementation — by design. If a skill ever needs
nested objects or oneOf, extend here.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, create_model

_SCALAR = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _resolve_type(prop_schema: dict) -> type:
    t = prop_schema.get("type", "string")
    if t in _SCALAR:
        return _SCALAR[t]
    if t == "array":
        items = prop_schema.get("items", {})
        return list[_resolve_type(items)]
    # Fallback: untyped (any).
    return str


def build_args_model(name: str, parameters: dict[str, Any]) -> type[BaseModel]:
    """Return a dynamically-created Pydantic model class for these tool args.

    `name` becomes the model class name (must be unique across the process for
    a given args shape — caller's responsibility to namespace by tool name).
    `parameters` is the JSON schema's `parameters` object (`type: object,
    properties: {...}, required: [...]`).
    """
    properties = parameters.get("properties", {}) or {}
    required = set(parameters.get("required", []) or [])

    field_defs: dict[str, tuple[type, Any]] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _resolve_type(prop_schema)
        description = prop_schema.get("description")
        if prop_name in required:
            field_defs[prop_name] = (py_type, Field(..., description=description))
        else:
            # Optional → default None, type widened to `py_type | None`.
            field_defs[prop_name] = (py_type | None, Field(default=None, description=description))

    if not field_defs:
        # StructuredTool needs at least an empty model so it can validate `{}`.
        return create_model(name)
    return create_model(name, **field_defs)
