"""Write-ahead cold-outage identity and once-only reconnect admission."""

from __future__ import annotations

import json
import logging
import asyncio
import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from single_instance_lease import OperationLock
from zdd import diagnostics, process_exit

from .models import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager

log = logging.getLogger("agent-bridge")
RECONNECT_PROMPT = "Resume. Host agent-bridge updated."


class ColdRecoveryStoppedError(ValueError):
    """A durable stop superseded automatic or previously admitted recovery."""


def _confirmed(row: dict[str, Any]) -> bool:
    if row["state"] != "reconnected" or not row["reconnect_evidence_json"]:
        return False
    evidence = json.loads(row["reconnect_evidence_json"])
    if (
        evidence.get("kind") != "provider-prompt-response"
        or evidence.get("native_session_id") != row["native_session_id"]
        or type(evidence.get("turn_index")) is not int
        or evidence["turn_index"] < 0
        or not evidence.get("stop_reason")
    ):
        raise ValueError("cold reconnect provider evidence changed")
    return True


@dataclass
class _Custody:
    manager: Any
    session_id: str
    lock: OperationLock
    task: Any
    active: bool = True


_custody: ContextVar[_Custody | None] = ContextVar("cold-recovery-custody", default=None)


@dataclass
class _RecoveryAdmission:
    manager: Any
    session_id: str
    task: Any
    stop_token: str | None
    automatic: bool
    active: bool = True
    manual_admitted: bool = False


_recovery_admission: ContextVar[_RecoveryAdmission | None] = ContextVar(
    "cold-recovery-admission", default=None,
)


@dataclass
class _AutomaticRecovery:
    manager: Any
    session_id: str
    task: Any
    active: bool = True


_automatic_recovery: ContextVar[_AutomaticRecovery | None] = ContextVar(
    "automatic-cold-recovery", default=None,
)


def _stop_token(manager: SessionManager, session_id: str) -> str | None:
    row = manager.db.get_session(session_id)
    if row is None:
        raise KeyError(session_id)
    return row["cold_recovery_stop_token"]


def _assert_recovery_current(manager: SessionManager, session_id: str) -> _RecoveryAdmission:
    held = _recovery_admission.get()
    if (
        held is None or not held.active or held.manager is not manager
        or held.session_id != session_id or held.task is not asyncio.current_task()
    ):
        raise ValueError("cold recovery requires its task-bound admission")
    if _stop_token(manager, session_id) != held.stop_token or (
        held.automatic and held.stop_token is not None
    ):
        raise ColdRecoveryStoppedError("Durable stop superseded cold recovery admission")
    return held


def manual_recovery_stop_token(manager: SessionManager, session_id: str) -> str | None:
    """Authorize only the stop token captured by this task's manual resume."""
    held = _recovery_admission.get()
    if (
        held is not None and held.active and held.manager is manager
        and held.session_id == session_id and held.task is asyncio.current_task()
        and not held.automatic
    ):
        return held.stop_token
    return None


@contextmanager
def recovery_admission(manager: SessionManager, session_id: str) -> Iterator[None]:
    """Serialize native recovery with drain and count until its admission settles."""
    held = _recovery_admission.get()
    if held is not None and held.active and held.task is asyncio.current_task():
        if held.manager is not manager or held.session_id != session_id:
            raise ValueError("native recovery admission belongs to another session")
        _assert_recovery_current(manager, session_id)
        yield
        return
    if manager.is_draining:
        from .session_manager import DaemonDrainingError

        raise DaemonDrainingError("session")
    origin = _automatic_recovery.get()
    if origin is not None and (
        not origin.active or origin.manager is not manager
        or origin.session_id != session_id or origin.task is not asyncio.current_task()
    ):
        raise ValueError("automatic cold recovery belongs to another task/session")
    stop_token = _stop_token(manager, session_id)
    if origin is not None and stop_token is not None:
        raise ColdRecoveryStoppedError("Automatic cold recovery is durably stopped")
    held = _RecoveryAdmission(
        manager, session_id, asyncio.current_task(), stop_token, origin is not None,
    )
    token = _recovery_admission.set(held)
    manager._active_acquisitions = getattr(manager, "_active_acquisitions", 0) + 1
    try:
        yield
        if held.manual_admitted:
            _assert_recovery_current(manager, session_id)
            if not manager.db.clear_cold_recovery_stop(session_id, held.stop_token):
                raise ColdRecoveryStoppedError("A newer stop superseded manual recovery")
            row = latest(manager.db, session_id)
            session = manager.get_session(session_id)
            if row is not None and _confirmed(row) and session is not None:
                session.background_recovery_enabled = manager.db.update_session_background_recovery(
                    session_id, True,
                )
    finally:
        held.active = False
        _recovery_admission.reset(token)
        manager._active_acquisitions -= 1


