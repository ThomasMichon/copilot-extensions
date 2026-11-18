"""Complete an original accepted load without invoking another native operation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
import json
import os
import time
from typing import Any, cast

from . import retained_postload_evidence as evidence, retained_session_recovery as recovery
from .retained_load_acceptance import host_binding
from .acquisition_executor import executor
from .creation_worktree_writer import _binding, _read, retained
from .models import RetainedRecoveryRequest
from .session_status import SessionStatus


async def complete(
    manager: Any, session: Any, row: dict[str, Any], request: RetainedRecoveryRequest,
    events: list[dict[str, Any]], ready: Callable[[], bool],
) -> dict[str, Any]:
    """Reconcile through the retained Host; leave unknown custody held on failure."""
    from .acquisition_authority import worktree_command
    from .acquisition_launch_owner import session_association
    from .acquisition_worktree_writer import current, registration_pending, select_writer
    from .cold_outages import latest
    from .creation_writer_inspection import inspect_writer
    from .retained_recovery_settlement import _events, _historical_predecessor
    from agent_remote_driver.session_host.inspection import probe
    from . import worktree_lineage

    scope, _writer = _binding(row)
    if [
        key for key, operation in recovery._operations(row).items()
        if operation["state"] not in {"complete", "not-started"}
    ] != [request.operation_id]:
        raise recovery._refuse("Original accepted load has another unresolved native operation")
    target = session.target.to_json()
    session_id = session.session_id
    index = manager._host_index
    generation = manager._generation_id
    expected_gate = {
        "worktree_id": scope.worktree_id, "delivery_id": f"retained-recovery:{request.operation_id}",
        "mode": "cli", "session_id": session_id,
        "native_session_id": request.native_session_id, "driver": "retained-recovery",
    }
    original_client = session.client
    attached = original_client
    expected_events = list(events)
    event_fault = False

    def require_owner() -> None:
        saved = manager.db.get_session(session_id)
        if (
            not ready() or manager.is_draining or saved is None
            or manager.get_session(session_id) is not session or session.target.to_json() != target
            or saved["target_json"] != target
            or session.acp_session_id != request.native_session_id
            or saved["acp_session_id"] != request.native_session_id
            or session.status != SessionStatus.STOPPED or saved["status"] != "stopped"
            or session.client is not attached or session.background_recovery_enabled
            or attached is not None and attached is not original_client and not attached.is_running
            or session._native_turn_transport is not None or session._handoff_in_progress
            or session._turn_start_lock.locked()
            or session._prompt_task is not None and not session._prompt_task.done()
            or saved["cold_recovery_stop_token"] is not None or latest(manager.db, session_id) is not None
            or session_association(manager, session) is not None
            or _read(manager.db, request.journal_key) != row
            or index is None or manager._host_index is not index
            or not generation or manager._generation_id != generation
            or session.event_log is None
            or event_fault or _events(manager, session_id) != expected_events
        ):
            raise recovery._refuse("Post-load completion requires its stopped original owner")
        gates = manager.db.execute_read(
            "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (scope.worktree_id,),
        )
        if len(gates) != 1 or dict(gates[0]) != expected_gate:
            raise recovery._refuse("Original accepted load lost its retained launch gate")

    async def require_head() -> None:
        writer = current()
        if writer is None:
            raise recovery._refuse("Post-load completion lost its qualified writer")
        head = await partial(worktree_command, owner_prefix=writer.prefix)(
            ["head-session", "--worktree-id", scope.worktree_id, "--json"], scope.project,
        )
        if (
            head.get("tracked") is not True or head.get("state") != "active"
            or head.get("head_session") != request.native_session_id
            or type(head.get("head_revision")) is not int
            or head["head_revision"] != request.head_revision or head.get("pending_handoffs")
        ):
            raise recovery._refuse("Original accepted load changed its authoritative native head")
        require_owner()

    def inventory() -> tuple[Any, Any]:
        require_owner()
        observed = index.locked_inventory()
        hosts = [host for host in observed.records if host.session_id == session_id]
        if not observed.storage_complete or len(hosts) != 1 or hosts[0].resume_on_reattach:
            raise recovery._refuse("Post-load completion requires its original retained Host")
        return observed, hosts[0]

    def require_exclusive(records: Any) -> None:
        collisions = manager.db.execute_read(
            "SELECT * FROM sessions WHERE id<>? AND "
            "(acp_session_id=? OR json_extract(target_json,'$.worktree_id')=?)",
            (session_id, request.native_session_id, scope.worktree_id),
        )
        if any(not _historical_predecessor(
            manager, row, session_id, dict(other), records,
        ) for other in collisions) or manager.db.execute_read(
            "SELECT 1 FROM live_sessions WHERE worktree_id=? AND status='live' "
            "AND updated_at>=? AND session_id<>?",
            (scope.worktree_id, time.time() - 120, request.native_session_id),
        ):
            raise recovery._refuse("Another native owner retains the original workspace")

    async def idle(host: Any) -> dict[str, Any]:
        observed = await probe("127.0.0.1", host.port, host.nonce, child_pid=host.child_pid)
        require_owner()
        snapshot = observed.get("snapshot", {})
        if (
            observed.get("state") != "responsive" or snapshot.get("host_pid") != host.host_pid
            or snapshot.get("child_pid") != host.child_pid or snapshot.get("child_alive") is not True
            or snapshot.get("turn_in_flight") is not False
        ):
            raise recovery._refuse("Original Host did not authenticate an idle surviving child")
        return cast(dict[str, Any], snapshot)

    with executor(manager.db, f"retained-recovery:{scope.worktree_id}"), executor(
        manager.db, scope.worktree_id,
    ):
        async with session._lifecycle_lock:
            require_owner()
            observation = await inspect_writer(manager, session, ready)
            if observation["bridge_instance"]["installation_root"] != request.bridge_installation:
                raise recovery._refuse("Post-load completion changed its original Bridge installation")
            writer, support = await select_writer(_writer)
            if (
                type(support.get("retained_hosted_activation")) is not int
                or support["retained_hosted_activation"] != 1
            ):
                raise recovery._refuse("Executing Worktrees owner lacks retained hosted ACP activation")
            async with retained(manager.db, row, executing_writer=writer):
                require_owner()
                await require_head()
                _snapshot, host = inventory()
                require_exclusive(_snapshot.records)
                activation = evidence.qualify(
                    events, host, request, scope.worktree_writer, scope.worktree_path,
                )
                before = await idle(host)
                if not manager._claim_host_record(host):
                    raise recovery._refuse("Another live frontend retains the original Host")
                claimed, owned = inventory()
                if (
                    host_binding(owned) != host_binding(host)
                    or owned.owner_generation != generation or owned.owner_pid != os.getpid()
                ):
                    raise recovery._refuse("Original Host claim was not confirmed")

                def require_claim() -> None:
                    fresh, active = inventory()
                    if active != owned or evidence.physical(active, request) != activation:
                        raise recovery._refuse("Original accepted Host or claim changed")
                    require_exclusive(fresh.records)
                    index.assert_inventory_current(fresh)

                try:
                    if original_client is not None:
                        evidence.original_frontend(
                            manager, session, original_client, owned, request, evidence._span(events, request),
                        )
                        require_claim()
                        await asyncio.wait_for(original_client.shutdown(), timeout=manager._timeouts.session_start)
                        require_claim()
                        session.client = attached = None
                    if not await asyncio.wait_for(manager._reattach_one(
                        owned, session, new_status=SessionStatus.STOPPED, retained_check=require_claim,
                    ), timeout=manager._timeouts.session_start):
                        raise recovery._refuse("Original accepted Host could not be reattached")
                    attached = session.client
                    require_claim()
                    if (
                        attached is None or attached.acp_session_id != request.native_session_id
                        or getattr(attached, "_pending_permission_future", None) is not None
                        or getattr(attached, "_pending_elicitations", {})
                    ):
                        raise recovery._refuse("Original accepted conversation did not reattach idle")
                    after = await idle(owned)
                    if after["host_generation"] != before["host_generation"]:
                        raise recovery._refuse("Original Host generation changed during reattachment")
                    from .retained_load_configuration import configuration_event, finish

                    def configuration_emitted(name: str, data: dict[str, Any]) -> None:
                        nonlocal event_fault
                        try:
                            # The client swallows callback failures. Keep the actual event
                            # in the journal even when it disqualifies this and later inspections.
                            manager._route_acp_event(session, name, data)
                            observed = _events(manager, session_id)
                            if not configuration_event(name, data):
                                raise recovery._refuse("Unexpected activity during original configuration")
                            if (
                                observed[:-1] != expected_events or len(observed) != len(expected_events) + 1
                                or observed[-1]["event_type"] != name or observed[-1]["data"] != data
                            ):
                                raise recovery._refuse("Concurrent activity changed original configuration history")
                            expected_events[:] = observed
                            require_claim()
                        except BaseException:
                            event_fault = True
                            raise

                    await asyncio.wait_for(finish(
                        attached, evidence._span(events, request), require_claim, configuration_emitted,
                    ), timeout=manager._timeouts.session_new)
                    if not worktree_lineage.register_session(
                        scope.worktree_id, request.native_session_id,
                        pid=activation["child"]["pid"], worktree_dir=scope.worktree_path,
                        hosted_activation=activation,
                    ):
                        raise recovery._refuse("Original accepted load registration remains unconfirmed")
                    await require_head()
                    await idle(owned)
                    require_claim()
                    if _events(manager, session_id) != expected_events:
                        raise recovery._refuse("Original accepted-load history changed during completion")
                    with manager.db._runtime_transaction(write=True, full_sync=True) as conn:
                        saved = conn.execute(
                            "SELECT * FROM workspace_embodiments WHERE key_digest=?", (request.journal_key,),
                        ).fetchone()
                        gate = conn.execute(
                            "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (scope.worktree_id,),
                        ).fetchone()
                        saved_session = conn.execute(
                            "SELECT * FROM sessions WHERE id=?", (session_id,),
                        ).fetchone()
                        collisions = conn.execute(
                            "SELECT * FROM sessions WHERE id<>? AND "
                            "(acp_session_id=? OR json_extract(target_json,'$.worktree_id')=?)",
                            (session_id, request.native_session_id, scope.worktree_id),
                        ).fetchall()
                        if (
                            saved is None or dict(saved) != row or gate is None or dict(gate) != expected_gate
                            or saved_session is None or saved_session["status"] != "stopped"
                            or saved_session["cold_recovery_stop_token"] is not None
                            or saved_session["acp_session_id"] != request.native_session_id
                            or saved_session["target_json"] != target
                            or any(not _historical_predecessor(
                                manager, row, session_id, dict(other), claimed.records,
                            ) for other in collisions)
                            or conn.execute(
                                "SELECT 1 FROM live_sessions WHERE worktree_id=? AND status='live' "
                                "AND updated_at>=? AND session_id<>?",
                                (scope.worktree_id, time.time() - 120, request.native_session_id),
                            ).fetchone()
                        ):
                            raise recovery._refuse("Original accepted operation or workspace custody changed")
                        operations = recovery._operations(row)
                        operations[request.operation_id] = {
                            "request": request.model_dump(), "state": "complete",
                        }
                        conn.execute(
                            "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
                            (json.dumps(operations, sort_keys=True), request.journal_key),
                        )
                        conn.execute(
                            "DELETE FROM acquisition_launch_gates WHERE worktree_id=? AND delivery_id=?",
                            (scope.worktree_id, expected_gate["delivery_id"]),
                        )
                        conn.execute(
                            "UPDATE sessions SET status='idle',updated_at=? WHERE id=?", (time.time(), session_id),
                        )
                    session.status = SessionStatus.IDLE
                except BaseException:
                    if attached is not None and attached is not original_client and session.client is attached:
                        await registration_pending(manager, session)
                    raise
    return recovery.inspect(manager, session_id, request.operation_id)
