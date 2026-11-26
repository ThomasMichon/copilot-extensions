"""Supervisor finalize-first sweep for obsolete tasks awaiting a relevance closeout.

Bridge liveness reads and finalization both run in bounded background jobs;
the supervision cycle only reads coordinator pages and records results. A
background selection job picks a local candidate whose worker is confirmed
absent. The supervisor then fences start/resume and durably marks the fence
in flight (effect unknown until proven) *before* running a real
``agent-worktrees finalize`` in a second background job, so a restart can never
lose custody of a possibly half-finalized checkout. A later cycle records the
outcome: accepted evidence settles the task as abandoned, a confirmed refusal
lifts the fence so the worker closes out itself, and an unknown effect keeps
the fence and custody until the worktree's exact durable record proves
finalization or an unfinalized checkout. An outcome that could not be
published is kept and retried. An error is never treated as acceptance,
missing state never as cleanup proof, and unknown liveness never as absence.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
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
#: Shared budget for one background selection job's Bridge liveness reads.
SELECT_DEADLINE_SECONDS = 20.0
IN_FLIGHT_DETAIL = "finalize in flight; effect unknown until reconciled"
_clock = time.monotonic
#: One-shot (``supervise --once``) budget to carry selection, finalization and
#: publication to an outcome; an unfinished finalize stays in-flight custody.
DRAIN_DEADLINE_SECONDS = SELECT_DEADLINE_SECONDS + FINALIZE_TIMEOUT_SECONDS + 40.0
DRAIN_MAX_STEPS = 8
#: Candidate pages read per sweep; the cursor carries the rest to later cycles.
MAX_PAGES = 4

ACCEPTED, REFUSED, UNKNOWN = "accepted", "refused", "unknown"
ALIVE, ABSENT = "alive", "absent"

FinalizeRunner = Callable[[str, str], "subprocess.CompletedProcess[str] | None"]
StatusProbe = Callable[[str, str], "dict[str, Any] | None"]
Verdict = tuple[str, "dict[str, Any] | None", str]

#: Durable record statuses proving finalization never completed, so the worker
#: may close out its own checkout. ``finalized`` alone proves cleanup; every
#: other status (``finalizing``, archived, orphaned, unrecognized) or a missing
#: record keeps custody unknown.
UNFINALIZED_STATUSES = frozenset({"active", "complete", "completed", "pushed"})


@dataclass
class _Job:
    task_id: str
    token: str
    kind: str
    thread: threading.Thread | None = None
    verdict: Verdict = (UNKNOWN, None, "finalize-first job did not report")
    done: threading.Event = field(default_factory=threading.Event)
    #: A selection job's chosen candidate (``None`` when none was absent).
    candidate: dict[str, Any] | None = None


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
    """Reconcile an unknown finalization from the worktree's exact durable record."""
    try:
        record = probe(project, worktree_id)
    except Exception as exc:  # A failed read is not evidence either way.
        return UNKNOWN, None, f"worktree record unavailable: {type(exc).__name__}"
    if not isinstance(record, dict):
        # A missing record or an unavailable read never proves cleanup.
        return UNKNOWN, None, "exact worktree record unavailable; finalization unproven"
    status = record.get("status")
    if status == "finalized":
        return ACCEPTED, _accepted(project, worktree_id, reconciled=status), ""
    if status in UNFINALIZED_STATUSES:
        return REFUSED, None, f"finalize did not complete; worktree is {status}"[:1024]
    return UNKNOWN, None, f"worktree is {status or 'unreadable'}; finalization unproven"[:1024]


def _status_probe(project: str, worktree_id: str) -> dict[str, Any] | None:
    """The exact durable record; the roster can lag it, so a list read is never used."""
    from . import worktree_record
    from .procutil import agent_worktrees_environment, agent_worktrees_launch_prefix

    prefix = agent_worktrees_launch_prefix()
    if not prefix:
        return None
    return worktree_record.read_exact_row(
        prefix, worktree_id, project=project, timeout=STATUS_TIMEOUT_SECONDS,
        task_scoped=False, env=agent_worktrees_environment(),
    )


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


def _transient(exc: Exception) -> bool:
    return not isinstance(exc, DispatchError) or exc.status_code >= 500


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
        # The fence was marked in flight before launch, so custody survives this;
        # a transient failure keeps the outcome for retry, a rejection leaves the
        # unknown fence to exact-record reconciliation.
        log.warning("relevance finalize-first outcome unrecorded for %s: %s", task_id, exc)
        return {
            "task_id": task_id, "action": "unrecorded", "error": str(exc)[:512],
            "retry": _transient(exc),
        }


def sweep(owner: Supervisor) -> dict[str, Any] | None:
    """Advance at most one finalize-first job without blocking the cycle.

    A running job is left alone. A finished selection fences and starts its
    candidate's finalization; a finished finalization or reconciliation is
    recorded (and kept for retry when publication failed transiently).
    Otherwise one unknown fence is reconciled or a background selection starts.
    """
    job = _job(owner)
    if job is not None:
        if not job.done.is_set():
            return {"task_id": job.task_id, "action": "running", "kind": job.kind}
        if job.kind == "select":
            _set_job(owner, None)
            return _admit(owner, job.candidate)
        result = _record(owner, job)
        if not result.pop("retry", False):
            _set_job(owner, None)
        return result
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
    start_cursor = getattr(owner, "_relevance_finalize_cursor", None)
    candidates = _local_candidates(owner)
    if not candidates:
        return None
    job = _Job("", "", "select")
    _start(owner, job, lambda: _select(owner, job, candidates, start_cursor))
    return {"action": "selecting", "kind": "select", "candidates": len(candidates)}


