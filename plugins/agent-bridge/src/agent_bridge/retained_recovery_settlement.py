"""Settle affirmative pre-native failures without retrying a retained operation."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import json
import time
from typing import Any

from . import retained_session_recovery as recovery
from .acquisition_executor import executor
from .creation_worktree_writer import JOURNAL_KEY, _binding, _read, retained
from .models import RetainedRecoveryRequest
from .retained_metadata_lineage import adoption
from .session_status import SessionStatus

START_EVENT = "retained_recovery_started"
_MAX_EVENTS = 1024


def _failure_proven(
    row: dict[str, Any], session_id: str, request: RetainedRecoveryRequest,
    events: list[dict[str, Any]],
) -> bool:
    if (
        not events or any(
            type(item.get("event_id")) is not int or not isinstance(item.get("data"), dict)
            for item in events
        ) or [item["event_id"] for item in events] != list(range(1, len(events) + 1))
    ):
        return False
    markers = [
        (index, item) for index, item in enumerate(events)
        if item["event_type"] == START_EVENT and isinstance(item["data"].get("request"), dict)
        and item["data"]["request"].get("operation_id") == request.operation_id
    ]
    if markers:
        if (
            len(markers) != 1 or type(markers[0][1]["data"].get("version")) is not int
            or markers[0][1]["data"] != {"version": 1, "request": request.model_dump()}
        ):
            return False
        events = events[markers[0][0] + 1:]
    else:
        # Older writers did not tag progress with the operation ID. Only a fresh
        # adopted epoch with one operation can bind that complete failure history.
        boundary = adoption(row)
        if (
            boundary is None or boundary["session_id"] != session_id
            or boundary["request"] != request.model_dump() or boundary["prior_operations"]
            or list(recovery._operations(row)) != [request.operation_id]
        ):
            return False
    if any(item["event_type"] not in {
        "startup_progress", "connect_checkpoint", "host_child_exit",
    } for item in events):
        return False
    progress = [
        (index, item["data"].get("phase"), item["data"].get("state"))
        for index, item in enumerate(events) if item["event_type"] == "startup_progress"
    ]
    if (
        not progress or progress[0] != (0, "resume", "started")
        or progress[-1] != (len(events) - 1, "resume", "failed")
        or [(phase, state) for _, phase, state in progress if phase == "resume"]
        != [("resume", "started"), ("resume", "failed")]
        or any(phase in {"native-session-load", "native-session-new", "ready"}
               for _, phase, _ in progress)
    ):
        return False
    initialize = [(index, state) for index, phase, state in progress if phase == "acp-initialize"
                  and state != "waiting"]
    failures = [
        index for index, item in enumerate(events)
        if item["event_type"] == "connect_checkpoint"
        and item["data"].get("stage_name") == "LAUNCH_ACP"
        and item["data"].get("status") == "failed"
        and item["data"].get("retryable") is False
    ]
    exits = [
        index for index, item in enumerate(events)
        if item["event_type"] == "host_child_exit"
        and type(item["data"].get("exit_code")) is int
    ]
    return (
        len(initialize) == 2 and initialize[0][1] == "started" and initialize[1][1] == "failed"
        and len(exits) == 1 and initialize[0][0] < exits[0] < initialize[1][0]
        and len(failures) == 1 and initialize[1][0] < failures[0] < len(events) - 1
    )


def _events(manager: Any, session_id: str) -> list[dict[str, Any]]:
    count = manager.db.get_max_event_id(session_id)
    if not 0 < count <= _MAX_EVENTS:
        return []
    events = manager.db.get_events_range(session_id, 1, count)
    return events if len(events) == count else []


def _historical_predecessor(
    manager: Any, row: dict[str, Any], session_id: str, other: dict[str, Any],
    hosts: Any,
) -> bool:
    from .retained_metadata_lineage import native_for
    from zdd.process_exit import observe_original_exit

    try:
        handoffs = json.loads(row["creation_handoffs_json"] or "{}")
        cursor, seen = session_id, {session_id}
        while cursor != other["id"]:
            previous = [
                predecessor for predecessor, item in handoffs.items()
                if item["successor_id"] == cursor and item["stage"] == "completed"
            ]
            if len(previous) != 1 or previous[0] in seen:
                return False
            cursor = previous[0]
            seen.add(cursor)
        target = json.loads(other["target_json"])
        scope, _writer = _binding(row)
        if (
            other["status"] != "stopped" or other["runtime_custody_json"] is not None
            or other["acp_session_id"] != native_for(row, cursor)
            or target.get("type") != "local" or target.get("cwd") != scope.worktree_path
            or target.get("project") != scope.project or target.get("worktree_id") != scope.worktree_id
            or target.get("venue", {}).get(JOURNAL_KEY) != row["key_digest"]
        ):
            return False
        managed = manager.get_session(cursor)
        if managed is not None and (
            managed.session_id != cursor or managed.status != SessionStatus.STOPPED
            or managed.client is not None or managed._native_turn_transport is not None
            or managed._handoff_in_progress or managed.acp_session_id != other["acp_session_id"]
            or json.loads(managed.target.to_json()) != target
            or managed._prompt_task is not None and not managed._prompt_task.done()
        ):
            return False
        for host in hosts:
            if host.session_id == cursor and (
                host.boundary != "local" or any(
                    not isinstance(host.extra.get(f"{name}_process_start"), str)
                    or not host.extra[f"{name}_process_start"]
                    or not observe_original_exit(
                        getattr(host, f"{name}_pid"), host.extra[f"{name}_process_start"],
                    ) for name in ("host", "child")
                )
            ):
                return False
        return True
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


async def inspect(
    manager: Any, session_id: str, operation_id: str, ready: Callable[[], bool],
) -> dict[str, Any]:
    """Reconcile the original no-load failure; never launch, send or accept work."""
    outcome = recovery.inspect(manager, session_id, operation_id)
    if outcome["state"] != "unknown":
        return outcome
    session = manager.get_session(session_id)
    row = _read(manager.db, (session.target.venue or {}).get(JOURNAL_KEY))
    if row is None:
        raise recovery._refuse("Original native recovery journal disappeared")
    operation = recovery._operations(row)[operation_id]
    request = RetainedRecoveryRequest.model_validate(operation["request"])
    events = _events(manager, session_id)
    if not _failure_proven(row, session_id, request, events):
        return outcome
    scope, _writer = _binding(row)
    with executor(manager.db, f"retained-recovery:{scope.worktree_id}"), executor(
        manager.db, scope.worktree_id,
    ):
        from .acquisition_authority import worktree_command
        from .acquisition_launch_owner import session_association
        from .acquisition_worktree_writer import current
        from .cold_outages import latest
        from .creation_writer_inspection import inspect_writer
        from zdd.process_exit import observe_original_exit

        target = session.target.to_json()
        client = session.client
        owner: Any = getattr(session, "_retained_recovery_owner", None)
        if client is not None and (
            not isinstance(owner, recovery.RetainedRecovery) or owner.session is not session
            or owner.request != request or owner.active or owner.native_rpc_started
            or client.is_running or getattr(client, "_pending_permission_future", None) is not None
            or getattr(client, "_pending_elicitations", {})
        ):
            raise recovery._refuse("Pre-native settlement retains an unqualified transport")

        def require_stopped() -> None:
            saved = manager.db.get_session(session_id)
            if (
                not ready() or manager.is_draining or saved is None
                or manager.get_session(session_id) is not session
                or session.target.to_json() != target
                or session.acp_session_id != request.native_session_id
                or session.status != SessionStatus.STOPPED or saved["status"] != "stopped"
                or session.client is not client or session._native_turn_transport is not None
                or session._handoff_in_progress or saved["cold_recovery_stop_token"] is not None
                or latest(manager.db, session_id) is not None
                or session_association(manager, session) is not None
            ):
                raise recovery._refuse("Pre-native settlement requires its stopped original owner")

        require_stopped()
        observed = await inspect_writer(manager, session, ready)
        if observed["bridge_instance"]["installation_root"] != request.bridge_installation:
            raise recovery._refuse("Pre-native settlement changed its original Bridge installation")
        async with retained(manager.db, row):
            writer = current()
            if writer is None:
                raise recovery._refuse("Pre-native settlement lost its compatible keyed writer")
            head = await partial(worktree_command, owner_prefix=writer.prefix)(
                ["head-session", "--worktree-id", scope.worktree_id, "--json"], scope.project,
            )
        if (
            head.get("tracked") is not True or head.get("state") != "active"
            or head.get("head_session") != request.native_session_id
            or type(head.get("head_revision")) is not int
            or head["head_revision"] != request.head_revision or head.get("pending_handoffs")
        ):
            raise recovery._refuse("Pre-native settlement changed its authoritative native head")
        require_stopped()
        index = manager._host_index
        inventory = index.locked_inventory() if index is not None else None
        hosts = [item for item in inventory.records if item.session_id == session_id] if inventory else []
        if inventory is None or not inventory.storage_complete or len(hosts) != 1:
            raise recovery._refuse("Pre-native settlement requires the original retained Host record")
        host = hosts[0]
        if host.boundary != "local" or any(
            not isinstance(host.extra.get(f"{name}_process_start"), str)
            or not host.extra[f"{name}_process_start"]
            or not observe_original_exit(
                getattr(host, f"{name}_pid"), host.extra[f"{name}_process_start"],
            ) for name in ("host", "child")
        ):
            raise recovery._refuse("Pre-native settlement retains live or unknown Host custody")
        expected_gate = {
            "worktree_id": scope.worktree_id, "delivery_id": f"retained-recovery:{operation_id}",
            "mode": "cli", "session_id": session_id,
            "native_session_id": request.native_session_id, "driver": "retained-recovery",
        }
        gates = manager.db.execute_read(
            "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (scope.worktree_id,),
        )
        if len(gates) != 1 or dict(gates[0]) != expected_gate:
            raise recovery._refuse("Pre-native settlement changed its original launch gate")
        if client is not None:
            if owner.host_record != host or owner.streams is None or owner.socket is None:
                raise recovery._refuse("Pre-native settlement lost its original transport evidence")
            await owner.streams.aclose()
            await owner.socket.close()
            require_stopped()
            if (
                client.is_running or getattr(client, "_pending_permission_future", None) is not None
                or getattr(client, "_pending_elicitations", {})
            ):
                raise recovery._refuse("Pre-native transport changed during detach")
        index.assert_inventory_current(inventory)
        if _events(manager, session_id) != events:
            raise recovery._refuse("Pre-native failure history changed during settlement")
        with manager.db._runtime_transaction(write=True, full_sync=True) as conn:
            saved = conn.execute(
                "SELECT * FROM workspace_embodiments WHERE key_digest=?", (request.journal_key,),
            ).fetchone()
            gate = conn.execute(
                "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (scope.worktree_id,),
            ).fetchone()
            collisions = conn.execute(
                "SELECT * FROM sessions WHERE id<>? AND "
                "(acp_session_id=? OR json_extract(target_json,'$.worktree_id')=?)",
                (session_id, request.native_session_id, scope.worktree_id),
            ).fetchall()
            if (
                saved is None or dict(saved) != row or gate is None or dict(gate) != expected_gate
                or any(not _historical_predecessor(
                    manager, row, session_id, dict(other), inventory.records,
                ) for other in collisions)
                or conn.execute(
                "SELECT 1 FROM live_sessions WHERE worktree_id=? AND status='live' AND updated_at>=?",
                (scope.worktree_id, time.time() - 120),
                ).fetchone()
            ):
                raise recovery._refuse("Original pre-native operation or workspace custody changed")
            operations = recovery._operations(row)
            operations[operation_id] = {**operation, "state": "not-started"}
            conn.execute(
                "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
                (json.dumps(operations, sort_keys=True), request.journal_key),
            )
            conn.execute(
                "DELETE FROM acquisition_launch_gates WHERE worktree_id=? AND delivery_id=?",
                (scope.worktree_id, expected_gate["delivery_id"]),
            )
        if client is not None:
            session.client = None
    return recovery.inspect(manager, session_id, operation_id)
