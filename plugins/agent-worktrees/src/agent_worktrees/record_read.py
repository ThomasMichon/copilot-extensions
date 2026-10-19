"""Strict, repository-scoped reads of one durable worktree record."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath, PureWindowsPath
from typing import get_args

import yaml

from . import config as cfg
from . import tracking
from .record_config import configured_repositories
from .record_io import read_record_mapping


class RecordReadError(ValueError):
    """An exact metadata read failed, without implying lifecycle state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 10_000
MAX_JSON_BYTES = 1024 * 1024


@dataclass
class _JsonBudget:
    nodes: int = 0
    size: int = 0

    def charge(self, size: int, *, depth: int, node: bool = False) -> None:
        self.nodes += int(node)
        self.size += size
        if depth > MAX_JSON_DEPTH or self.nodes > MAX_JSON_NODES or self.size > MAX_JSON_BYTES:
            raise ValueError("Record JSON exceeds depth, expanded-node, or output-size limits")


def _json_value(
    value: object, ancestors: frozenset[int] = frozenset(),
    *, budget: _JsonBudget | None = None, depth: int = 0,
) -> object:
    budget = budget if budget is not None else _JsonBudget()
    budget.charge(0, depth=depth, node=True)
    if isinstance(value, date):
        value = value.isoformat()
    if isinstance(value, (dict, list)):
        if id(value) in ancestors:
            raise ValueError("Record contains a cyclic YAML value")
        nested = ancestors | {id(value)}
        budget.charge(2 + 2 * max(len(value) - 1, 0), depth=depth)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Record JSON mappings require string keys")
            converted = {}
            for key, item in value.items():
                if len(key) > MAX_JSON_BYTES:
                    raise ValueError("Record JSON key exceeds the output-size limit")
                budget.charge(len(json.dumps(key)) + 2, depth=depth)
                converted[key] = _json_value(item, nested, budget=budget, depth=depth + 1)
            return converted
        return [_json_value(item, nested, budget=budget, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, str) and len(value) > MAX_JSON_BYTES:
            raise ValueError("Record JSON string exceeds the output-size limit")
        budget.charge(len(json.dumps(value, allow_nan=False)), depth=depth)
        return value
    raise TypeError(f"Record contains a non-JSON value: {type(value).__name__}")


def _validate_head_ledger(data: dict) -> None:
    if "head_transitions" not in data:
        return
    transitions = data["head_transitions"]
    if not isinstance(transitions, list):
        raise ValueError("Record head_transitions must be a list")
    revisions: set[int] = set()
    for transition in transitions:
        if not isinstance(transition, dict):
            raise ValueError("Each head transition must be a mapping")
        revision = transition.get("revision")
        if type(revision) is not int or revision <= 0 or revision in revisions:
            raise ValueError("Each head transition requires a unique positive integer revision")
        revisions.add(revision)
        session_id = transition.get("session_id")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("Head transition session_id must be a nonempty string or null")
        ordinal = transition.get("handoff_ordinal")
        if ordinal is not None and (type(ordinal) is not int or ordinal <= 0):
            raise ValueError("Head transition handoff_ordinal must be a positive integer or null")


def read_exact_record(worktree_id: str) -> dict[str, object]:
    """Read only the selected project's exact record; never authorize launch."""
    if (
        not isinstance(worktree_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", worktree_id) is None
        or ".." in worktree_id
        or PureWindowsPath(worktree_id).is_reserved()
    ):
        raise RecordReadError("invalid-id", "Expected one exact worktree record ID")

    try:
        project = cfg.project_name()
        repositories = configured_repositories(project)
        record_path = cfg.tracking_dir(project) / f"{worktree_id}.yaml"
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        raise RecordReadError("project-unavailable", str(exc)) from exc

    try:
        data = read_record_mapping(record_path)
    except FileNotFoundError as exc:
        raise RecordReadError("record-missing", "Exact worktree record does not exist") from exc
    except OSError as exc:
        raise RecordReadError("record-unreadable", str(exc)) from exc
    except UnicodeError as exc:
        raise RecordReadError("record-malformed", "Record is not UTF-8") from exc
    except yaml.YAMLError as exc:
        raise RecordReadError("record-malformed", str(exc)) from exc
    except ValueError as exc:
        raise RecordReadError("record-malformed", str(exc)) from exc

    try:
        for key in ("worktree_id", "repo", "worktree_path", "branch", "status"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"Record requires a nonempty string: {key}")
        if data["worktree_id"] != worktree_id:
            raise RecordReadError("id-mismatch", "Stored worktree ID does not match the request")
        if data["repo"] not in repositories:
            raise RecordReadError(
                "repository-mismatch", "Record repository is not configured in this project"
            )

        path = data["worktree_path"]
        posix, windows = PurePosixPath(path), PureWindowsPath(path)
        if (
            "\0" in path
            or not (posix.is_absolute() or windows.is_absolute())
            or ".." in posix.parts
            or ".." in windows.parts
        ):
            raise RecordReadError(
                "path-invalid", "Record requires an absolute, non-traversing worktree path"
            )
        if data["status"] not in get_args(tracking.WorktreeStatus):
            raise ValueError("Record has an unknown status")
        for key in (
            "machine",
            "platform",
            "creation_nonce",
            "owner",
            "owner_ref",
            "caller_worktree",
            "parent_session",
            "head_session",
            "bound_agent",
        ):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                raise ValueError(f"Record field must be a string or null: {key}")
        if "checkout_managed" in data and not isinstance(data["checkout_managed"], bool):
            raise ValueError("Record checkout_managed must be a boolean")
        raw_sessions = data.get("sessions")
        if raw_sessions is not None:
            if not isinstance(raw_sessions, list):
                raise ValueError("Record sessions must be a list or null")
            for entry in raw_sessions:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("session_id"), str)
                    or not entry["session_id"]
                ):
                    raise ValueError("Each recorded session requires a nonempty session_id")
                if "state" in entry and entry["state"] not in get_args(tracking.SessionState):
                    raise ValueError("Recorded session has an unknown state")
        _validate_head_ledger(data)
        for key in ("execution_leg", "session_backend"):
            binding = data.get(key)
            if isinstance(binding, dict) and "version" in binding:
                if type(binding["version"]) is not int:
                    raise ValueError(f"Record {key} version must be an integer")
        # Compatibility parsing may stringify fields, expanding shared aliases.
        _json_value(data)
        record = tracking.parse_record_data(data, path=record_path)
        leg = tracking.derive_execution_leg(record)
        worktree = {
            "id": record.worktree_id,
            "repo": record.repo,
            "path": record.worktree_path,
            "branch": record.branch,
            "creation_nonce": record.creation_nonce,
            "status": record.status,
            "session_count": len(record.sessions) if record.sessions is not None else None,
            "last_session_id": record.resolved_head_session,
            "execution_leg": leg.to_dict() if leg is not None else record.execution_leg_raw,
            "execution_leg_opaque": record.execution_leg_opaque,
            "session_backend": (
                record.session_backend.to_dict()
                if record.session_backend is not None
                else record.session_backend_raw
            ),
            "session_backend_opaque": record.session_backend_opaque,
            "sessions_indexed": record.sessions is not None,
            "dispatch_attempt": (
                record.dispatch_attempt.to_dict()
                if record.dispatch_attempt is not None
                else record.dispatch_attempt_raw
            ),
            "dispatch_attempt_opaque": record.dispatch_attempt_opaque,
            "bound_agent": record.bound_agent,
        }
        for key in (
            "machine",
            "platform",
            "owner",
            "owner_ref",
            "caller_worktree",
            "parent_session",
            "checkout_managed",
        ):
            worktree[key] = data.get(key)
        return json.loads(json.dumps(_json_value(worktree), allow_nan=False))
    except RecordReadError:
        raise
    except (yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise RecordReadError("record-malformed", str(exc)) from exc
