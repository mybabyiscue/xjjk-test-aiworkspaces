"""Security helpers shared by TAPD preparation scripts."""

from __future__ import annotations

import json
from typing import TypeAlias

JsonObject: TypeAlias = dict[str, object]

SENSITIVE_FIELD_PARTS: frozenset[str] = frozenset(
    {"authorization", "cookie", "token", "password", "secret", "credential", "api-key", "apikey", "account"}
)


def is_sensitive_key(key: str) -> bool:
    return any(part in key.lower() for part in SENSITIVE_FIELD_PARTS)


def redact_value(key: str, value: object) -> object:
    if is_sensitive_key(key):
        return "***"
    if isinstance(value, dict):
        return redact_object(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def redact_object(value: JsonObject) -> JsonObject:
    return {key: redact_value(key, item) for key, item in value.items()}


def redacted_json(value: object) -> str:
    payload: object = redact_object(value) if isinstance(value, dict) else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
