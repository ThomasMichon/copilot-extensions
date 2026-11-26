"""Supervisor finalize-first sweep for obsolete tasks awaiting a relevance closeout.

For each candidate whose worker is confirmed absent on this machine the
supervisor fences start/resume, runs a real ``agent-worktrees finalize`` on the
task's managed worktree in a bounded background job, and on a later cycle
records the outcome: accepted evidence settles the task as abandoned, a
confirmed refusal lifts the fence so the worker closes out itself, and an
unknown effect (timeout, unavailable CLI, malformed result) keeps the fence and
custody until a later worktree status read reconciles it. An error is never
treated as acceptance, and unknown liveness is never treated as absence.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from .action_controls import action_inhibited
from .client_errors import DispatchError, DispatchUpgradeRequired
from .queue_relevance_finalize_first import FINALIZE_RUNNER

if TYPE_CHECKING:
    from .supervisor import Supervisor

log = logging.getLogger("agent-dispatch.supervisor")

FINALIZE_TIMEOUT_SECONDS = 180.0
STATUS_TIMEOUT_SECONDS = 15.0
#: Candidate pages read per sweep; the cursor carries the rest to later cycles.
MAX_PAGES = 4

ACCEPTED, REFUSED, UNKNOWN = "accepted", "refused", "unknown"
ALIVE, ABSENT = "alive", "absent"

FinalizeRunner = Callable[[str, str], "subprocess.CompletedProcess[str] | None"]
StatusProbe = Callable[[str], "dict[str, Any] | None"]
Verdict = tuple[str, "dict[str, Any] | None", str]

#: Worktree statuses that prove nothing is left for the worker to clean up.
RECONCILED_STATUSES = frozenset({"finalized", "absent"})


@dataclass
class _Job:
    task_id: str
    token: str
    kind: str
    thread: threading.Thread | None = None
    verdict: Verdict = (UNKNOWN, None, "finalize-first job did not report")
    done: threading.Event = field(default_factory=threading.Event)


def run_finalize(project: str, worktree_id: str) -> subprocess.CompletedProcess[str] | None:
    from .procutil import run_agent_worktrees_capture

    return run_agent_worktrees_capture(
        "--project", project, "finalize", "--worktree-id", worktree_id, "--json",
        timeout=FINALIZE_TIMEOUT_SECONDS,
    )


def _accepted(project: str, worktree_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "runner": FINALIZE_RUNNER, "status": "finalized", "worktree_id": worktree_id,
        "project": project, "returncode": 0, **extra,
    }


def finalize_evidence(runner: FinalizeRunner, project: str, worktree_id: str) -> Verdict:
    """Classify one finalize run as accepted, a confirmed refusal, or unknown.

    Only a parseable result that reports failure is a refusal. A timeout, an
    unavailable CLI, an exception or an unparseable result may have removed
    the checkout already, so its effect is unknown.
    """
    try:
        result = runner(project, worktree_id)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return UNKNOWN, None, f"finalize failed: {type(exc).__name__}: {exc}"[:1024]
    if result is None:
        return UNKNOWN, None, "agent-worktrees finalize timed out or is unavailable"
    try:
        value = json.loads(result.stdout or "")
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return UNKNOWN, None, f"finalize returned no result (exit {result.returncode})"
    if (
        result.returncode == 0 and value.get("success") is True
        and value.get("status") == "finalized" and value.get("worktree_id") == worktree_id
    ):
        return ACCEPTED, _accepted(project, worktree_id), ""
    if value.get("success") is not False:
        return UNKNOWN, None, f"finalize result is inconclusive (exit {result.returncode})"
    detail = value.get("error")
    if not isinstance(detail, str) or not detail:
        detail = (result.stderr or "").strip() or f"exit {result.returncode}"
    return REFUSED, None, f"finalize refused: {detail}"[:1024]


def reconcile_evidence(probe: StatusProbe, project: str, worktree_id: str) -> Verdict:
    """Reconcile an unknown finalization from the worktree's tracking record."""
    try:
        record = probe(worktree_id)
    except Exception as exc:  # A failed read is not evidence either way.
        return UNKNOWN, None, f"worktree status unavailable: {type(exc).__name__}"
    if not isinstance(record, dict):
        return UNKNOWN, None, "worktree status unavailable"
    status = record.get("status")
    if status in RECONCILED_STATUSES:
        return ACCEPTED, _accepted(project, worktree_id, reconciled=status), ""
    return REFUSED, None, f"finalize did not complete; worktree is {status or 'unreadable'}"[:1024]


