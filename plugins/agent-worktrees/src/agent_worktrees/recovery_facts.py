"""Disconnected recovery-envelope codecs; no lifecycle writer or authority."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from uuid import UUID

SCHEMA = "agent-worktrees.recovery-fact"
MAX_BYTES = 64 * 1024
MAX_DEPTH = 32


class RecoveryError(ValueError):
    """A scoped refusal, not a foreign-owner or absent-resource verdict."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def identity(value: object) -> str:
    if not isinstance(value, str):
        raise RecoveryError("malformed_identity")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise RecoveryError("malformed_identity") from None
    return value


def _object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("duplicate_field")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise RecoveryError("malformed_json")


def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise RecoveryError("malformed_json")
    try:
        exact = Decimal(text)
    except InvalidOperation:
        raise RecoveryError("number_precision_unsupported") from None
    if exact != Decimal(str(value)):
        raise RecoveryError("number_precision_unsupported")
    return value


def _integer(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise RecoveryError("malformed_json") from None


def _depth(value: object, level: int = 0) -> None:
    if level > MAX_DEPTH:
        raise RecoveryError("record_limit")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def _capabilities(value: object) -> dict:
    if not isinstance(value, dict):
        raise RecoveryError("malformed_capabilities")
    for operation, required in value.items():
        if (
            not isinstance(operation, str) or not operation
            or not isinstance(required, list)
            or any(not isinstance(item, str) or not item for item in required)
            or len(set(required)) != len(required)
        ):
            raise RecoveryError("malformed_capabilities")
    if "read" not in value or "patch" not in value:
        raise RecoveryError("malformed_capabilities")
    return value


def _validate(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise RecoveryError("unsupported_schema")
    if type(value.get("version")) is not int or value["version"] != 1:
        raise RecoveryError("unsupported_schema")
    for field in ("repository_id", "resource_id", "operation_id", "writer_incarnation"):
        identity(value.get(field))
    if type(value.get("revision")) is not int or value["revision"] < 1:
        raise RecoveryError("malformed_revision")
    if not isinstance(value.get("kind"), str) or not value["kind"]:
        raise RecoveryError("malformed_kind")
    if not isinstance(value.get("payload"), dict):
        raise RecoveryError("malformed_payload")
    _capabilities(value.get("required_capabilities"))
    return value


def require_capabilities(value: dict, operation: str, supported: frozenset[str]) -> None:
    required = _capabilities(value.get("required_capabilities"))
    if operation not in required or not set(required[operation]) <= supported:
        raise RecoveryError("required_capability_unsupported")


def _canonical(value: dict) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise RecoveryError("malformed_json") from None
    except RecursionError:
        raise RecoveryError("record_limit") from None


def decode_fact(raw: bytes, *, supported: frozenset[str]) -> dict:
    """Read one bounded fact, preserving unknown additive fields recursively.

    Capability sets come from the semantic owner, never package version. A
    parsed writer incarnation is historical evidence, not current authority.
    """
    if len(raw) > MAX_BYTES:
        raise RecoveryError("record_limit")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant,
            parse_float=_number, parse_int=_integer,
        )
        _depth(value)
    except (UnicodeError, json.JSONDecodeError):
        raise RecoveryError("malformed_json") from None
    except RecursionError:
        raise RecoveryError("record_limit") from None
    result = _validate(value)
    require_capabilities(result, "read", supported)
    if len(_canonical(result)) > MAX_BYTES:
        raise RecoveryError("record_limit")
    return result


def encode_fact(value: dict, *, supported: frozenset[str]) -> bytes:
    """Canonical serialization only: no write, acknowledgement or revision bump."""
    raw = _canonical(value)
    decoded = decode_fact(raw, supported=supported)
    if decoded != value:
        raise RecoveryError("malformed_json")
    return raw


def patch_payload(
    value: dict, changes: Mapping[str, object], *,
    supported: frozenset[str], known_paths: frozenset[tuple[str, ...]],
) -> dict:
    """Pure candidate edit of named leaves; preserve every unmentioned field.

    The caller must separately authorize and commit any effect. Envelope
    identity, revision and incarnation are not editable through this function.
    """
    result = decode_fact(encode_fact(value, supported=supported), supported=supported)
    require_capabilities(result, "patch", supported)
    if not isinstance(changes, Mapping):
        raise RecoveryError("malformed_payload")

    def merge(target: dict, updates: Mapping, prefix: tuple[str, ...]) -> None:
        for key, item in updates.items():
            if not isinstance(key, str):
                raise RecoveryError("unknown_patch_field")
            path = (*prefix, key)
            prior = target.get(key)
            if isinstance(item, Mapping):
                if not any(
                    candidate[:len(path)] == path and len(candidate) > len(path)
                    for candidate in known_paths
                ):
                    raise RecoveryError("unknown_patch_field")
                if key in target and not isinstance(prior, dict):
                    raise RecoveryError("incompatible_patch_shape")
                child = copy.deepcopy(prior) if isinstance(prior, dict) else {}
                merge(child, item, path)
                target[key] = child
            else:
                if path not in known_paths:
                    raise RecoveryError("unknown_patch_field")
                if isinstance(prior, (dict, list)):
                    raise RecoveryError("incompatible_patch_shape")
                if isinstance(item, (list, tuple)):
                    raise RecoveryError("incompatible_patch_shape")
                target[key] = copy.deepcopy(item)

    try:
        merge(result["payload"], changes, ())
    except RecursionError:
        raise RecoveryError("record_limit") from None
    encode_fact(result, supported=supported)
    return result