def admitted_recovery(method: Any) -> Any:
    @wraps(method)
    async def run(manager: SessionManager, session_id: str, *args: Any, **kwargs: Any) -> Any:
        session_id = manager._resolve_ref(session_id) or session_id
        with recovery_admission(manager, session_id):
            result = await method(manager, session_id, *args, **kwargs)
            held = _assert_recovery_current(manager, session_id)
            if not held.automatic:
                held.manual_admitted = True
            return result

    return run


def reconnect_admitted_before_drain(
    manager: SessionManager, session_id: str, delivery_id: str | None, *,
    prompt: str, no_resume: bool,
) -> bool:
    held = _recovery_admission.get()
    if (
        held is None or not held.active or held.manager is not manager
        or held.session_id != session_id or held.task is not asyncio.current_task()
    ):
        return False
    require_custody(manager, session_id)
    _assert_recovery_current(manager, session_id)
    row = latest(manager.db, session_id)
    session = manager.get_session(session_id)
    return (
        row is not None and row["state"] == "interrupted"
        and delivery_id == "cold-outage:" + row["outage_id"]
        and prompt == RECONNECT_PROMPT and no_resume is True
        and session is not None and session.acp_session_id == row["native_session_id"]
        and session.target.to_json() == row["target_json"]
    )


def require_custody(manager: SessionManager, session_id: str) -> _Custody:
    held = _custody.get()
    if (
        held is None
        or not held.active
        or held.manager is not manager
        or held.session_id != session_id
        or held.task is not asyncio.current_task()
    ):
        raise ValueError("cold native recovery requires its kernel operation custody")
    held.lock.assert_owned()
    return held


@contextmanager
def operation(manager: SessionManager, session_id: str) -> Iterator[None]:
    """Crash-releasing kernel exclusion; an inherited task cannot reuse custody."""
    existing = _custody.get()
    if existing is not None and existing.active and existing.task is asyncio.current_task():
        require_custody(manager, session_id)
        yield
        return
    directory = manager.db.db_path.absolute().parent / "cold-recovery-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    with OperationLock(directory / (key + ".lock")) as lock:
        held = _Custody(manager, session_id, lock, asyncio.current_task())
        token = _custody.set(held)
        try:
            yield
        finally:
            held.active = False
            _custody.reset(token)


def _owner(manager: SessionManager) -> dict[str, Any]:
    start = diagnostics.process_start_time(os.getpid())
    if start is None:
        raise ValueError("recovery owner native process identity is unavailable")
    return {"generation": manager._generation_id, "pid": os.getpid(), "start_time": start}


def _exited(execution: dict[str, Any]) -> bool:
    return all(
        process_exit.observe_original_exit(
            execution[name]["pid"],
            execution[name]["start_time"],
        )
        for name in ("host", "child")
    )


def _unconfirmed_reconnect(manager: SessionManager, row: dict[str, Any]) -> bool:
    return not _confirmed(row) and (
        row["state"] in {"reconnect_admitted", "reconnect_unknown"}
        or bool(
            manager.db.execute_read(
                "SELECT 1 FROM turns WHERE acquisition_delivery_id=?",
                ("cold-outage:" + row["outage_id"],),
            )
        )
    )


