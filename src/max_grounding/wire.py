"""Deterministic JSON-compatible serialization for public service boundaries."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def to_wire(value: Any):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_wire(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [to_wire(item) for item in value]
    if isinstance(value, list):
        return [to_wire(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_wire(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported public wire value: {type(value).__name__}")
