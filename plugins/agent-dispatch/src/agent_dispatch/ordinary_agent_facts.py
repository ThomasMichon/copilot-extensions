"""Concrete identities from ordinary Bridge creation and resumed SessionInfo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from .queue_records import TaskError
from .worktree_attribution import foreign_task_id


@dataclass(frozen=True)
class AgentIdentity:
    bridge: str
    native: str
    worktree: str
    project: str
    path: str | None


def native_conflict(native: str, known: list[str], retired: list[str]) -> bool:
    """Retired history cannot veto a different current, unretired native head."""
    return native in retired or any(item != native and item not in retired for item in known)


def validate_created(info: dict, *, project: str | None = None) -> dict:
    """Require actual creation readiness, never synthesize it for a resume."""
    if not isinstance(info, dict):
        raise TaskError("ordinary bridge creation must be a JSON object")
    connection = info.get("connection")
    if (
        not isinstance(info.get("session_id"), str) or not info["session_id"]
        or info.get("ready") is not True
        or info.get("status") != "awaiting_first_prompt"
        or not isinstance(connection, dict)
        or any(not isinstance(connection.get(field), str) or not connection[field]
               for field in ("worktree_id", "native_session_id", "repo"))
        or (project is not None and connection["repo"] != project)
    ):
        raise TaskError("ordinary bridge creation identity/readiness is unproven")
    return info


def validate_resumed(info: dict, *, project: str | None = None) -> dict:
    """Validate real ready-to-send SessionInfo, not a created-agent receipt."""
    if not isinstance(info, dict) or any(
        not isinstance(info.get(field), str) or not info[field]
        for field in ("session_id", "acp_session_id", "durable_session_id",
                      "worktree_id", "project", "target_dir")
    ):
        raise TaskError("ordinary resume did not prove concrete native/worktree identity")
    if (
        info["durable_session_id"] != info["acp_session_id"]
        or info.get("status") != "idle"
        or info.get("read_only") is not False
        or type(info.get("at_rest")) is not bool
        or "liveness" not in info
        or (info["liveness"] is not None and info["liveness"] != "active")
        or any(field in info for field in ("ready", "created", "acquisition_outcome", "connection"))
        or (project is not None and info["project"] != project)
    ):
        raise TaskError("ordinary resumed agent identity/readiness is unproven")
    return info


def identity(info: dict) -> AgentIdentity:
    if info.get("fact_kind") == "resumed":
        validate_resumed(info)
        return AgentIdentity(
            info["session_id"], info["acp_session_id"], info["worktree_id"],
            info["project"], info["target_dir"],
        )
    validate_created(info)
    connection = info["connection"]
    return AgentIdentity(
        info["session_id"], connection["native_session_id"], connection["worktree_id"],
        connection["repo"], connection.get("target_dir"),
    )


def owner_record(data: dict, reservation: dict, task_id: str) -> dict:
    """Require the exact active owner record in the stored execution repository."""
    allocation_task = task_id
    if reservation.get("allocation_reservation_key") is not None:
        from .spawn_factories import SpawnPreparationRetained
        from .workspace_binding import allocation_provenance

        try:
            allocation_task, _, _ = allocation_provenance(task_id, reservation)
        except (SpawnPreparationRetained, KeyError, TypeError, ValueError) as exc:
            raise TaskError(f"ground allocation provenance is unproven: {exc}") from exc
    record = data
    if (
        not isinstance(record, dict)
        or not {
            "id", "repo", "path", "status", "creation_nonce", "session_count",
            "last_session_id", "execution_leg", "execution_leg_opaque",
            "session_backend", "session_backend_opaque", "sessions_indexed",
        } <= record.keys()
        or any(
            not isinstance(record.get(field), str) or not record[field]
            for field in ("id", "repo", "path", "status")
        )
    ):
        raise TaskError("ground owner returned no exact worktree record")
    if not {"dispatch_attempt", "dispatch_attempt_opaque"} <= record.keys():
        raise TaskError("exact owner record lacks dispatch-attribution capability")
    path = record["path"]
    posix, windows = PurePosixPath(path), PureWindowsPath(path)
    if (
        "\0" in path or not (posix.is_absolute() or windows.is_absolute())
        or ".." in posix.parts or ".." in windows.parts
        or any(type(record[field]) is not bool for field in (
            "sessions_indexed", "execution_leg_opaque", "session_backend_opaque",
            "dispatch_attempt_opaque",
        ))
        or record["session_count"] is not None
        and (type(record["session_count"]) is not int or record["session_count"] < 0)
        or record["creation_nonce"] is not None
        and not isinstance(record["creation_nonce"], str)
    ):
        raise TaskError("ground owner returned invalid workspace or history metadata")
    attribution = record["dispatch_attempt"]
    if record["dispatch_attempt_opaque"] or attribution is not None and (
        not isinstance(attribution, dict)
        or not isinstance(attribution.get("task_id"), str)
        or not attribution["task_id"]
    ):
        raise TaskError("ground worktree dispatch attribution is opaque or invalid")
    if record["status"] == "finalizing":
        raise TaskError("ground workspace finalization is in progress; retry the assignment")
    if (
        record["id"] != reservation.get("worktree")
        or record["repo"] != reservation.get("allocation_project")
        or reservation.get("worktree_path") is not None
        and record["path"] != reservation["worktree_path"]
        or record.get("status") not in {"active", "pushed"}
        or foreign_task_id(record, allocation_task)
        or record.get("session_live") is True
        or record.get("native_liveness") not in (None, "stopped", "gone")
        or record.get("session_retired") is True
    ):
        raise TaskError("ground worktree identity, holder, or native history is unproven")
    head = record.get("last_session_id")
    if head is not None and (not isinstance(head, str) or not head):
        raise TaskError("ground owner returned an invalid native head")
    if "head_session_id" in record and record["head_session_id"] != head:
        raise TaskError("ground owner returned conflicting native heads")
    return record


def sessionless(record: dict) -> bool:
    """Only positively indexed-empty history permits a first conversation."""
    return (
        record.get("status") == "active"
        and record.get("sessions_indexed") is True
        and record.get("execution_leg_opaque") is False
        and record.get("session_backend_opaque") is False
        and type(record.get("session_count")) is int
        and record["session_count"] == 0
        and record.get("last_session_id") is None
        and record.get("sessions", []) == []
        and not any(record.get(field) is not None for field in (
            "execution_leg", "session_backend", "provider_binding",
        ))
    )