def _record(manager: SessionManager, session: Session, row: dict[str, Any]) -> dict | None:
    """Reconcile a registered replacement, including a registration/claim crash."""
    manager._host_index.refresh()
    rec = manager._host_index.get(session.session_id)
    original = json.loads(row["execution_json"])
    saved = json.loads(row["replacement_json"]) if row["replacement_json"] else None
    if rec is None:
        if row["recovery_phase"] in {"launching", "registered"}:
            if saved is None or not _exited(saved["execution"]):
                raise ValueError(
                    "replacement launch outcome is unknown; refusing another native launch"
                )
            if _unconfirmed_reconnect(manager, row):
                raise ValueError("reconnect delivery remains unknown after replacement exit")
        return None
    # Read birth evidence without changing claims or contacting the native child.
    from pathlib import Path

    path = Path(rec.state_file)
    if rec.boundary != "local" or path.is_symlink():
        raise ValueError("cold replacement is not an owned local host authority")
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        state.get("session_id") != session.session_id
        or not rec.nonce
        or state.get("nonce") != rec.nonce
    ):
        raise ValueError("cold replacement authority changed")
    execution = {
        name: {"pid": state[name + "_pid"], "start_time": state[name + "_process_start_time"]}
        for name in ("host", "child")
    }
    if rec.host_pid != execution["host"]["pid"] or rec.child_pid != execution["child"]["pid"]:
        raise ValueError("cold replacement process identity changed")
    if execution == original and _exited(original):
        manager._host_index.remove(session.session_id)
        return None
    previous = {
        "generation": row["recovery_generation"],
        "pid": row["recovery_pid"],
        "start_time": row["recovery_start_time"],
    }
    nonce = hashlib.sha256(rec.nonce.encode()).hexdigest()
    if saved is not None:
        if saved["execution"] != execution or saved["nonce_sha256"] != nonce:
            raise ValueError("registered cold replacement changed")
        owners = [previous, saved["record_owner"]]
    else:
        if row["recovery_phase"] != "launching":
            raise ValueError("host has no matching durable cold launch")
        owners = [previous]
    owner = next(
        (
            item
            for item in owners
            if item["generation"] == rec.owner_generation and item["pid"] == rec.owner_pid
        ),
        None,
    )
    if owner is None and (rec.owner_generation or rec.owner_pid or saved is None):
        raise ValueError("registered replacement has a different generation owner")
    child_exited = process_exit.observe_original_exit(
        execution["child"]["pid"],
        execution["child"]["start_time"],
    )
    if child_exited and _unconfirmed_reconnect(manager, row):
        raise ValueError("reconnect delivery remains unknown after replacement child exit")
    if _exited(execution):
        if _unconfirmed_reconnect(manager, row):
            raise ValueError("reconnect delivery remains unknown after replacement exit")
        manager._host_index.remove(session.session_id)
        return None
    # An exited child is not a live reference; retain its exact exit evidence
    # while the surviving owned host is settled under the lifecycle lock.
    for name, item in execution.items():
        if name == "child" and child_exited:
            continue
        try:
            with process_exit.open_process(item["pid"], item["start_time"]):
                pass
        except ProcessLookupError as exc:
            raise ValueError("Cold replacement exited or changed during admission; retain custody") from exc
    return {
        "execution": execution,
        "nonce_sha256": nonce,
        "record_owner": owner or saved["record_owner"],
        "child_exited": child_exited,
    }


async def settle_partial_replacement(manager: SessionManager, session: Session) -> None:
    """Settle only an exact orphan host; an unknown reconnect never reaches here."""
    require_custody(manager, session.session_id)
    if not session._lifecycle_lock.locked():
        raise ValueError("partial host settlement requires lifecycle custody")
    row = latest(manager.db, session.session_id)
    saved = json.loads(row["replacement_json"]) if row["replacement_json"] else None
    if not saved or not process_exit.observe_original_exit(
        saved["execution"]["child"]["pid"],
        saved["execution"]["child"]["start_time"],
    ):
        return
    if _unconfirmed_reconnect(manager, row):
        raise ValueError("reconnect delivery remains unknown after replacement child exit")
    if session.client is not None:
        session.client.mark_host_child_exited(-1)
    task = session._prompt_task
    if task is not None and not task.done():
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=5)
        if not done:
            raise TimeoutError("exited native prompt task did not settle")
        try:
            await task
        except asyncio.CancelledError:
            pass
    if session.client is not None:
        await session.client.shutdown()
        session.client = None
    from .session_cold_drain import retire_exited_child_host

    await retire_exited_child_host(manager, session, saved["execution"])
    claim(manager, session)


