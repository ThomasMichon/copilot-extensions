"""Request-local foreground composition over the canonical journal and executor."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from single_instance_lease.operation import OperationLock, OperationLockedError

from . import core


def run_explicit(
    spec: core.RequestSpec, *, runner: core.TrustedRunner, root: Path | None = None,
    executor: Callable[[core.Transaction], int] | None = None,
    on_admitted: Callable[[core.Request], None] | None = None,
) -> core.Request:
    """Atomically claim a caller-owned operation; never queue replayable work or drain peers."""
    if executor is None:
        from agent_procutil.owned_process import require_caller_owned_support

        require_caller_owned_support()
    root = core._root(root)
    with core._database(root, create=True):
        pass
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError as error:
        raise core.StateError("request-local execution is busy; no request admitted") from error
    try:
        core._assert_foundation_clear(root, include_running=True)
        with core._database(root, writable=True) as db:
            db.execute("BEGIN IMMEDIATE")
            lock.assert_owned()
            if db.execute("SELECT 1 FROM requests WHERE state='running' LIMIT 1").fetchone() is not None:
                raise core.InterruptedError("installation queue has unsettled running work; recovery required")
            if db.execute(
                "SELECT 1 FROM requests WHERE state='pending' LIMIT 1",
            ).fetchone() is not None or core._has_fence(db, root):
                raise core.StateError("request-local execution requires an idle, unfenced queue; nothing replayed")
            core._upgrade_schema(db, root, lock)
            db.commit()
        request = core.enqueue(spec, runner=runner, root=root, _exclusive_custody=lock)
        try:
            if on_admitted is not None:
                on_admitted(request)
            core.drain(root=root, executor=executor, _request_id=request.id, _custody=lock)
        except BaseException as error:
            setattr(error, "request_id", request.id)
            if core.status(request.id, root=root).state == "running":
                core._outcome(
                    root, request.id, "interrupted",
                    error=f"foreground owner interrupted: {type(error).__name__}; settlement required",
                )
            raise
        result = core.status(request.id, root=root)
        if not isinstance(result, core.Request):
            raise core.StateError("request-local execution returned no request")
        return result
    finally:
        lock.release()