def _status_probe(worktree_id: str) -> dict[str, Any] | None:
    from .doctor import resolve_worktree

    return resolve_worktree(worktree_id, timeout=STATUS_TIMEOUT_SECONDS)


def _local(candidate: dict[str, Any]) -> bool:
    from . import remote_dispatch
    from .bridge_remote import normalize_host

    machine = candidate["reservation"].get("execution_machine")
    local = remote_dispatch.local_machine()
    if not local:
        return False
    return not machine or normalize_host(local) == normalize_host(str(machine))


def _liveness(owner: Supervisor, candidate: dict[str, Any]) -> str:
    """``alive``, ``absent`` or ``unknown``; only a confirmed absence is eligible."""
    reservation = candidate["reservation"]
    probe = getattr(owner, "finalize_liveness_fn", None)
    try:
        if probe is not None:
            verdict = probe(reservation["worktree"], reservation.get("execution_machine"))
        else:
            from . import tracking

            verdict = {tracking.LIVE: ALIVE, tracking.GONE: ABSENT}.get(
                tracking.local_worktree_occupancy(str(reservation.get("worktree") or "")), UNKNOWN,
            )
    except Exception:
        return UNKNOWN
    return verdict if verdict in (ALIVE, ABSENT) else UNKNOWN


def _job(owner: Supervisor) -> _Job | None:
    return getattr(owner, "_relevance_finalize_job", None)


def _set_job(owner: Supervisor, job: _Job | None) -> None:
    owner._relevance_finalize_job = job  # type: ignore[attr-defined]


def _start(owner: Supervisor, job: _Job, work: Callable[[], Verdict]) -> dict[str, Any]:
    def run() -> None:
        try:
            job.verdict = work()
        except Exception as exc:  # Never let a crashed job read as acceptance.
            job.verdict = (UNKNOWN, None, f"finalize-first job failed: {type(exc).__name__}")
        finally:
            job.done.set()

    job.thread = threading.Thread(target=run, name=f"relevance-finalize-{job.task_id}", daemon=True)
    _set_job(owner, job)
    job.thread.start()
    return {"task_id": job.task_id, "action": "started", "kind": job.kind}


def _record(owner: Supervisor, job: _Job) -> dict[str, Any]:
    client = owner.client
    verdict, evidence, detail = job.verdict
    task_id, token = job.task_id, job.token
    try:
        if verdict == ACCEPTED and evidence is not None:
            result = client.settle_relevance_finalize(task_id, token=token, evidence=evidence)
            log.info("relevance finalize-first abandoned obsolete task %s", task_id)
            return {"task_id": task_id, "action": "settled", **result}
        if verdict == REFUSED:
            result = client.release_relevance_finalize(task_id, token=token, detail=detail)
            log.info("relevance finalize-first refused for %s: %s", task_id, detail)
            return {"action": "released", **result}
        result = client.retain_relevance_finalize(task_id, token=token, detail=detail)
        log.warning("relevance finalize-first effect unknown for %s; custody retained: %s", task_id, detail)
        return {"action": "retained", **result}
    except (DispatchError, httpx.TransportError, ValueError) as exc:
        # The fence expires on its own; never assume settlement happened.
        log.warning("relevance finalize-first outcome unrecorded for %s: %s", task_id, exc)
        return {"task_id": task_id, "action": "unrecorded", "error": str(exc)[:512]}


