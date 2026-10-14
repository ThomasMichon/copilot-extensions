"""Strict, repository-scoped reads of one durable worktree record."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import get_args

import yaml

from . import config as cfg
from . import tracking


class RecordReadError(ValueError):
    """An exact metadata read failed, without implying lifecycle state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        config = cfg.load_config(include_control_plane_related_pr=False)
        record_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    except (OSError, RuntimeError, ValueError) as exc:
        raise RecordReadError("project-unavailable", str(exc)) from exc

    try:
        raw = record_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RecordReadError("record-missing", "Exact worktree record does not exist") from exc
    except OSError as exc:
        raise RecordReadError("record-unreadable", str(exc)) from exc
    except UnicodeError as exc:
        raise RecordReadError("record-malformed", "Record is not UTF-8") from exc

    try:
        data = tracking._yaml_safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Record must be a YAML mapping")
        for key in ("worktree_id", "repo", "worktree_path", "branch", "status"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"Record requires a nonempty string: {key}")
        if data["worktree_id"] != worktree_id:
            raise RecordReadError("id-mismatch", "Stored worktree ID does not match the request")
        if data["repo"] not in config.repos:
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
        json.dumps(worktree, allow_nan=False)
        return worktree
    except RecordReadError:
        raise
    except (yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise RecordReadError("record-malformed", str(exc)) from exc
