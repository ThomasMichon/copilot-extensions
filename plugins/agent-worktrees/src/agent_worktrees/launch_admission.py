"""Kernel-owned exclusion between creation recovery and seed handoff."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import time
from typing import TYPE_CHECKING

from single_instance_lease import AlreadyRunningError, SingleInstance

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


class LaunchAdmissionBusy(ValueError):
    """Another launch mutation or creation repair owns this worktree."""


def _lease(path: Path) -> SingleInstance:
    from .launch_seed_state import state_path

    return SingleInstance(
        state_path(path).parent, service="worktree launch admission",
        lock_name="launch-admission.lock",
    )


@contextmanager
def guard(path: Path, *, timeout: float = 0.0) -> Iterator[None]:
    lease = _lease(path)
    deadline = time.monotonic() + timeout
    while True:
        try:
            lease.acquire()
            break
        except AlreadyRunningError as exc:
            if time.monotonic() >= deadline:
                raise LaunchAdmissionBusy(
                    "Worktree launch admission is busy; the prompt remains staged. "
                    "Retry after the current launch mutation or creation repair completes."
                ) from exc
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    try:
        yield
    finally:
        lease.release()


def check(path: Path) -> None:
    if _lease(path).lock_path.exists():
        with guard(path):
            pass


def _require_running_harness(record: WorktreeRecord, holder_pid: int | None) -> None:
    from . import launch_seed_state, locks, procs, reclaim, sessions, tracking

    current = tracking.load_record(record.yaml_path)
    if any(getattr(current, field) != getattr(record, field) for field in (
        "repo", "worktree_id", "worktree_path", "branch", "creation_nonce", "owner_ref",
    )):
        raise LaunchAdmissionBusy("Harness allocation changed during recovery admission")
    head = current.resolved_head_session
    if (
        not holder_pid or not current.creation_nonce or current.kind != "session"
        or current.status != "active" or current.pair_role == "knowledge"
        or not head or current.pending_seed or current.pending_handoffs
        or launch_seed_state.peek(record.yaml_path, read_only=True) is not None
    ):
        raise LaunchAdmissionBusy("Occupied launch admission has no established harness identity")
    owner_birth = locks.process_start_time(holder_pid)
    live = sessions.verify_worktree_active(current, strict=True)
    if (
        not owner_birth or not live.probes_ok or not live.mux_probe_ok
        or live.live_session_ids != [head]
    ):
        raise LaunchAdmissionBusy("Occupied launch admission has unknown or conflicting execution")
    bindings = reclaim.resolve_bound_copilots(
        worktree_id=current.worktree_id, session_ids=[head], strict=True,
    )
    if len(bindings) != 1:
        raise LaunchAdmissionBusy("Occupied launch admission has no unique registered process")
    process = bindings[0]
    pid, birth = process.get("pid"), process.get("start_time")
    if (
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
        or process.get("session_id") != head or process.get("worktree_id") != current.worktree_id
        or not isinstance(birth, str) or not birth
        or locks.process_start_time(pid) != birth
        or not procs.is_descendant_of(pid, {holder_pid})
        or locks.process_start_time(holder_pid) != owner_birth
    ):
        raise LaunchAdmissionBusy("Occupied launch admission is not the registered harness's native owner")


@contextmanager
def existing_harness_guard(record: WorktreeRecord) -> Iterator[None]:
    """Keep lifecycle exclusion without stopping a proven native lifetime owner."""
    from .worktree_lifecycle import guard as lifecycle_guard

    with lifecycle_guard(record.yaml_path):
        lease = _lease(record.yaml_path)
        owned = False
        try:
            lease.acquire()
            owned = True
        except AlreadyRunningError as exc:
            _require_running_harness(record, exc.holder_pid)
        try:
            yield
        finally:
            if owned:
                lease.release()
