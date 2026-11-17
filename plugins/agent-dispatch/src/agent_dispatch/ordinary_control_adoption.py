"""Accept a Bridge control epoch without replacing its native or birth facts."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from typing import Any

from .ordinary_agent_facts import identity
from .ordinary_writer_admission import REQUIREMENT, writer_requirement
from .queue_records import TaskError
from .queue_task_relationships import valid_record_writer

HISTORY = "retained_control_history"
ADOPTION = "retained_metadata_adoption"


def protect_history(conn: sqlite3.Connection) -> None:
    """Commit a rollback-safe write fence with the first adopted control record."""
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_control_history_retained"
        " BEFORE UPDATE OF agent_info ON spawn_reservations"
        " WHEN CASE WHEN json_valid(OLD.agent_info)"
        f" THEN json_type(OLD.agent_info, '$.{HISTORY}') IS NOT NULL ELSE 0 END"
        " AND CASE WHEN json_valid(NEW.agent_info) THEN ("
        f" json_extract(NEW.agent_info, '$.{HISTORY}') IS NOT json_extract(OLD.agent_info, '$.{HISTORY}')"
        f" OR json_extract(NEW.agent_info, '$.{ADOPTION}') IS NOT json_extract(OLD.agent_info, '$.{ADOPTION}')"
        " OR json_extract(NEW.agent_info, '$.startup_invocation')"
        " IS NOT json_extract(OLD.agent_info, '$.startup_invocation')"
        ") ELSE 1 END"
        " BEGIN SELECT RAISE(ABORT, 'retained ordinary control history is immutable'); END"
    )


def _require(condition: bool) -> None:
    if not condition:
        raise TaskError("ordinary control adoption lacks its original keyed owner evidence")


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def retain_control(
    previous: dict[str, Any] | None, incoming: dict[str, Any],
    reservation: dict[str, Any], *, resumed: bool,
) -> None:
    """Validate the transition and retain predecessor evidence in the same write."""
    prior = previous.get(HISTORY) if previous is not None else None
    boundary = incoming.get(ADOPTION)
    if previous is None:
        _require(HISTORY not in incoming and ADOPTION not in incoming)
        return
    before, after = identity(previous), identity(incoming)
    if before == after:
        if prior is not None:
            _require(
                isinstance(prior, dict)
                and incoming.get(HISTORY, prior) == prior
                and incoming.get(ADOPTION, previous.get(ADOPTION)) == previous.get(ADOPTION)
            )
            incoming[HISTORY] = copy.deepcopy(prior)
            incoming[ADOPTION] = copy.deepcopy(previous[ADOPTION])
        else:
            _require(HISTORY not in incoming and ADOPTION not in incoming)
        return
    _require(
        resumed and prior is None and HISTORY not in incoming
        and before.bridge != after.bridge
        and (before.native, before.worktree, before.project, before.path)
        == (after.native, after.worktree, after.project, after.path)
        and reservation.get("session_handle") == f"local-body:{before.bridge}"
        and _nonempty(reservation.get("creation_key"))
        and isinstance(boundary, dict)
    )
    if not isinstance(boundary, dict):
        raise TaskError("ordinary control adoption boundary is unavailable")
    _require(
        set(boundary) == {
            "version", "request", "predecessor_id", "session_id", "history", "prior_operations",
        }
        and type(boundary.get("version")) is int and boundary["version"] == 1
        and boundary.get("predecessor_id") == before.bridge
        and boundary.get("session_id") == after.bridge
        and boundary.get("history") == {
            "status": "unavailable", "predecessor_id": before.bridge,
            "event_epoch": after.bridge, "turn_epoch": after.bridge,
        }
    )
    operations = boundary.get("prior_operations")
    _require(
        isinstance(operations, list)
        and all(_nonempty(item) for item in operations)
        and len(set(operations)) == len(operations)
    )
    digest = hashlib.sha256(json.dumps([
        before.project, reservation["creation_key"],
    ]).encode()).hexdigest()
    request = boundary.get("request")
    _require(isinstance(request, dict))
    if not isinstance(request, dict):
        raise TaskError("ordinary control adoption request is unavailable")
    _require(
        set(request) == {
            "version", "operation_id", "journal_key", "native_session_id",
            "head_revision", "bridge_installation",
        }
        and type(request.get("version")) is int and request["version"] == 1
        and _nonempty(request.get("operation_id"))
        and request.get("journal_key") == digest
        and request.get("native_session_id") == before.native
        and type(request.get("head_revision")) is int and request["head_revision"] > 0
        and _nonempty(request.get("bridge_installation"))
    )
    requirement = writer_requirement(previous.get(REQUIREMENT))
    _require(
        incoming.get(REQUIREMENT) == requirement
        and reservation.get(REQUIREMENT) is not None
        and json.loads(reservation[REQUIREMENT]) == requirement
    )
    observed = incoming.get("worktree_writer_observation")
    _require(isinstance(observed, dict))
    if not isinstance(observed, dict):
        raise TaskError("ordinary control adoption writer observation is unavailable")
    instance, executing = observed.get("bridge_instance"), observed.get("executing_writer_identity")
    _require(
        observed.get("schema") == "copilot-extensions.creation-worktree-writer"
        and type(observed.get("version")) is int and observed["version"] == 1
        and observed.get("key_digest") == digest
        and observed.get("session_id") == after.bridge
        and observed.get("native_session_id") == after.native
        and observed.get("worktree_id") == after.worktree
        and observed.get("project") == after.project
        and observed.get("worktree_path") == after.path
        and observed.get("worktree_writer") == requirement
        and observed.get("writer_scope") == "executing-process"
        and valid_record_writer(observed.get("writer_contract"))
        and type(observed.get("retained_session_writes")) is int
        and observed["retained_session_writes"] == 1
        and observed.get("automatic_recovery_stopped") is False
        and isinstance(instance, dict) and isinstance(executing, dict)
    )
    if not isinstance(instance, dict) or not isinstance(executing, dict):
        raise TaskError("ordinary control adoption executing owner is unavailable")
    writer_requirement({
        "version": 1, "identity": executing, "creation_nonce": requirement["creation_nonce"],
    })
    _require(
        instance.get("installation_root") == request["bridge_installation"]
        and type(instance.get("pid")) is int and instance["pid"] > 0
        and type(instance.get("generation")) is int and instance["generation"] > 0
        and executing["installation_root"] == requirement["identity"]["installation_root"]
    )
    incoming[HISTORY] = {
        "version": 1, "predecessor": copy.deepcopy(previous),
        "adoption": copy.deepcopy(boundary),
    }