def claim(manager: SessionManager, session: Session) -> dict[str, Any]:
    """Persist generation/process ownership before any replacement launch."""
    require_custody(manager, session.session_id)
    row = latest(manager.db, session.session_id)
    if row is None:
        raise ValueError("cold outage disappeared")
    if (
        session.acp_session_id != row["native_session_id"]
        or session.target.to_json() != row["target_json"]
        or manager._generation_id == row["owner_generation"]
    ):
        raise ValueError(
            "cold recovery requires a successor and the original native/target identity"
        )
    durable = manager.db.execute_read(
        "SELECT acp_session_id,target_json FROM sessions WHERE id=?",
        (session.session_id,),
    )
    if (
        len(durable) != 1
        or durable[0]["acp_session_id"] != row["native_session_id"]
        or durable[0]["target_json"] != row["target_json"]
    ):
        raise ValueError("cold recovery durable native holder changed")
    owner = _owner(manager)
    previous = {
        "generation": row["recovery_generation"],
        "pid": row["recovery_pid"],
        "start_time": row["recovery_start_time"],
    }
    if previous["pid"] is not None and previous != owner:
        if not process_exit.observe_original_exit(previous["pid"], previous["start_time"]):
            raise ValueError("another live generation owns cold native recovery")
    if row["state"] == "reconnected" and not _confirmed(row):
        finish(
            manager.db,
            row["outage_id"],
            "reconnect_unknown",
            "reconnected disposition has no provider response evidence",
        )
        row = latest(manager.db, session.session_id)
    if row["state"] in {"prepared", "failed"}:
        if not _exited(json.loads(row["execution_json"])):
            raise ValueError("cold drain outcome is uncertain; original execution remains live")
        finish(
            manager.db,
            row["outage_id"],
            "interrupted" if row["native_session_id"] else "nonresumable",
        )
        manager.db.update_session_status(session.session_id, "stopped", time.time())
        for index in json.loads(row["interrupted_turns_json"]):
            manager.db.update_turn(
                session.session_id, index, stop_reason="interrupted", completed_at=time.time()
            )
        row = latest(manager.db, session.session_id)
    if row["state"] not in {
        "interrupted",
        "reconnect_admitted",
        "reconnect_unknown",
        "reconnected",
    }:
        raise ValueError(f"cold outage recovery is fenced ({row['state']})")
    if not row["native_session_id"] or not _exited(json.loads(row["execution_json"])):
        raise ValueError("cold recovery requires confirmed original host and child exit")
    replacement = _record(manager, session, row)
    phase = "registered" if replacement else "reserved"
    manager.db.execute_write(
        "UPDATE cold_outages SET recovery_generation=?,recovery_pid=?,recovery_start_time=?,"
        "recovery_phase=?,replacement_json=? WHERE outage_id=?",
        (
            owner["generation"],
            owner["pid"],
            owner["start_time"],
            phase,
            json.dumps(replacement) if replacement else None,
            row["outage_id"],
        ),
    )
    if replacement:
        prior = replacement["record_owner"]
        manager._host_index.claim(
            session.session_id,
            generation=owner["generation"],
            owner_pid=owner["pid"],
            pid_alive=lambda pid: not process_exit.observe_original_exit(pid, prior["start_time"]),
        )
    return latest(manager.db, session.session_id)


def launching(manager: SessionManager, session: Session) -> None:
    require_custody(manager, session.session_id)
    _assert_recovery_current(manager, session.session_id)
    row = latest(manager.db, session.session_id)
    if row["recovery_phase"] != "reserved":
        raise ValueError("registered/uncertain replacement must be reconciled, not relaunched")
    manager.db.execute_write(
        "UPDATE cold_outages SET recovery_phase='launching' WHERE outage_id=?",
        (row["outage_id"],),
    )


