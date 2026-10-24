"""Destructive, explicitly negotiated drain of this generation's local hosts."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from zdd import diagnostics, process_exit

from . import cold_outages
from .models import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager

log = logging.getLogger("agent-bridge")


def refuse_graceful(manager: SessionManager, timeout: float) -> dict[str, Any]:
    """An interrupted generation cannot subsequently attest graceful cutover."""
    log.warning("Graceful drain refused: this generation already selected destructive cold drain")
    return {
        "mode": "incompatible-contract-cold",
        "drained": False,
        "clean": False,
        "forced": True,
        "busy_sessions": manager.busy_sessions(),
        "live_host_count": manager.live_host_count,
        "timeout": timeout,
        "failures": [
            {"session_id": "", "reason": "generation already selected destructive cold drain"}
        ],
    }


@contextlib.asynccontextmanager
async def _lifecycle(session: Session, deadline: float) -> AsyncIterator[None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("cold drain lifecycle budget expired")
    await asyncio.wait_for(session._lifecycle_lock.acquire(), timeout=remaining)
    try:
        yield
    finally:
        session._lifecycle_lock.release()


def owned_execution(manager: SessionManager, session: Session, record: Any) -> dict[str, Any]:
    """Validate generation ownership and the host's launch-time birth evidence."""
    if (
        record is None
        or record.boundary != "local"
        or session.target.type != "local"
        or session.target.elevated
        or record.owner_generation != manager._generation_id
        or record.owner_pid != os.getpid()
        or record.session_id != session.session_id
    ):
        raise ValueError("execution is not a local host owned by this generation")
    path = Path(record.state_file)
    if path.is_symlink():
        raise ValueError("host authority must not be a symlink")
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        not record.nonce
        or state.get("nonce") != record.nonce
        or state.get("session_id") != session.session_id
        or state.get("host_pid") != record.host_pid
        or state.get("child_pid") != record.child_pid
    ):
        raise ValueError("host launch authority does not match its owned record")
    identities = {}
    for name in ("host", "child"):
        token = state.get(name + "_process_start_time")
        if not isinstance(token, str) or not token:
            raise ValueError("legacy host lacks cross-platform process birth evidence")
        identities[name] = {"pid": state[name + "_pid"], "start_time": token}
    return identities


async def _terminate(
    manager: SessionManager,
    session: Session,
    identities: dict[str, Any],
    timeout: float,
) -> None:
    if timeout <= 0:
        raise TimeoutError("cold termination budget expired")
    deadline = time.monotonic() + timeout
    with contextlib.ExitStack() as stack:
        refs = {
            name: stack.enter_context(process_exit.open_process(item["pid"], item["start_time"]))
            for name, item in identities.items()
        }
        host_client = manager._session_host_client(session)
        if host_client is None:
            raise ValueError("no owned authenticated host connection for termination")
        await asyncio.wait_for(host_client.terminate(), timeout=timeout)
        if not await asyncio.to_thread(
            refs["child"].wait, min(max(0, deadline - time.monotonic()), 30.0)
        ):
            raise TimeoutError("native child exit was not confirmed")
        result = await asyncio.to_thread(
            diagnostics.terminate_pid_if_identity,
            identities["host"]["pid"],
            identities["host"]["start_time"],
            exit_timeout=min(max(0, deadline - time.monotonic()), 30.0),
        )
        if result.get("exit_confirmed") is not True or not refs["host"].wait(0):
            raise RuntimeError("owned host exit was not confirmed")


async def _settle(manager: SessionManager, session: Session) -> None:
    task = session._prompt_task
    if task is not None and not task.done():
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=5.0)
        if not done:
            raise TimeoutError("frontend prompt task did not stop after native exit")
        try:
            await task
        except asyncio.CancelledError:
            pass
    if session.client is not None:
        session.client.mark_host_child_exited(-1)
        await session.client.shutdown()
        session.client = None
    session.status = SessionStatus.STOPPED
    now = time.time()
    manager.db.update_session_status(session.session_id, SessionStatus.STOPPED.value, now)
    outage = cold_outages.latest(manager.db, session.session_id)
    for index in json.loads(outage["interrupted_turns_json"]):
        manager.db.update_turn(
            session.session_id,
            index,
            stop_reason="interrupted",
            completed_at=now,
        )
    manager._host_index.remove(session.session_id)
    from .session_host.osutil import reap_zombie

    identities = json.loads(outage["execution_json"])
    await asyncio.to_thread(reap_zombie, identities["host"]["pid"])
    if session.event_log is not None:
        session.event_log.append(
            "session_state_changed",
            {
                "status": "stopped",
                "trigger": "incompatible-contract-cold",
                "native_session_id": session.acp_session_id,
            },
        )


