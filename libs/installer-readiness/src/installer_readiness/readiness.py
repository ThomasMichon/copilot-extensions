"""Strict parser for readiness probe output."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .discovery import READINESS_SCHEMA, READINESS_VERSION
from .model import ReadinessResult, ReadinessState


def parse_readiness(value: object) -> ReadinessResult:
    """Parse one readiness result without accepting extension-shaped typos."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"invalid readiness UTF-8: {error}") from error
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid readiness JSON: {error}") from error
    elif isinstance(value, Mapping):
        try:
            data = dict(value)
        except Exception as error:
            raise ValueError(f"invalid readiness mapping: {error}") from error
    else:
        raise ValueError("readiness result must be JSON text, bytes, or a mapping")
    if not isinstance(data, dict):
        raise ValueError("readiness result must be an object")
    if any(not isinstance(key, str) for key in data):
        raise ValueError("readiness result property names must be strings")
    unknown = sorted(set(data) - {"schema", "version", "module", "state", "detail"})
    if unknown:
        raise ValueError(f"readiness result has unknown fields: {', '.join(unknown)}")
    version = data.get("version")
    if (
        data.get("schema") != READINESS_SCHEMA
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != READINESS_VERSION
    ):
        raise ValueError("readiness result has unsupported schema/version")
    module_id = data.get("module")
    if not isinstance(module_id, str) or not module_id.strip():
        raise ValueError("readiness result module must be a non-empty string")
    try:
        state = ReadinessState(data.get("state"))
    except ValueError as error:
        raise ValueError(
            "readiness state must be ready, configuration-empty, not-ready, or failed"
        ) from error
    detail = data.get("detail")
    if detail is not None and (not isinstance(detail, str) or not detail.strip()):
        raise ValueError("readiness detail must be a non-empty string when present")
    return ReadinessResult(
        module_id=module_id.strip(),
        state=state,
        detail=detail.strip() if detail else None,
    )