def registered(manager: SessionManager, session: Session) -> None:
    require_custody(manager, session.session_id)
    row = claim(manager, session)
    if row["recovery_phase"] != "registered":
        raise ValueError("native launch did not register its replacement host")


def revalidate_launch(manager: SessionManager, session_id: str) -> None:
    """Recheck admission after preparation without clearing its durable fence."""
    require_custody(manager, session_id)
    _assert_recovery_current(manager, session_id)
    row = latest(manager.db, session_id)
    if row is None or row["recovery_phase"] != "launching":
        raise ValueError("native launch requires its retained launching disposition")


def guarded_resume(method: Any) -> Any:
    @wraps(method)
    async def run(manager: SessionManager, session_id: str, *args: Any, **kwargs: Any) -> Any:
        session_id = manager._resolve_ref(session_id) or session_id
        row = latest(manager.db, session_id)
        if row is None:
            return await method(manager, session_id, *args, **kwargs)
        session = manager.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        _assert_recovery_current(manager, session_id)
        with operation(manager, session_id):
            row = claim(manager, session)
            partial = bool(
                row["replacement_json"] and json.loads(row["replacement_json"]).get("child_exited")
            )
            if session.client is not None and session.client.is_running and not partial:
                if row["recovery_phase"] != "registered":
                    raise ValueError("live client has no registered cold replacement")
                if row["state"] != "reconnected":
                    await reconnect(manager, session, row)
                return session
            session.status = SessionStatus.STOPPED
            session.turn_count = len(manager.db.get_turns(session_id))
            from .events import EventLog

            session.event_log = EventLog.from_db(
                manager.db,
                session_id,
                acp_session_id=session.acp_session_id,
                worktree_id=session.target.worktree_id,
            )
            return await method(manager, session_id, *args, **kwargs)

    return run


def available(db: Any) -> bool:
    """An adopted legacy read-only schema can legitimately predate this ledger."""
    return bool(
        db.execute_read(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cold_outages'",
        )
    )


def latest(db: Any, session_id: str) -> dict[str, Any] | None:
    if not available(db):
        return None
    rows = db.execute_read(
        "SELECT * FROM cold_outages WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
        (session_id,),
    )
    return dict(rows[0]) if rows else None


def unsettled_launches(db: Any) -> list[str]:
    """Retain every launching journal row, including orphaned historical rows."""
    if not available(db):
        return []
    return [
        row["session_id"]
        for row in db.execute_read(
            "SELECT session_id FROM cold_outages WHERE recovery_phase='launching' ORDER BY rowid"
        )
    ]


