"""Supervisor finalize-first sweep for obsolete tasks awaiting a relevance closeout.

For each candidate whose worker is not hot on this machine the supervisor
fences start/resume, runs a real ``agent-worktrees finalize`` on the task's
managed worktree and either settles the task as abandoned (accepted) or lifts
the fence so the worker closes out itself (refused, unavailable or failed).
An error is never treated as acceptance.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from .action_controls import action_inhibited
from .client_errors import DispatchError, DispatchUpgradeRequired
from .queue_relevance_finalize_first import FINALIZE_RUNNER

if TYPE_CHECKING:
    from .supervisor import Supervisor

log = logging.getLogger("agent-dispatch.supervisor")

FINALIZE_TIMEOUT_SECONDS = 180.0

FinalizeRunner = Callable[[str, str], "subprocess.CompletedProcess[str] | None"]


def run_finalize(project: str, worktree_id: str) -> subprocess.CompletedProcess[str] | None:
    from .procutil import run_agent_worktrees_capture

    return run_agent_worktrees_capture(
        "--project", project, "finalize", "--worktree-id", worktree_id, "--json",
        timeout=FINALIZE_TIMEOUT_SECONDS,
    )


def finalize_evidence(
    runner: FinalizeRunner, project: str, worktree_id: str,
) -> tuple[dict[str, Any] | None, str]:
    """Return accepted evidence, or ``None`` with the refusal detail."""
    try:
        result = runner(project, worktree_id)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return None, f"finalize failed: {type(exc).__name__}: {exc}"[:1024]
    if result is None:
        return None, "agent-worktrees finalize is unavailable"
    try:
        value = json.loads(result.stdout or "")
    except ValueError:
        value = None
    if (
        result.returncode != 0 or not isinstance(value, dict) or value.get("success") is not True
        or value.get("status") != "finalized" or value.get("worktree_id") != worktree_id
    ):
        detail = value.get("error") if isinstance(value, dict) else None
        if not isinstance(detail, str) or not detail:
            detail = (result.stderr or "").strip() or f"exit {result.returncode}"
        return None, f"finalize refused: {detail}"[:1024]
    return {
        "runner": FINALIZE_RUNNER, "status": "finalized", "worktree_id": worktree_id,
        "project": project, "returncode": 0,
    }, ""


def _local(candidate: dict[str, Any]) -> bool:
    from . import remote_dispatch
    from .bridge_remote import normalize_host

    machine = candidate["reservation"].get("execution_machine")
    local = remote_dispatch.local_machine()
    if not local:
        return False
    return not machine or normalize_host(local) == normalize_host(str(machine))


def _hot(owner: Supervisor, candidate: dict[str, Any]) -> bool:
    reservation = candidate["reservation"]
    try:
        return owner.liveness_fn(reservation["worktree"], reservation.get("execution_machine")) is not None
    except Exception:
        # Unknown liveness is not proof the worker is cold.
        return True


def sweep(owner: Supervisor) -> dict[str, Any] | None:
    """Try at most one finalize-first settlement; return its outcome when attempted."""
    client = owner.client
    lookup = getattr(client, "relevance_finalize_candidates", None)
    if lookup is None:
        return None
    try:
        candidates = lookup(owner.supervisor_id)
    except DispatchUpgradeRequired:
        return None
    except (DispatchError, httpx.TransportError, ValueError) as exc:
        log.warning("relevance finalize-first candidates unavailable: %s", exc)
        return None
    for candidate in candidates:
        reservation = candidate.get("reservation")
        if not isinstance(reservation, dict) or not _local(candidate) or _hot(owner, candidate):
            continue
        if action_inhibited("worker-admission", logger=log):
            return None
        governance = owner._recheck_governance("pre-mutation:relevance-finalize-first")
        if governance is not None and governance.get("status") != "ready":
            return None
        return _attempt(owner, candidate)
    return None


def _attempt(owner: Supervisor, candidate: dict[str, Any]) -> dict[str, Any] | None:
    client = owner.client
    task_id = candidate["task_id"]
    reservation = candidate["reservation"]
    try:
        fence = client.fence_relevance_finalize(
            task_id, supervisor_id=owner.supervisor_id, generation=candidate["generation"],
            reservation_key=reservation["key"],
        )
    except (DispatchError, httpx.TransportError, ValueError) as exc:
        log.info("relevance finalize-first not fenced for %s: %s", task_id, exc)
        return None
    # Liveness may have changed while fencing; a hot worker closes out itself.
    if _hot(owner, candidate):
        evidence, detail = None, "worker became live before finalize"
    else:
        runner: FinalizeRunner = getattr(owner, "finalize_runner", None) or run_finalize
        evidence, detail = finalize_evidence(
            runner, reservation["allocation_project"], reservation["worktree"],
        )
    token = fence["token"]
    try:
        if evidence is not None:
            result = client.settle_relevance_finalize(task_id, token=token, evidence=evidence)
            log.info("relevance finalize-first abandoned obsolete task %s", task_id)
            return {"task_id": task_id, "action": "settled", **result}
        result = client.release_relevance_finalize(task_id, token=token, detail=detail)
        log.info("relevance finalize-first refused for %s: %s", task_id, detail)
        return {"action": "released", **result}
    except (DispatchError, httpx.TransportError, ValueError) as exc:
        # The fence expires on its own; never assume settlement happened.
        log.warning("relevance finalize-first outcome unrecorded for %s: %s", task_id, exc)
        return {"task_id": task_id, "action": "unrecorded", "error": str(exc)[:512]}
