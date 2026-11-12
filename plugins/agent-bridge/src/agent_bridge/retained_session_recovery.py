"""Once-only continuation of an existing keyed workspace, never allocation."""

from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
import asyncio
import json
import os
import time
from typing import Any

from .acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from .acquisition_executor import executor
from .creation_worktree_writer import JOURNAL_KEY, _binding, _read, retained
from .models import RetainedRecoveryRequest, SessionStatus

CAPABILITY = "retained_native_session_recovery"
ENVIRONMENT_KEY = "AGENT_WORKTREES_RETAINED_NATIVE_RESUME"


def _refuse(message: str) -> AcquisitionRefused:
    return AcquisitionRefused("retained_recovery_held", message)


def permission_held(db: Any, session: Any) -> bool:
    row = _read(db, (session.target.venue or {}).get(JOURNAL_KEY))
    return row is not None and row.get("creation_recoveries_json") is not None


def _operations(row: dict) -> dict:
    try:
        value = json.loads(row["creation_recoveries_json"] or "{}")
        if not isinstance(value, dict):
            raise ValueError("invalid journal")
        for operation_id, operation in value.items():
            if (
                not isinstance(operation, dict) or set(operation) != {"request", "state"}
                or not isinstance(operation["state"], str)
                or operation["state"] not in {"preparing", "invoking", "complete", "unknown", "not-started"}
                or RetainedRecoveryRequest.model_validate(operation["request"]).operation_id != operation_id
            ):
                raise ValueError("invalid retained operation")
        return value
    except (TypeError, ValueError, KeyError) as exc:
        raise _refuse("Original native recovery journal is unconfirmed") from exc


def inspect(manager: Any, session_id: str, operation_id: str) -> dict:
    session = manager.get_session(session_id)
    if session is None or session.session_id != session_id:
        raise _refuse("Native recovery requires the exact retained Bridge session")
    row = _read(manager.db, (session.target.venue or {}).get(JOURNAL_KEY))
    operation = _operations(row).get(operation_id) if row is not None else None
    if not isinstance(operation, dict) or set(operation) != {"request", "state"}:
        raise _refuse("Original native recovery operation is unavailable; do not replay")
    request = RetainedRecoveryRequest.model_validate(operation["request"])
    if (
        request.operation_id != operation_id or request.journal_key != row["key_digest"]
        or request.native_session_id != session.acp_session_id
        or operation["state"] not in {"preparing", "invoking", "complete", "unknown", "not-started"}
    ):
        raise _refuse("Original native recovery operation changed")
    return {
        "version": 1, "operation_id": operation_id,
        "state": operation["state"] if operation["state"] in {"complete", "not-started"} else "unknown",
        "journal_key": request.journal_key, "bridge_session_id": session_id,
        "native_session_id": request.native_session_id,
    }


@dataclass
class RetainedRecovery:
    manager: Any
    session: Any
    request: RetainedRecoveryRequest
    target_json: str
    task: Any
    native_started: bool = False
    socket: Any = None
    streams: Any = None
    active: bool = True
    host_process: Any = None
    host_record: Any = None
    native_rpc_started: bool = False

    @property
    def session_id(self) -> str:
        return self.session.session_id

    def require_current(self, *, check_task: bool = True) -> None:
        if (
            not self.active or self.task.done() or self.manager.is_draining
            or check_task and asyncio.current_task() is not self.task
            or self.manager.get_session(self.session.session_id) is not self.session
            or self.session.acp_session_id != self.request.native_session_id
            or self.manager.db.get_session(self.session.session_id)["cold_recovery_stop_token"] is not None
        ):
            raise _refuse("Original native recovery frontend or conversation changed")
        target = json.loads(self.session.target.to_json())
        original = json.loads(self.target_json)
        target.get("env", {}).pop(ENVIRONMENT_KEY, None)
        if "native_resume_id" in original:
            target["native_resume_id"] = original["native_resume_id"]
        else:
            target.pop("native_resume_id", None)
        if target != original or self.session.target.native_resume_id not in (
            None, self.request.native_session_id,
        ):
            raise _refuse("Original native recovery target changed")
        row = _read(self.manager.db, self.request.journal_key)
        operation = _operations(row).get(self.request.operation_id) if row is not None else None
        if operation != {"request": self.request.model_dump(), "state": "invoking"}:
            raise _refuse("Original native recovery operation is held")

    def child_context(self, child_argv: list[str], cwd: str | None) -> str:
        self.require_current(check_task=False)
        return json.dumps({
            "argv": child_argv, "cwd": os.path.abspath(cwd or os.getcwd()),
            "session_id": self.session_id,
        })

    def begin_host(self) -> None:
        self.require_current(check_task=False)
        if self.native_started:
            raise _refuse("Original retained recovery Host was already invoked")
        self.native_started = True

    def retain_process(self, process: Any, state_file: str, nonce: str) -> None:
        self.host_process = process

    def retain_host(self, host: Any) -> None:
        self.host_record = self.manager._register_host_launch(host, self.session_id)

    def admit_host_child(self, context: str, request: dict) -> None:
        self.require_current(check_task=False)
        expected = json.loads(context)
        if (
            self.host_process is None or self.host_process.poll() is not None
            or request["host_pid"] != self.host_process.pid or request["parent_pid"] != os.getpid()
            or any(request[key] != value for key, value in expected.items())
            or request["environment"].get("AGENT_BRIDGE_SESSION_ID") != self.session_id
        ):
            raise _refuse("Original retained recovery Host or child changed")

    def require_native(self, client: Any, session_id: str) -> None:
        self.require_current(check_task=False)
        if self.session.client is not client or session_id != self.request.native_session_id:
            raise _refuse("Original retained recovery native transport changed")

    def native_method(self, client: Any, session_id: str, method: Any) -> Any:
        async def invoke(**kwargs):
            self.require_native(client, session_id)
            if (
                self.native_rpc_started or kwargs.get("session_id") != session_id
                or kwargs.get("cwd") != self.session.target.cwd
            ):
                raise _refuse("Original retained recovery native request changed")
            self.native_rpc_started = True
            return await method(**kwargs)

        return invoke

    def environment(self, writer: WorktreeWriterRequirement) -> str:
        target = self.session.target
        return json.dumps({
            "version": 1, "operation_id": self.request.operation_id,
            "writer": {
                **writer.model_dump(), "worktree_id": target.worktree_id,
                "project": target.project, "worktree_path": target.cwd,
            },
            "head": {
                "session_id": self.request.native_session_id, "revision": self.request.head_revision,
            },
        }, sort_keys=True)


