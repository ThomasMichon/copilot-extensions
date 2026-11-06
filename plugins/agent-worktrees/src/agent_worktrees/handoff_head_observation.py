"""Passive owning comparison of consumption, durable head and Picker choice."""

from __future__ import annotations

import argparse
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import config as cfg
from . import output, sessions, tracking
from .handoff_head_io import (
    ENTRY_LIMIT,
    FILE_BYTES,
    PATH_LIMIT,
    TOTAL_BYTES,
    WORK_SECONDS,
    ObservationFailure,
    Snapshot,
)
from .handoff_head_receipts import read_expected, segment

CONTRACT = "agent-worktrees.handoff-head-observation"
VERSION = 1
COMMAND = "handoff-head-observation"


def declaration() -> dict[str, Any]:
    """Advertise this selected runtime's optional read, without reading receipts."""
    return {"contract": CONTRACT, "version": VERSION, "command": COMMAND}


def add_parsers(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        COMMAND,
        allow_abbrev=False,
        help="Passively compare one exact worktree's handoff consumer and head (JSON)",
    )
    parser.add_argument(
        "--worktree", required=True, help="Exact worktree id in the selected project"
    )
    parser.add_argument("--contract-version", type=int, choices=(VERSION,), default=VERSION)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_observation)


def _record(
    snapshot: Snapshot,
    path: Path,
    project: str,
    worktree_id: str,
) -> tracking.WorktreeRecord:
    raw = snapshot.read(path)
    if raw is None:
        raise ObservationFailure("record-unavailable")
    data = tracking._yaml_safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ObservationFailure("invalid-record")
    if data.get("repo") != project or data.get("worktree_id") != worktree_id:
        raise ObservationFailure("record-subject-mismatch", unavailable=False)
    if not data.get("creation_nonce"):
        raise ObservationFailure("legacy-record-incarnation", unavailable=False)
    segment(data["creation_nonce"])
    for key in ("sessions", "handoffs"):
        values = data.get(key)
        if values is None:
            values = []
        if not isinstance(values, list) or len(values) > ENTRY_LIMIT:
            raise ObservationFailure("observation-record-budget")
        if any(not isinstance(item, dict) for item in values):
            raise ObservationFailure("invalid-record")
    for entry in data.get("sessions") or []:
        segment(entry.get("session_id"))
        if entry.get("state", "active") not in {"active", "yielded", "handed-off", "concluded"}:
            raise ObservationFailure("unsupported-session-state")
        activations = entry.get("activations")
        if activations is None:
            activations = []
        if not isinstance(activations, list) or len(activations) > 256:
            raise ObservationFailure("observation-activation-budget")
        if any(
            not isinstance(item, dict)
            or type(item.get("ordinal")) is not int
            or item["ordinal"] <= 0
            for item in activations
        ):
            raise ObservationFailure("invalid-activation-ledger")
        for activation in activations:
            for key in ("started_at", "start_recorded_at"):
                value = activation.get(key)
                if isinstance(value, datetime):
                    value = value.isoformat()
                if not isinstance(value, str) or not value or len(value) > 64:
                    raise ObservationFailure("unrecorded-activation-timestamp", unavailable=False)
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    raise ObservationFailure(
                        "invalid-activation-timestamp", unavailable=False
                    ) from None
    for handoff in data.get("handoffs") or []:
        segment(handoff.get("token"))
        segment(handoff.get("predecessor"))
        if type(handoff.get("ordinal")) is not int or handoff["ordinal"] <= 0:
            raise ObservationFailure("invalid-handoff-ledger")
    transitions = data.get("head_transitions")
    if transitions is None:
        transitions = []
    if not isinstance(transitions, list) or len(transitions) > 256:
        raise ObservationFailure("observation-head-ledger-budget")
    revisions: set[int] = set()
    for transition in transitions:
        if (
            not isinstance(transition, dict)
            or type(transition.get("revision")) is not int
            or transition["revision"] <= 0
            or transition["revision"] in revisions
        ):
            raise ObservationFailure("invalid-head-ledger")
        revisions.add(transition["revision"])
        if transition.get("session_id") is not None:
            segment(transition["session_id"])
    if revisions and max(revisions) != data.get("head_revision"):
        raise ObservationFailure("inconsistent-head-revision")
    for key in ("lifecycle_revision", "head_revision"):
        if type(data.get(key, 0)) is not int or data.get(key, 0) < 0:
            raise ObservationFailure("invalid-record-revision")
    if data.get("head_session") is not None:
        segment(data["head_session"])
    record = tracking.parse_record_data(data, path=path)
    ids = [segment(entry.session_id) for entry in record.sessions or []]
    if len(set(ids)) != len(ids):
        raise ObservationFailure("ambiguous-session-registry", unavailable=False)
    for value in (record.resolved_head_session, record.head_session):
        if value is not None:
            segment(value)
    return record


