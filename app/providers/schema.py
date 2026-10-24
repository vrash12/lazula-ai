"""Turn a Pydantic output model into a JSON schema that providers' structured-output
modes accept.

Anthropic structured outputs (checked October 2026) require
`additionalProperties: false` on every object and do not support numeric
limits, string length limits or complex array limits. We remove those here;
Pydantic still enforces them when the hub validates the answer, so nothing
is lost.

Free-form maps (`dict[str, X]`) cannot be expressed with
`additionalProperties: false`, so output models must use lists of objects
instead. `strict_json_schema` raises if one slips in.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_UNSUPPORTED = {
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "default",
}


class SchemaNotStrict(ValueError):
    pass


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    cleaned = _clean(schema, path="$")
    if not isinstance(cleaned, dict):
        raise SchemaNotStrict("Top-level schema is not an object")
    return cleaned


def _clean(node: Any, path: str) -> Any:
    if isinstance(node, list):
        return [_clean(item, f"{path}[{i}]") for i, item in enumerate(node)]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED:
            continue
        out[key] = _clean(value, f"{path}.{key}")
    is_object = out.get("type") == "object" or "properties" in out
    if is_object:
        extra = node.get("additionalProperties")
        if extra not in (None, False):
            raise SchemaNotStrict(f"Free-form map at {path}; use a list of objects instead")
        out["additionalProperties"] = False
    return out
