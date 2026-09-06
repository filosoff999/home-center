from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Any


class ValidationError(ValueError):
    pass


def validate(schema: dict[str, Any], value: Any, *, root: dict[str, Any] | None = None, path: str = "$") -> None:
    root = root or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValidationError(f"{path}: unsupported external ref")
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        validate(target, value, root=root, path=path)
        return
    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{path}: enum mismatch")
    if "type" in schema and not _matches_type(schema["type"], value):
        raise ValidationError(f"{path}: type mismatch")
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                raise ValidationError(f"{path}: missing {required}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in properties:
                validate(properties[key], child, root=root, path=f"{path}.{key}")
            elif additional is False:
                raise ValidationError(f"{path}: unexpected {key}")
            elif isinstance(additional, dict):
                validate(additional, child, root=root, path=f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValidationError(f"{path}: too few items")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            raise ValidationError(f"{path}: duplicate items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate(schema["items"], item, root=root, path=f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10**9):
            raise ValidationError(f"{path}: string length")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValidationError(f"{path}: pattern mismatch")
        _format(schema.get("format"), value, path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", value):
            raise ValidationError(f"{path}: below minimum")


def _matches_type(expected: str | list[str], value: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(item, value) for item in expected)
    mapping = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }
    return mapping[expected]()


def _format(name: str | None, value: str, path: str) -> None:
    try:
        if name == "ipv4":
            if ipaddress.ip_address(value).version != 4:
                raise ValueError
        elif name == "uuid":
            uuid.UUID(value)
        elif name == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{path}: invalid {name}") from exc