def _picker(snapshot: Snapshot, record: tracking.WorktreeRecord, root: Path) -> str | None:
    available: dict[str, bool] = {}

    def has_data(session_id: str) -> bool:
        sid = segment(session_id)
        if sid in available:
            return available[sid]
        directory = root / sid
        info = snapshot.inspect(directory, presence_only=True)
        if info is None:
            available[sid] = False
            return False
        if not stat.S_ISDIR(info[2]):
            raise ObservationFailure("invalid-session-directory")
        data_paths = [
            snapshot.inspect(directory / name, presence_only=True)
            for name in ("session.db", "events.jsonl")
        ]
        if any(info is not None and not stat.S_ISREG(info[2]) for info in data_paths):
            raise ObservationFailure("invalid-conversation-file")
        available[sid] = any(info is not None for info in data_paths)
        return available[sid]

    def latest(_path: str, entries: list | None) -> str | None:
        best_id, best_time = None, ""
        for entry in entries or []:
            sid = segment(entry.session_id)
            if not has_data(sid):
                continue
            raw = snapshot.read(root / sid / "workspace.yaml", limit=64 * 1024)
            if raw is None:
                updated = entry.started_at or ""
            else:
                value = yaml.safe_load(raw.decode("utf-8"))
                if value is not None and not isinstance(value, dict):
                    raise ObservationFailure("invalid-picker-workspace")
                updated = str(value.get("updated_at", "")) if value else ""
            if updated > best_time:
                best_id, best_time = sid, updated
        return best_id

    return sessions.resolve_resume_target(
        record,
        conversation_data=has_data,
        latest_session=latest,
    )


def observe(
    project: str, worktree_id: str, project_root: Path, session_root: Path
) -> dict[str, Any]:
    """Read a sampled comparison; never claim launch, delivery or retirement."""
    snapshot = Snapshot()
    result: dict[str, Any] = {
        "contract": CONTRACT,
        "contract_version": VERSION,
        "project": project,
        "worktree_id": worktree_id,
        "status": "unavailable",
        "reason": "not-observed",
        "expected": None,
        "durable_head": {"status": "unavailable"},
        "picker": {"status": "unavailable"},
        "limits": {
            "file_bytes": FILE_BYTES,
            "total_bytes": TOTAL_BYTES,
            "paths": PATH_LIMIT,
            "sessions": ENTRY_LIMIT,
            "directory_entries": ENTRY_LIMIT,
            "cooperative_seconds": WORK_SECONDS,
        },
    }
    try:
        segment(project)
        segment(worktree_id)
        namespace = project_root / "worktrees"
        path = namespace / f"{worktree_id}.yaml"
        record = _record(snapshot, path, project, worktree_id)
        result["subject"] = {
            "creation_nonce": record.creation_nonce,
            "lifecycle_revision": record.lifecycle_revision,
            "head_revision": record.head_revision,
        }
        result["durable_head"] = {
            "status": "observed",
            "session_id": record.resolved_head_session,
            "revision": record.head_revision,
        }
        result["picker"] = {
            "status": "observed",
            "session_id": _picker(snapshot, record, session_root),
        }
        expected = read_expected(
            snapshot,
            record,
            namespace / worktree_id / "handoff",
            session_root,
        )
        result["expected"] = expected.expected()
        durable_matches = record.resolved_head_session == expected.consumer
        picker_matches = result["picker"]["session_id"] == expected.consumer
        result.update(
            status="match" if durable_matches and picker_matches else "mismatch",
            reason="sampled-identities-match"
            if durable_matches and picker_matches
            else ("durable-head-mismatch" if not durable_matches else "picker-target-mismatch"),
        )
    except ObservationFailure as error:
        result.update(
            status="unavailable" if error.unavailable else "unknown",
            reason=error.reason,
        )
        if error.reason == "source-changed":
            result["durable_head"] = {"status": "stale"}
            result["picker"] = {"status": "stale"}
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        yaml.YAMLError,
    ):
        result.update(status="unavailable", reason="source-unreadable-or-invalid")
    try:
        snapshot.verify()
    except ObservationFailure as error:
        result.update(
            status="unavailable" if error.unavailable else "unknown",
            reason=error.reason,
            expected=None,
            durable_head={"status": "stale"},
            picker={"status": "stale"},
        )
    except OSError:
        result.update(status="unavailable", reason="source-recheck-unavailable", expected=None)
    result["work"] = snapshot.work()
    return result


def cmd_observation(args: argparse.Namespace) -> int:
    """Serve the optional read within the selected project's existing namespace."""
    project = cfg.project_name()
    result = observe(
        project, args.worktree, cfg.project_dir(project), sessions._session_state_dir()
    )
    output._json_output(result)
    return 0