_current: ContextVar[RetainedRecovery | None] = ContextVar("retained-session-recovery", default=None)


@contextmanager
def _preparing(db: Any, request: RetainedRecoveryRequest, previous: dict):
    original = {"request": request.model_dump(), "state": "preparing"}
    previous[request.operation_id] = original
    with db._runtime_transaction(write=True, full_sync=True) as conn:
        conn.execute(
            "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
            (json.dumps(previous, sort_keys=True), request.journal_key),
        )
    try:
        yield
    except BaseException:
        with db._runtime_transaction(write=True, full_sync=True) as conn:
            row = conn.execute(
                "SELECT * FROM workspace_embodiments WHERE key_digest=?", (request.journal_key,),
            ).fetchone()
            saved = _operations(dict(row)) if row is not None else {}
            if saved.get(request.operation_id) == original:
                saved[request.operation_id] = {**original, "state": "not-started"}
                conn.execute(
                    "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
                    (json.dumps(saved, sort_keys=True), request.journal_key),
                )
        raise


def permits_gate(db: Any, session: Any, gate: Any) -> bool:
    owner = _current.get()
    if owner is None or owner.manager.db is not db or owner.session is not session:
        return False
    expected = (
        session.target.worktree_id, f"retained-recovery:{owner.request.operation_id}",
        "cli", session.session_id, owner.request.native_session_id, "retained-recovery",
    )
    if tuple(gate[key] for key in (
        "worktree_id", "delivery_id", "mode", "session_id", "native_session_id", "driver",
    )) != expected:
        return False
    owner.require_current()
    return True


def require_resume(manager: Any, session: Any) -> None:
    digest = (session.target.venue or {}).get(JOURNAL_KEY)
    if digest is None:
        return
    row = _read(manager.db, digest)
    if row is None:
        return
    unresolved = [
        key for key, value in _operations(row).items()
        if not isinstance(value, dict) or value.get("state") not in {"complete", "not-started"}
    ]
    if not unresolved:
        return
    owner = _current.get()
    if (
        owner is None or owner.manager is not manager or owner.session is not session
        or unresolved != [owner.request.operation_id]
    ):
        raise _refuse("Original retained native recovery must be inspected without replay")
    owner.require_current()


