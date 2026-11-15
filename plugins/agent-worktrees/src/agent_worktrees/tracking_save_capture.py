"""Bounded immutable selected evidence, not a canonical/history transaction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal
from machine_transport import LocalExecutionContext

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

MAX_CONTENT_BYTES = 256 * 1024
MAX_DOMAIN_BYTES = 3072
MAX_RECEIPT_BYTES = 32 * 1024
MAX_ITEMS = 64
MAX_TEXT = 4096
MAX_NODES = 8192
MAX_DEPTH = 16
DOMAINS = ("claims", "binding", "disposition")


class CaptureOmitted(ValueError):
    """Selected evidence cannot fit this process-local capture budget."""


@dataclass(frozen=True, slots=True)
class FrozenValue:
    kind: str
    atom: object
    children: tuple[FrozenValue, ...] = ()
    names: tuple[str, ...] = ()


def freeze_legacy(record: object, *, path: Path | None = None) -> tuple[FrozenValue, int]:
    remaining = [MAX_NODES, MAX_CONTENT_BYTES]
    root = _freeze(record, remaining, set())
    if path is not None:
        if root.kind not in {"object", "namespace"}:
            raise CaptureOmitted("legacy-record-object-required")
        names, children = list(root.names), list(root.children)
        loaded_from = _freeze(path, remaining, set())
        if "_loaded_from" in names:
            children[names.index("_loaded_from")] = loaded_from
        else:
            names.append("_loaded_from")
            children.append(loaded_from)
        root = FrozenValue(root.kind, root.atom, tuple(children), tuple(names))
    return root, MAX_CONTENT_BYTES - remaining[1]


def _freeze(
    value: object,
    remaining: list[int],
    ancestors: set[int],
    depth: int = 0,
) -> FrozenValue:
    remaining[0] -= 1
    remaining[1] -= 64
    if remaining[0] < 0 or remaining[1] < 0 or depth > MAX_DEPTH:
        raise CaptureOmitted("legacy-object-structure-bound")
    if value is None or isinstance(value, (bool, float, str, bytes, int, Path, date, Enum)):
        if isinstance(value, (str, bytes, Path)):
            raw = str(value) if isinstance(value, Path) else value
            if len(raw) > remaining[1]:
                raise CaptureOmitted("legacy-object-text-bound")
            remaining[1] -= len(raw.encode("utf-8") if isinstance(raw, str) else raw)
        elif isinstance(value, int):
            remaining[1] -= max(1, value.bit_length() // 8 + 1)
        if remaining[1] < 0:
            raise CaptureOmitted("legacy-object-byte-bound")
        return FrozenValue("atom", value)
    if id(value) in ancestors:
        raise CaptureOmitted("legacy-object-cyclic")
    ancestors.add(id(value))
    try:
        if isinstance(value, dict):
            if len(value) > remaining[0]:
                raise CaptureOmitted("legacy-object-member-bound")
            children = tuple(
                node
                for key, child in value.items()
                for node in (
                    _freeze(key, remaining, ancestors, depth + 1),
                    _freeze(child, remaining, ancestors, depth + 1),
                )
            )
            return FrozenValue("dict", None, children)
        if isinstance(value, (list, tuple, set, frozenset)):
            if len(value) > remaining[0]:
                raise CaptureOmitted("legacy-object-member-bound")
            return FrozenValue(
                type(value).__name__,
                None,
                tuple(_freeze(child, remaining, ancestors, depth + 1) for child in value),
            )
        if not is_dataclass(value) and type(value) is not SimpleNamespace:
            raise CaptureOmitted("unsupported-legacy-object")
        attributes = vars(value) if hasattr(value, "__dict__") else {}
        if len(attributes) > remaining[0]:
            raise CaptureOmitted("legacy-object-member-bound")
        names = tuple(
            dict.fromkeys(
                [item.name for item in fields(value)] + list(attributes)
                if is_dataclass(value)
                else list(attributes)
            )
        )
        names = tuple(
            name
            for name in names
            if depth
            or name
            not in {
                "_session_projection_dirty",
                "_session_projection_initial_registration",
                "_controller_projection_dirty",
            }
        )
        for name in names:
            if len(name) > remaining[1]:
                raise CaptureOmitted("legacy-object-field-name-bound")
            remaining[1] -= len(name.encode("utf-8"))
            if remaining[1] < 0:
                raise CaptureOmitted("legacy-object-field-name-bound")
        children = tuple(
            _freeze(getattr(value, name), remaining, ancestors, depth + 1) for name in names
        )
        return FrozenValue(
            "namespace" if type(value) is SimpleNamespace else "object",
            type(value),
            children,
            names,
        )
    finally:
        ancestors.remove(id(value))


def thaw_legacy(value: FrozenValue) -> object:
    if value.kind == "atom":
        return value.atom
    children = [thaw_legacy(child) for child in value.children]
    if value.kind == "dict":
        return dict(zip(children[::2], children[1::2], strict=True))
    if value.kind in {"list", "tuple", "set", "frozenset"}:
        return {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}[value.kind](
            children
        )
    if value.kind == "namespace":
        return SimpleNamespace(**dict(zip(value.names, children, strict=True)))
    if value.kind != "object" or not isinstance(value.atom, type):
        raise CaptureOmitted("invalid-frozen-legacy-object")
    result = object.__new__(value.atom)
    for name, child in zip(value.names, children, strict=True):
        object.__setattr__(result, name, child)
    return result


def text(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise CaptureOmitted("text-bound-or-type")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as error:
        raise CaptureOmitted("invalid-unicode") from error
    if len(raw) > MAX_TEXT:
        raise CaptureOmitted("text-byte-bound")
    return value


def encode(value: object, limit: int = MAX_DOMAIN_BYTES) -> bytes:
    digestible = bytearray()
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    for part in encoder.iterencode(value):
        digestible.extend(part.encode("utf-8"))
        if len(digestible) > limit:
            raise CaptureOmitted("domain-byte-bound")
    return bytes(digestible)


@dataclass(frozen=True, slots=True)
class Identity:
    project: str
    worktree_id: str
    machine: str
    incarnation: str | None
    head_session_id: str | None
    machine_identity_version: int | None = None
    environment: str | None = None
    execution_machine: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    domain: str
    data: bytes | None
    omission: str | None = None

    def value(self) -> object:
        return None if self.data is None else json.loads(self.data)


@dataclass(frozen=True, slots=True)
class Projection:
    identity: Identity | None
    domains: tuple[Evidence, ...]
    fence: bytes | None
    omission: str | None = None


@dataclass(frozen=True, slots=True)
class SaveReceipt:
    receipt_id: str
    path: str
    captured_at: str
    before: Projection
    after: Projection
    saved_sha256: str | None
    saved_bytes: int | None
    coverage: Literal["selected-fields", "partial"]
    changed_domains: tuple[str, ...]
    lock_qualification: Literal["record-lock-held", "unverified"]
    actor_qualification: Literal["unknown"] = "unknown"
    actor_id: None = None
    history_completeness: Literal["unknown"] = "unknown"
    exclusion: Literal["sidecar", "in-process", "unverified"] = "unverified"

    @property
    def encoded_bytes(self) -> int:
        return (
            2048
            + len(self.path.encode("utf-8"))
            + sum(
                len(item.data or b"")
                for state in (self.before, self.after)
                for item in state.domains
            )
            + sum(len(state.fence or b"") for state in (self.before, self.after))
            + sum(
                len(value.encode("utf-8"))
                for state in (self.before, self.after)
                if state.identity is not None
                for value in (
                    state.identity.project,
                    state.identity.worktree_id,
                    state.identity.machine,
                    state.identity.incarnation,
                    state.identity.head_session_id,
                    state.identity.environment,
                    state.identity.execution_machine,
                )
                if value is not None
            )
        )


@dataclass(frozen=True, slots=True)
class DeliveryIssue:
    reason: str
    omitted_count: int
    omitted_bytes: int | None
    saturated: bool = False
    count_basis: Literal["delivery-incidents"] = "delivery-incidents"
    coverage: Literal["incomplete"] = "incomplete"
    history_completeness: Literal["unknown"] = "unknown"


def omitted(reason: str) -> Projection:
    return Projection(None, tuple(Evidence(name, None, reason) for name in DOMAINS), None, reason)


def _claims(record: WorktreeRecord) -> list[dict]:
    if len(record.resources) > MAX_ITEMS:
        raise CaptureOmitted("claim-item-bound")
    return [
        {
            "kind": text(claim.kind),
            "ref": text(claim.ref),
            "state": text(claim.state),
            "created_at": text(claim.created_at),
            "note": text(claim.note),
            "handoff_bundle": text(claim.handoff_bundle),
            "has_producer": claim.has_producer,
            "metadata_opaque": claim.metadata_opaque,
        }
        for claim in record.resources
    ]


def reference(value: object, *, nullable: bool = False) -> str | None:
    result = text(value, nullable=nullable)
    if result is not None and (not result or len(result.encode("utf-8")) > 512):
        raise CaptureOmitted("source-reference-bound-or-empty")
    return result


def required_reference(value: object) -> str:
    result = reference(value)
    if result is None:
        raise CaptureOmitted("source-reference-unavailable")
    return result


def _binding(record: WorktreeRecord) -> dict:
    active = record.active_effort
    return {
        "active_effort": None
        if active is None
        else {
            "path": text(active.path),
            "participant": text(active.participant),
            "slice": text(active.slice),
        },
        "effort_revision": record.effort_revision,
    }


def _disposition(record: WorktreeRecord) -> dict:
    return {
        "status": text(record.status),
        "title": text(record.title, nullable=True),
        "follow_up": record.follow_up,
        "paused": record.paused,
        "summary": text(record.summary),
        "activity": text(record.activity),
        "paused_revision": record.paused_revision,
    }


def project(record: WorktreeRecord | None) -> Projection:
    if record is None:
        return Projection(None, tuple(Evidence(name, b"null") for name in DOMAINS), b"null")
    identity = None
    reason = None
    try:
        head = None
        if len(record.head_transitions) <= MAX_ITEMS and len(record.sessions or []) <= MAX_ITEMS:
            head = reference(record.resolved_head_session, nullable=True)
        else:
            reason = "head-evidence-bound"
        identity = Identity(
            required_reference(record.repo),
            required_reference(record.worktree_id),
            required_reference(record.machine),
            reference(record.creation_nonce, nullable=True),
            head,
            record.machine_fields.get("machine_identity_version"),
            record.machine_fields.get("environment"),
            record.machine_fields.get("execution_machine"),
        )
        fence = encode(
            {
                "lifecycle_revision": record.lifecycle_revision,
                "head_revision": record.head_revision,
                "effort_revision": record.effort_revision,
                "paused_revision": record.paused_revision,
            },
            512,
        )
    except (CaptureOmitted, ValueError, TypeError, UnicodeError) as error:
        reason, fence = str(error)[:128], None
    domains = []
    for name, capture in (
        ("claims", _claims),
        ("binding", _binding),
        ("disposition", _disposition),
    ):
        try:
            domains.append(Evidence(name, encode(capture(record))))
        except (CaptureOmitted, ValueError, TypeError, UnicodeError) as error:
            domains.append(Evidence(name, None, str(error)[:128]))
    return Projection(identity, tuple(domains), fence, reason)


def content_bytes(content: str) -> bytes:
    if len(content) > MAX_CONTENT_BYTES:
        raise CaptureOmitted("saved-record-character-bound")
    raw = content.encode("utf-8")
    if len(raw) > MAX_CONTENT_BYTES:
        raise CaptureOmitted("saved-record-byte-bound")
    return raw


def _validate_snapshot(
    value: object,
    remaining: list[int],
    ancestors: set[int],
    depth: int = 0,
) -> None:
    remaining[0] -= 1
    if remaining[0] < 0 or depth > MAX_DEPTH:
        raise CaptureOmitted("saved-record-structure-bound")
    if isinstance(value, (str, bytes)):
        if len(value) > remaining[1]:
            raise CaptureOmitted("saved-record-expanded-text-bound")
        raw = value.encode("utf-8") if isinstance(value, str) else value
        remaining[1] -= len(raw)
        if remaining[1] < 0:
            raise CaptureOmitted("saved-record-expanded-text-bound")
    elif isinstance(value, (dict, list, set)):
        if id(value) in ancestors:
            raise CaptureOmitted("saved-record-cyclic-evidence")
        ancestors.add(id(value))
        try:
            if isinstance(value, dict):
                for key, child in value.items():
                    _validate_snapshot(key, remaining, ancestors, depth + 1)
                    _validate_snapshot(child, remaining, ancestors, depth + 1)
            else:
                for child in value:
                    _validate_snapshot(child, remaining, ancestors, depth + 1)
        finally:
            ancestors.remove(id(value))


def parse(
    content: str, path: Path, *, reporting_context: LocalExecutionContext | None = None,
) -> WorktreeRecord:
    from . import tracking

    content_bytes(content)
    data = tracking._yaml_safe_load(content)
    if not isinstance(data, dict):
        raise CaptureOmitted("saved-record-not-mapping")
    _validate_snapshot(data, [MAX_NODES, MAX_CONTENT_BYTES], set())
    return tracking.parse_record_data(data, path=path, reporting_context=reporting_context)


def receipt(
    receipt_id: str,
    path: Path,
    before: Projection,
    after: Projection,
    raw: bytes | None,
    *,
    record_held: bool,
    sidecar_held: bool = False,
) -> SaveReceipt:
    path_text = text(str(path))
    if path_text is None:
        raise CaptureOmitted("save-path-unavailable")
    changed = tuple(
        old.domain
        for old, new in zip(before.domains, after.domains, strict=True)
        if old.data is not None and new.data is not None and old.data != new.data
    )
    partial = (
        not record_held
        or raw is None
        or before.omission is not None
        or after.omission is not None
        or any(item.omission is not None for state in (before, after) for item in state.domains)
        or after.identity is None
        or after.identity.incarnation is None
    )
    result = SaveReceipt(
        receipt_id,
        path_text,
        datetime.now(timezone.utc).isoformat(),
        before,
        after,
        None if raw is None else hashlib.sha256(raw).hexdigest(),
        None if raw is None else len(raw),
        "partial" if partial else "selected-fields",
        changed,
        "record-lock-held" if record_held else "unverified",
        exclusion="sidecar" if sidecar_held else ("in-process" if record_held else "unverified"),
    )
    if result.encoded_bytes > MAX_RECEIPT_BYTES:
        raise CaptureOmitted("receipt-byte-bound")
    return result