def prepare(manager: SessionManager, session: Session, identities: dict[str, Any]) -> str:
    """Commit native/target identity and interrupted state before any signal."""
    require_custody(manager, session.session_id)
    for identity in identities.values():
        with process_exit.open_process(identity["pid"], identity["start_time"]):
            pass
    previous = latest(manager.db, session.session_id)
    if previous is not None and not _confirmed(previous):
        raise ValueError("an unresolved cold outage already fences this session")
    outage = uuid.uuid4().hex
    now = time.time()
    db = manager.db
    if db.read_only:
        raise PermissionError("cold drain requires the writable database owner")
    with db._write_lock:
        conn = db._get_conn()
        try:
            conn.execute(
                "INSERT INTO cold_outages "
                "(outage_id,session_id,native_session_id,target_json,previous_status,"
                "owner_generation,execution_json,interrupted_turns_json,state,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,'prepared',?)",
                (
                    outage,
                    session.session_id,
                    session.acp_session_id,
                    session.target.to_json(),
                    session.status.value,
                    manager._generation_id,
                    json.dumps(identities),
                    json.dumps(
                        [
                            row["turn_index"]
                            for row in conn.execute(
                                "SELECT turn_index FROM turns WHERE session_id=? AND completed_at IS NULL",
                                (session.session_id,),
                            )
                        ]
                    ),
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET background_recovery_enabled=0,updated_at=? WHERE id=?",
                (now, session.session_id),
            )
            conn.execute(
                "UPDATE turns SET stop_reason='interrupted',completed_at=? "
                "WHERE session_id=? AND completed_at IS NULL",
                (now, session.session_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    session.background_recovery_enabled = False
    return outage


def finish(db: Any, outage_id: str, state: str, error: str | None = None) -> None:
    db.execute_write(
        "UPDATE cold_outages SET state=?,error=? WHERE outage_id=?",
        (state, error, outage_id),
    )


def assert_prompt_allowed(db: Any, session_id: str, delivery_id: str | None) -> None:
    row = latest(db, session_id)
    if (
        row is not None
        and not _confirmed(row)
        and (row["state"] != "interrupted" or delivery_id != "cold-outage:" + row["outage_id"])
    ):
        raise ValueError("cold outage fences new prompts until same-native reconnect")


def resume_policy(manager: SessionManager, session: Session) -> dict[str, Any] | None:
    """Require recovery custody and the original native identity before resume."""
    row = latest(manager.db, session.session_id)
    if row is None:
        return None
    require_custody(manager, session.session_id)
    _assert_recovery_current(manager, session.session_id)
    if row["state"] not in {
        "interrupted",
        "reconnect_admitted",
        "reconnect_unknown",
        "reconnected",
    }:
        raise ValueError(f"cold outage recovery is fenced ({row['state']})")
    if (
        not row["native_session_id"]
        or session.acp_session_id != row["native_session_id"]
        or session.target.to_json() != row["target_json"]
        or manager._generation_id == row["owner_generation"]
    ):
        raise ValueError(
            "cold recovery requires a successor and the original native/target identity"
        )
    if row["state"] != "reconnected":
        execution = json.loads(row["execution_json"])
        for name in ("host", "child"):
            identity = execution[name]
            if not process_exit.observe_original_exit(identity["pid"], identity["start_time"]):
                raise ValueError("cold recovery requires confirmed exit of the original execution")
    return row


async def reconnect(manager: SessionManager, session: Session, row: dict[str, Any]) -> None:
    """Admit one new reconnect turn, never replay an interrupted tool/request."""
    delivery = "cold-outage:" + row["outage_id"]
    require_custody(manager, session.session_id)
    if latest(manager.db, session.session_id)["recovery_phase"] != "registered":
        raise ValueError("reconnect requires a registered owned replacement")
    async with session._turn_start_lock:
        _assert_recovery_current(manager, session.session_id)
        if session.acp_session_id != row["native_session_id"]:
            raise ValueError("cold reconnect native identity changed")
        saved = manager.db.execute_read(
            "SELECT session_id,prompt,turn_index FROM turns WHERE acquisition_delivery_id=?",
            (delivery,),
        )
        if saved:
            if (
                saved[0]["session_id"] != session.session_id
                or saved[0]["prompt"] != RECONNECT_PROMPT
            ):
                raise ValueError("cold reconnect delivery identity conflict")
            current = latest(manager.db, session.session_id)
            if current["state"] == "reconnected" and current["reconnect_evidence_json"]:
                evidence = json.loads(current["reconnect_evidence_json"])
                if (
                    evidence.get("kind") == "provider-prompt-response"
                    and evidence.get("native_session_id") == session.acp_session_id
                    and evidence.get("turn_index") == saved[0]["turn_index"]
                    and evidence.get("stop_reason")
                ):
                    return
                raise ValueError("cold reconnect provider evidence changed")
            manager.db.execute_write(
                "UPDATE cold_outages SET state='reconnect_unknown',"
                "error='retained admission does not prove provider delivery' "
                "WHERE outage_id=? AND state!='reconnected'",
                (row["outage_id"],),
            )
            manager._set_background_recovery_enabled(session, False)
            raise ValueError("cold reconnect admission retained; provider delivery is unknown")
        else:
            try:
                await manager._submit_prompt_locked(
                    session.session_id,
                    RECONNECT_PROMPT,
                    no_resume=True,
                    delivery_id=delivery,
                )
            except BaseException:
                manager.db.execute_write(
                    "UPDATE cold_outages SET state='reconnect_unknown',"
                    "error='reconnect submission outcome is unknown' "
                    "WHERE outage_id=? AND state!='reconnected'",
                    (row["outage_id"],),
                )
                if not _confirmed(latest(manager.db, session.session_id)):
                    manager._set_background_recovery_enabled(session, False)
                raise
        manager.db.execute_write(
            "UPDATE cold_outages SET state='reconnect_admitted' "
            "WHERE outage_id=? AND state='interrupted'",
            (row["outage_id"],),
        )
        if not _confirmed(latest(manager.db, session.session_id)):
            manager._set_background_recovery_enabled(session, False)
    log.info(
        "Cold outage %s reconnect admitted; provider delivery is not yet confirmed",
        row["outage_id"],
    )


def provider_result(
    manager: SessionManager, session: Session, turn_index: int, result: dict[str, Any] | None
) -> None:
    """Only the actual prompt RPC response confirms delivery, not a turn row."""
    if not available(manager.db):
        return
    rows = manager.db.execute_read(
        "SELECT c.* FROM cold_outages c JOIN turns t "
        "ON t.acquisition_delivery_id='cold-outage:'||c.outage_id "
        "WHERE t.session_id=? AND t.turn_index=?",
        (session.session_id, turn_index),
    )
    if not rows:
        return
    row = rows[0]
    if result is None and _confirmed(dict(row)):
        return
    if (
        row["native_session_id"] != session.acp_session_id
        or row["recovery_generation"] != manager._generation_id
        or row["recovery_pid"] != os.getpid()
        or row["recovery_start_time"] != diagnostics.process_start_time(os.getpid())
    ):
        raise ValueError("cold reconnect provider result belongs to another recovery owner")
    confirmed = bool(result and result.get("stop_reason") and not result.get("error"))
    evidence = (
        json.dumps(
            {
                "kind": "provider-prompt-response",
                "native_session_id": session.acp_session_id,
                "turn_index": turn_index,
                "stop_reason": result["stop_reason"],
            }
        )
        if confirmed
        else None
    )
    manager.db.execute_write(
        "UPDATE cold_outages SET state=?,reconnect_evidence_json=?,error=? WHERE outage_id=?",
        (
            "reconnected" if confirmed else "reconnect_unknown",
            evidence,
            None if confirmed else "provider delivery was not confirmed",
            row["outage_id"],
        ),
    )
    if confirmed:
        session.background_recovery_enabled = manager.db.update_session_background_recovery(
            session.session_id, True,
        )


async def recover(
    manager: SessionManager,
    *,
    confirmed_only: bool = False,
    automatic: bool = False,
) -> int:
    """Resume only confirmed interrupted native conversations, on the successor."""
    if not available(manager.db):
        log.info("Legacy database has no cold-outage ledger; no cold recovery was attempted")
        return 0
    rows = manager.db.execute_read(
        "SELECT c.* FROM cold_outages c WHERE c.rowid="
        "(SELECT MAX(p.rowid) FROM cold_outages p WHERE p.session_id=c.session_id) ORDER BY c.rowid",
    )
    count = 0
    for row in rows:
        if manager.is_draining:
            break
        if automatic and row["state"] in {"reconnect_admitted", "reconnect_unknown"}:
            continue
        if confirmed_only and not _confirmed(row):
            continue
        session = manager.get_session(row["session_id"])
        if session is None:
            log.error("Cold recovery has no retained session %s", row["session_id"])
            continue
        if _stop_token(manager, session.session_id) is not None:
            continue
        if manager._generation_id == latest(manager.db, session.session_id)["owner_generation"]:
            continue
        if row["state"] == "nonresumable" or (
            _confirmed(row) and not session.background_recovery_enabled
        ):
            continue
        if _confirmed(row) and session.client is not None and session.client.is_running:
            continue
        origin = _AutomaticRecovery(manager, session.session_id, asyncio.current_task())
        token = _automatic_recovery.set(origin)
        try:
            await manager.resume_session(session.session_id, drain=False, allow_recreate=False)
        except (OSError, RuntimeError, ValueError, KeyError):
            log.exception("Same-native cold recovery refused for %s", session.session_id)
            continue
        finally:
            origin.active = False
            _automatic_recovery.reset(token)
        count += 1
    return count