async def resume(
    manager: Any, session_id: str, request: RetainedRecoveryRequest,
    requirement: WorktreeWriterRequirement | None, ready,
) -> tuple[Any, dict]:
    from .acquisition_authority import worktree_command
    from .acquisition_worktree_writer import _qualify
    from .creation_writer_inspection import inspect_writer
    from .acquisition_launch_owner import require_unassociated_session
    from .cold_outages import latest, recovery_admission

    session = manager.get_session(session_id)
    if (
        session is None or session.session_id != session_id or requirement is None
        or session.acp_session_id != request.native_session_id
    ):
        raise _refuse("Recovery requires its exact retained session and original writer")
    with executor(manager.db, f"retained-recovery:{session.target.worktree_id}"):
        row = _read(manager.db, request.journal_key)
        if row is None or (session.target.venue or {}).get(JOURNAL_KEY) != request.journal_key:
            raise _refuse("Recovery changed its original keyed journal")
        scope, writer = _binding(row)
        if scope.worktree_writer != requirement:
            raise _refuse("Recovery changed its original Worktrees writer requirement")
        previous = _operations(row)
        if request.operation_id in previous:
            if previous[request.operation_id].get("request") != request.model_dump():
                raise _refuse("Recovery operation ID conflicts with its original request")
            return session, inspect(manager, session_id, request.operation_id)
        if any(value.get("state") not in {"complete", "not-started"} for value in previous.values()):
            raise _refuse("An unresolved original native recovery is retained")
        with _preparing(manager.db, request, previous):
            observation = await inspect_writer(manager, session, ready)
            if (
                observation["bridge_instance"]["installation_root"] != request.bridge_installation
                or session.acp_session_id != request.native_session_id
                or session.status != SessionStatus.STOPPED or session.client is not None
                or session._native_turn_transport is not None or session._handoff_in_progress
                or ENVIRONMENT_KEY in session.target.env or latest(manager.db, session_id) is not None
                or manager.db.get_session(session_id)["cold_recovery_stop_token"] is not None
            ):
                raise _refuse("Recovery requires a stopped, unambiguous original native owner")
            require_unassociated_session(manager, session, operation="retained recovery")
            inventory = manager._host_index.locked_inventory() if manager._host_index is not None else None
            if inventory is None or not inventory.storage_complete:
                raise _refuse("Recovery cannot replace unknown or retained Host custody")
            support = await _qualify(writer)
            if type(support.get("retained_native_resume")) is not int or support["retained_native_resume"] != 1:
                raise _refuse("Original Worktrees owner lacks retained native resume fencing")
            command = partial(worktree_command, owner_prefix=writer.prefix)
            preflight = await command([
                "embody", "--worktree-id", scope.worktree_id, "--json",
                "--bridge-acquisition", request.native_session_id, "--require-native-admission", "--dry-run",
            ], scope.project)
            if not (
                preflight.get("ok") is True and preflight.get("dry_run") is True
                and type(preflight.get("native_admission_version")) is int
                and preflight["native_admission_version"] == 1
                and preflight.get("worktree_id") == scope.worktree_id
                and preflight.get("work_dir") == scope.worktree_path
                and preflight.get("resume_session") == request.native_session_id
            ):
                raise _refuse("Original native owner did not prove confirmed conversation loss")
            for item in inventory.records:
                if item.session_id == session_id and not manager._host_index.release_exited_local(item):
                    raise _refuse("Recovery cannot replace unknown or retained Host custody")
            owner = RetainedRecovery(manager, session, request, session.target.to_json(), asyncio.current_task())
            previous[request.operation_id] = {"request": request.model_dump(), "state": "invoking"}
            delivery = f"retained-recovery:{request.operation_id}"
            with manager.db._runtime_transaction(write=True, full_sync=True) as conn:
                # Native-bound gates admit this native's registration but fence ordinary ACP resumes.
                inserted = conn.execute(
                    "INSERT INTO acquisition_launch_gates VALUES (?,?,'cli',?,?,'retained-recovery') "
                    "ON CONFLICT(worktree_id) DO NOTHING",
                    (scope.worktree_id, delivery, session_id, request.native_session_id),
                )
                if inserted.rowcount != 1 or conn.execute(
                    "SELECT 1 FROM sessions WHERE json_extract(target_json,'$.worktree_id')=? "
                    "AND status IN ('created','starting','running','idle') AND id<>? LIMIT 1",
                    (scope.worktree_id, session_id),
                ).fetchone() or conn.execute(
                    "SELECT 1 FROM live_sessions WHERE worktree_id=? AND status='live' "
                    "AND updated_at>=? AND session_id<>? LIMIT 1",
                    (scope.worktree_id, time.time() - 120, request.native_session_id),
                ).fetchone():
                    raise _refuse("Another native launch owner holds the retained workspace")
                conn.execute(
                    "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
                    (json.dumps(previous, sort_keys=True), request.journal_key),
                )
        session._retained_recovery_owner = owner
        token = _current.set(owner)
        try:
            with recovery_admission(manager, session_id, automatic=True):
                async with retained(manager.db, row):
                    resumed = await manager.resume_session(
                        session_id, drain=False, allow_recreate=False, owner_native_resume=True,
                        recovery_operation=owner, worktree_writer=requirement,
                    )
                    owner.require_current()
                    if resumed is not session or session.status != SessionStatus.IDLE:
                        raise _refuse("Retained native recovery did not prove an idle original session")
            state = "complete"
        except BaseException:
            state = "unknown" if owner.native_started else "not-started"
            raise
        finally:
            owner.active = False
            _current.reset(token)
            with manager.db._runtime_transaction(write=True, full_sync=True) as conn:
                row = conn.execute(
                    "SELECT * FROM workspace_embodiments WHERE key_digest=?", (request.journal_key,),
                ).fetchone()
                saved = _operations(dict(row)) if row is not None else {}
                if saved.get(request.operation_id) != previous[request.operation_id]:
                    raise _refuse("Original native recovery acceptance changed; launch gate retained")
                saved[request.operation_id] = {**saved[request.operation_id], "state": state}
                conn.execute(
                    "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
                    (json.dumps(saved, sort_keys=True), request.journal_key),
                )
                if state in {"complete", "not-started"}:
                    conn.execute(
                        "DELETE FROM acquisition_launch_gates WHERE worktree_id=? AND delivery_id=?",
                        (scope.worktree_id, delivery),
                    )
        return session, inspect(manager, session_id, request.operation_id)