async def retire_exited_child_host(
    manager: SessionManager,
    session: Session,
    execution: dict[str, Any],
) -> None:
    """Retire an identity-pinned owned host only after its exact child exited."""
    cold_outages.require_custody(manager, session.session_id)
    child, host = execution["child"], execution["host"]
    if not process_exit.observe_original_exit(child["pid"], child["start_time"]):
        raise ValueError("partial host child is still live")
    try:
        reference = process_exit.open_process(host["pid"], host["start_time"], allow_exited=True)
    except ProcessLookupError:
        if process_exit.observe_original_exit(host["pid"], host["start_time"]):
            return
        raise
    with reference:
        if reference.wait(0):
            return
        result = await asyncio.to_thread(
            diagnostics.terminate_pid_if_identity,
            host["pid"],
            host["start_time"],
            exit_timeout=5,
        )
        if result.get("exit_confirmed") is not True and not reference.wait(0):
            raise RuntimeError("partial owned host exit was not confirmed")
    from .session_host.osutil import reap_zombie

    await asyncio.to_thread(reap_zombie, host["pid"])


async def cold_drain(
    manager: SessionManager,
    *,
    timeout: float,
    poll: float,
    source: str,
    reason: str | None,
    exclude_session_id: str | None,
) -> dict[str, Any]:
    """Persist before killing; success requires actual owned execution exit."""
    if not cold_outages.available(manager.db):
        raise ValueError("legacy database has no cold-outage ledger; destructive drain refused")
    manager.set_draining(True, source=source, reason=reason)
    manager._cold_drain_entered = True
    manager._cancel_drain_watchdog()
    deadline = time.monotonic() + timeout
    while getattr(manager, "_active_acquisitions", 0) and time.monotonic() < deadline:
        await asyncio.sleep(min(poll, max(0, deadline - time.monotonic())))
    interrupted: list[str] = []
    nonresumable: list[str] = []
    preserved: list[str] = []
    failures: list[dict[str, str]] = []
    pending_launches = set(cold_outages.unsettled_launches(manager.db))
    if getattr(manager, "_active_acquisitions", 0):
        failures.append({"session_id": "", "reason": "in-flight acquisition remains fenced"})
    else:
        for session in list(manager._sessions.values()):
            if session.session_id in pending_launches:
                continue
            record = manager._host_index.get(session.session_id)
            if (
                session.client is None
                and session.status
                not in {
                    SessionStatus.RUNNING,
                    SessionStatus.STARTING,
                }
                and record is None
            ):
                continue
            if session.session_id == exclude_session_id or session.target.type == "remote-acp":
                preserved.append(session.session_id)
                continue
            from .privileged_plugins import CONTEXT_KEY, relay_agent

            if (
                session.target.elevated
                or relay_agent(session.target) is not None
                or (CONTEXT_KEY in (session.target.venue or {}))
            ):
                preserved.append(session.session_id)
                continue
            outage = None
            try:
                with cold_outages.operation(manager, session.session_id):
                    try:
                        async with _lifecycle(session, deadline):
                            manager._host_index.refresh()
                            record = manager._host_index.get(session.session_id)
                            identities = owned_execution(manager, session, record)
                            outage = cold_outages.prepare(manager, session, identities)
                            await _terminate(
                                manager, session, identities, deadline - time.monotonic()
                            )
                            await _settle(manager, session)
                            state = "interrupted" if session.acp_session_id else "nonresumable"
                            cold_outages.finish(manager.db, outage, state)
                            (interrupted if session.acp_session_id else nonresumable).append(
                                session.session_id
                            )
                    except BaseException as exc:
                        if outage is not None:
                            cold_outages.finish(manager.db, outage, "failed", str(exc))
                        raise
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                failures.append({"session_id": session.session_id, "reason": str(exc)})
                log.exception("Cold drain could not confirm termination of %s", session.session_id)
    manager._host_index.refresh()
    busy = manager.busy_sessions()
    hosts = manager.live_host_count
    active_clients = [
        session.session_id
        for session in manager._sessions.values()
        if session.client is not None and session.client.is_running
    ]
    for session_id in cold_outages.unsettled_launches(manager.db):
        failures.append(
            {
                "session_id": session_id,
                "reason": "native replacement launch outcome remains unresolved",
            }
        )
    if (
        not manager.is_draining
        or busy
        or hosts
        or active_clients
        or getattr(
            manager,
            "_active_acquisitions",
            0,
        )
    ):
        failures.append(
            {
                "session_id": "",
                "reason": "cold drain gate or execution census did not remain quiescent",
            }
        )
    result = {
        "mode": "incompatible-contract-cold",
        "drained": not failures and not preserved,
        "clean": False,
        "forced": True,
        "interrupted": interrupted,
        "nonresumable": nonresumable,
        "preserved": preserved,
        "failures": failures,
        "busy_sessions": busy,
        "live_host_count": hosts,
        "timeout": timeout,
    }
    log.warning(
        "Incompatible-contract cold drain: drained=%s interrupted=%d nonresumable=%d "
        "preserved=%d failures=%d",
        result["drained"],
        len(interrupted),
        len(nonresumable),
        len(preserved),
        len(failures),
    )
    return result