def drain(
    owner: Supervisor, *, budget: float = DRAIN_DEADLINE_SECONDS, max_steps: int = DRAIN_MAX_STEPS,
) -> list[dict[str, Any]]:
    """Run finalize-first to an outcome for a one-shot supervisor, within ``budget``.

    The recurring poll only advances one background job per cycle, so a
    process that polls once and exits would never fence or finalize. This
    waits for each job and sweeps again until one outcome is recorded (or
    left unrecorded), nothing is left to do, or the budget expires; a running
    finalize then keeps its durable in-flight fence for later reconciliation.
    """
    deadline = _clock() + budget
    results: list[dict[str, Any]] = []
    for _ in range(max_steps):
        job = _job(owner)
        if job is not None and not job.done.is_set():
            remaining = deadline - _clock()
            if remaining <= 0 or not job.done.wait(remaining):
                results.append({"task_id": job.task_id, "action": "running", "kind": job.kind})
                break
        elif job is None and _clock() >= deadline:
            break
        try:
            result = sweep(owner)
        except Exception as exc:  # The one-shot drain stays as additive as the cycle sweep.
            log.error("relevance finalize-first drain failed: %s", type(exc).__name__)
            break
        if result is None:
            break
        results.append(result)
        if result.get("action") not in ("selecting", "started", "running"):
            break
    return results


def _admit(owner: Supervisor, candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    if action_inhibited("worker-admission", logger=log):
        return None
    governance = owner._recheck_governance("pre-mutation:relevance-finalize-first")
    if governance is not None and governance.get("status") != "ready":
        return None
    return _attempt(owner, candidate)


def _select(
    owner: Supervisor, job: _Job, candidates: list[dict[str, Any]], start_cursor: Any,
) -> Verdict:
    """Off the poll thread: the first candidate confirmed absent within the budget."""
    deadline = _clock() + SELECT_DEADLINE_SECONDS
    for index, candidate in enumerate(candidates):
        if _clock() >= deadline:
            # Resume after the last probed candidate so none is skipped unprobed.
            owner._relevance_finalize_cursor = (  # type: ignore[attr-defined]
                candidates[index - 1].get("cursor") if index else start_cursor
            )
            break
        if _liveness(owner, candidate) == ABSENT:
            job.candidate = candidate
            return ACCEPTED, None, ""
    return UNKNOWN, None, "no candidate confirmed absent"


def _local_candidates(owner: Supervisor) -> list[dict[str, Any]]:
    """Local candidates from up to ``MAX_PAGES`` coordinator pages; no Bridge reads."""
    from .queue_relevance_finalize_first import CANDIDATE_LIMIT

    found: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        after = getattr(owner, "_relevance_finalize_cursor", None)
        try:
            page = owner.client.relevance_finalize_candidates(owner.supervisor_id, after=after)
        except DispatchUpgradeRequired:
            return found
        except (DispatchError, httpx.TransportError, ValueError) as exc:
            log.warning("relevance finalize-first candidates unavailable: %s", exc)
            return found
        cursor = page[-1].get("cursor") if page else None
        # A short page ends the cohort; the next sweep starts over from the oldest.
        owner._relevance_finalize_cursor = (  # type: ignore[attr-defined]
            cursor if len(page) >= CANDIDATE_LIMIT and isinstance(cursor, dict) else None
        )
        found.extend(
            candidate for candidate in page
            if isinstance(candidate.get("reservation"), dict) and _local(candidate)
        )
        if owner._relevance_finalize_cursor is None:  # type: ignore[attr-defined]
            break
    return found


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
    token = fence["token"]
    try:
        # Durable in-flight custody before any effect: a restart or a lost
        # outcome leaves an unknown fence that never reopens execution by expiry.
        client.retain_relevance_finalize(task_id, token=token, detail=IN_FLIGHT_DETAIL)
    except (DispatchError, httpx.TransportError, ValueError) as exc:
        log.warning("relevance finalize-first not started for %s: %s", task_id, exc)
        try:  # Nothing ran; lift the fence so the worker can close out itself.
            client.release_relevance_finalize(
                task_id, token=token, detail=f"finalize not started: {exc}"[:1024],
            )
        except (DispatchError, httpx.TransportError, ValueError):
            pass
        return {"task_id": task_id, "action": "not_started", "error": str(exc)[:512]}
    runner: FinalizeRunner = getattr(owner, "finalize_runner", None) or run_finalize

    def work() -> Verdict:
        # Liveness may have changed while fencing; anything but confirmed
        # absence leaves the checkout alone and the worker closes out itself.
        if _liveness(owner, candidate) != ABSENT:
            return REFUSED, None, "worker became live or its liveness is unknown before finalize"
        return finalize_evidence(runner, reservation["allocation_project"], reservation["worktree"])

    return _start(owner, _Job(task_id, token, "finalize"), work)