def sweep(owner: Supervisor) -> dict[str, Any] | None:
    """Advance at most one finalize-first job without blocking the cycle.

    A running job is left alone; a finished job's verdict is recorded; otherwise
    one unknown fence is reconciled or one fresh candidate is fenced and its
    finalization started in the background.
    """
    job = _job(owner)
    if job is not None:
        if not job.done.is_set():
            return {"task_id": job.task_id, "action": "running", "kind": job.kind}
        _set_job(owner, None)
        return _record(owner, job)
    client = owner.client
    if getattr(client, "relevance_finalize_candidates", None) is None:
        return None
    try:
        unknown = client.relevance_finalize_unknown(owner.supervisor_id)
    except DispatchUpgradeRequired:
        return None
    except (DispatchError, httpx.TransportError, ValueError) as exc:
        log.warning("relevance finalize-first unknown fences unavailable: %s", exc)
        return None
    # Rotate through unknown fences so one unresolvable checkout cannot starve
    # the others or fresh candidates; each is retried on a later rotation.
    tried: set[str] = getattr(owner, "_relevance_finalize_reconciled", set())
    for row in unknown:
        reservation = row.get("reservation")
        if isinstance(reservation, dict) and _local(row) and row["task_id"] not in tried:
            tried.add(row["task_id"])
            owner._relevance_finalize_reconciled = tried  # type: ignore[attr-defined]
            return _reconcile(owner, row)
    owner._relevance_finalize_reconciled = set()  # type: ignore[attr-defined]
    candidate = _next_candidate(owner)
    if candidate is None:
        return None
    if action_inhibited("worker-admission", logger=log):
        return None
    governance = owner._recheck_governance("pre-mutation:relevance-finalize-first")
    if governance is not None and governance.get("status") != "ready":
        return None
    return _attempt(owner, candidate)


def _next_candidate(owner: Supervisor) -> dict[str, Any] | None:
    from .queue_relevance_finalize_first import CANDIDATE_LIMIT

    for _ in range(MAX_PAGES):
        after = getattr(owner, "_relevance_finalize_cursor", None)
        try:
            page = owner.client.relevance_finalize_candidates(owner.supervisor_id, after=after)
        except DispatchUpgradeRequired:
            return None
        except (DispatchError, httpx.TransportError, ValueError) as exc:
            log.warning("relevance finalize-first candidates unavailable: %s", exc)
            return None
        cursor = page[-1].get("cursor") if page else None
        # A short page ends the cohort; the next sweep starts over from the oldest.
        owner._relevance_finalize_cursor = (  # type: ignore[attr-defined]
            cursor if len(page) >= CANDIDATE_LIMIT and isinstance(cursor, dict) else None
        )
        for candidate in page:
            reservation = candidate.get("reservation")
            if isinstance(reservation, dict) and _local(candidate) and _liveness(owner, candidate) == ABSENT:
                return candidate
        if owner._relevance_finalize_cursor is None:  # type: ignore[attr-defined]
            return None
    return None


def _reconcile(owner: Supervisor, row: dict[str, Any]) -> dict[str, Any]:
    reservation = row["reservation"]
    probe: StatusProbe = getattr(owner, "finalize_status_fn", None) or _status_probe
    job = _Job(row["task_id"], row["token"], "reconcile")
    return _start(owner, job, lambda: reconcile_evidence(
        probe, reservation["allocation_project"], reservation["worktree"],
    ))


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
    runner: FinalizeRunner = getattr(owner, "finalize_runner", None) or run_finalize

    def work() -> Verdict:
        # Liveness may have changed while fencing; anything but confirmed
        # absence leaves the checkout alone and the worker closes out itself.
        if _liveness(owner, candidate) != ABSENT:
            return REFUSED, None, "worker became live or its liveness is unknown before finalize"
        return finalize_evidence(runner, reservation["allocation_project"], reservation["worktree"])

    return _start(owner, _Job(task_id, fence["token"], "finalize"), work)
