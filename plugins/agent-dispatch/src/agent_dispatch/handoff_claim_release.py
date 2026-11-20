"""Release the target worktree's agent-worktrees claim once a context-handoff
task reaches a terminal state.

Context-handoff journals a ``task``-kind claim (via ``agent-worktrees claims
add task <id> --worktree <target_worktree>``) on a handoff task's target
worktree the moment it creates the task -- so agent-worktrees' existing,
kind-agnostic finalize obligation-settlement gate refuses to finalize that
worktree until the claim is released, forcing whoever finalizes it to first
deal with the still-open handoff (consume it, or explicitly abandon it) rather
than silently leaving it behind.

This module is the matching release side, hooked into the queue engine's own
terminal-transition entry points (``coordinator_tasks``'s HTTP routes and
``mcp_http``'s MCP tools both call the same underlying
:meth:`agent_dispatch.queue.TaskQueue.complete_with_outcome` /
:meth:`agent_dispatch.queue.TaskQueue.abandon`) rather than any one transport
layer, so every caller -- the CLI (which always talks HTTP to the
coordinator), an MCP client, or a direct HTTP caller -- is covered by the same
release regardless of which one resolved the handoff.

Deliberately does **not** reuse :mod:`agent_dispatch.hibernation_claims`'
``release_hibernation_claim``: that helper releases the claim on the
*current* worktree (inferred from its caller's own cwd), which is correct for
its own use case (``agent-dispatch run --detach``/``--resume``, always
invoked from within the worktree being hibernated/resumed) but wrong here --
this module's release runs inside the coordinator process or an MCP tool
call, whose cwd is generally *not* the handoff's target worktree. This module
uses the task's recorded creator binding, never an unqualified release.

The task's durable ``handoff_claim`` metadata records the exact native claim
returned after journaling. It is a selector, not authority. Terminal task
confirmation and Worktrees' locked record/claim identity, candidate, producer
and mutation checks remain independent requirements. Unbound legacy tasks
stay protected until an explicitly owner-qualified reconciliation.

**Fire-and-forget, never blocks the caller's own request.** ``release_if_handoff``
dispatches the bounded native operations on a background daemon
thread rather than running it inline: this module is called from inside
``coordinator_tasks``'s HTTP route handlers and ``mcp_http``'s MCP tools --
shared request-handling paths every completion/abandonment flows through, not
just handoff tasks' own. Running the release synchronously there would add
subprocess-call latency (and, if the release call is ever genuinely slow, an
outright client-side read-timeout risk) to *every* handoff completion over
HTTP or MCP -- a regression the release's own best-effort, cheap-failure-mode
philosophy already says is not worth the cost. The caller gets its
completion/abandon response immediately; the claim release trails behind it.

**Known, narrow, accepted gap:** :meth:`queue_liveness.LivenessMixin
.reconcile_liveness`'s owner-gone reconciliation can move a held task straight
to ``dead_letter`` without flowing through ``complete_with_outcome``/
``abandon`` at all, so this release is not reached on that path. For a
genuine handoff task this is a vanishingly narrow window in practice: a
handoff task always classifies as ``cli_embodied=True`` there (it holds no
headless spawn reservation), and that reconciliation only ever routes a
``cli_embodied`` task to ``suspended`` (never ``dead_letter``) once it
reaches ``started`` -- ``dead_letter`` is reachable only in the brief
``claimed``-but-not-yet-``started`` window between a consumer's ``claim()``
and its immediately-following ``start()`` call, with its owner going gone and
``attempts`` already at the cap in that same instant. A claim left behind
here is the same cheap, safe failure mode noted above (an operator
investigates and releases it manually) -- not silently reintroducing the
forgotten-handoff gap this module exists to close, since ``dead_letter``
itself is already a visible, investigated failure state.
"""

from __future__ import annotations

import threading
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .queue import TaskQueue


def is_handoff_task(task: dict[str, Any] | None) -> bool:
    """True if ``task`` (a task dict from ``client.get``/``complete``/etc.) is
    a context-handoff-created task -- the ones that hold a claim to release.

    The one shared classification: consume, attention and this release all
    use it (a task carries the ``handoff`` label, or predates that label and
    only carries the ``context-handoff`` source stamp).
    """
    if not isinstance(task, dict):
        return False
    labels = task.get("labels") or []
    return "handoff" in labels or task.get("source") == "context-handoff"


def handoff_worktree(task: dict[str, Any] | None) -> str | None:
    """The worktree a handoff baton belongs to: its ``target_worktree``, else
    its ``worktree`` affinity (a detached baton keeps only the affinity)."""
    if not isinstance(task, dict):
        return None
    raw_affinity = task.get("affinity")
    affinity = raw_affinity if isinstance(raw_affinity, dict) else {}
    worktree = task.get("target_worktree") or affinity.get("worktree")
    return worktree if isinstance(worktree, str) and worktree else None


def handoff_is_local(task: dict[str, Any] | None, machine: str | None) -> bool:
    """Whether a baton's worktree is on ``machine`` (this one): it names no
    ``target_machine``, or names this one. A worktree id and its handoff ledger
    are machine-local, so a baton pinned elsewhere can't be checked, resumed
    or cancelled from here."""
    target = (task or {}).get("target_machine")
    return not target or (machine is not None and str(target).casefold() == machine.casefold())


def _this_machine() -> str | None:
    from .remote_dispatch import local_machine

    return local_machine()


def release_if_handoff(
    task: dict[str, Any] | None, task_id: str | None = None, *, timeout: float = 15.0,
    cancel_pending: bool = False, queue: TaskQueue | None = None,
) -> None:
    """Best-effort, non-blocking: release the ``target_worktree``'s claim on
    ``task``'s id iff ``task`` is a context-handoff task.

    ``cancel_pending`` (an abandon) also cancels the baton's entry in that
    worktree's own handoff ledger, so an abandoned handoff no longer reads as
    pending there (``agent-worktrees cancel-handoff``, which leaves an entry a
    successor is already taking over untouched). A completion doesn't: its
    pickup consumed the entry.

    ``task_id`` overrides ``task.get("id")`` for a caller that already has the
    id handy (e.g. from the request body) and wants to avoid relying on the
    fetched record echoing it back verbatim. Never raises and never blocks
    the calling request: the actual release subprocess runs on a background
    daemon thread (see the module docstring). A claim left behind only blocks
    that one worktree's own future finalize until an operator investigates (a
    cheap, safe failure mode) -- never this task's own completion or
    abandonment, which must always succeed on their own terms regardless of
    this bookkeeping.
    """
    if not is_handoff_task(task):
        return
    resolved_id = task_id or (task or {}).get("id")
    if not resolved_id:
        return
    # A detached baton keeps its worktree only as an affinity: the claim and
    # the ledger entry are still that worktree's.
    worktree = handoff_worktree(task)
    thread = threading.Thread(
        target=_release_in_background,
        args=(resolved_id, (task or {}).get("target_machine")),
        kwargs={
            "worktree": worktree, "timeout": timeout, "cancel_pending": cancel_pending,
            "task": task, "queue": queue,
        },
        daemon=True,
        name=f"handoff-claim-release-{resolved_id}",
    )
    thread.start()


def _release_in_background(task_id: str, target_machine: Any, *, worktree: str | None, timeout: float,
                           cancel_pending: bool, task: dict[str, Any] | None = None,
                           queue: TaskQueue | None = None) -> None:
    """The release thread's body. The ledger is the baton's own machine's, so
    this host cancels only its own (like the claim release, which runs here
    too); resolving this machine's name may shell out, so it happens here,
    never on the caller's request."""
    if queue is not None:
        current = queue.get(task_id)
        task = asdict(current) if current is not None else None
    result = _release_task_claim(task_id, worktree=worktree, timeout=timeout, task=task)
    if not result["taskTerminal"]:
        return
    if not result["claimSettled"]:
        logging.getLogger(__name__).warning("Handoff creator claim remains pending: %s", result["claimNote"])
    if cancel_pending and target_machine:
        cancel_pending = handoff_is_local({"target_machine": target_machine}, _this_machine())
    if cancel_pending and worktree:
        from .procutil import run_agent_worktrees_capture
        from .handoff_claim_binding import validated_binding
        from .queue_records import TaskError
        try:
            binding = validated_binding((task or {}).get("handoff_claim"), task or {})
        except TaskError:
            return
        run_agent_worktrees_capture(
            "cancel-handoff",
            "--owner-ref", f'{binding["machine"]}/{binding["project"]}/{binding["worktree_id"]}',
            "--expected-record-nonce", binding["creation_nonce"], "--token", task_id, timeout=timeout,
        )


def _release_task_claim(
    task_id: str, *, worktree: str | None, timeout: float = 15.0,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Settle only a confirmed terminal handoff's recorded native incarnation.

    Shells via :func:`agent_dispatch.procutil.run_agent_worktrees_capture` --
    the same helper every other agent-worktrees spawn in this plugin uses --
    so this release gets the same scrubbed environment
    (:func:`agent_dispatch.procutil.agent_worktrees_environment`, which
    strips ``PYTHONHOME``/``PYTHONPATH``/``VIRTUAL_ENV``/
    ``__PYVENV_LAUNCHER__`` so the resolved agent-worktrees interpreter can't
    inherit the coordinator's own Python environment and load the wrong
    stdlib/venv) and the same no-console-window, leak-safe process handling.
    Unavailable, unbound or replaced owners return an explicit pending result.
    A read-only native binding operation establishes selected-provider support
    before the guarded write; parser refusal never causes an unpinned fallback.

    Runs synchronously on whatever thread calls it -- :func:`release_if_handoff`
    is the fire-and-forget entry point; call this directly only from a
    context (e.g. a test, or an already-background thread) that intends to
    block on it.
    """
    from .procutil import run_agent_worktrees_capture

    from .handoff_claim_binding import validated_binding
    from .queue_records import TaskError

    note = terminal_rejection(task, task_id)
    result: dict[str, Any] = {"taskTerminal": note is None, "claimSettled": False}
    if note:
        return {**result, "claimNote": note}
    try:
        binding = validated_binding((task or {}).get("handoff_claim"), task or {})
        if worktree and binding["worktree_id"] != worktree:
            raise TaskError("creator binding differs from the selected worktree")
    except TaskError as exc:
        return {**result, "claimNote": str(exc)}
    owner_ref = f'{binding["machine"]}/{binding["project"]}/{binding["worktree_id"]}'
    try:
        observed = _capture_result(run_agent_worktrees_capture(
            "claims", "binding", task_id, "--owner-ref", owner_ref, "--json", timeout=timeout,
        ))
        if validated_binding(observed.get("claim_binding"), task or {}) != binding:
            raise TaskError("creator claim binding no longer matches")
        settled = _capture_result(run_agent_worktrees_capture(
            "claims", "settle", task_id, "--owner-ref", owner_ref, "--released",
            "--expected-record-nonce", binding["creation_nonce"],
            "--expected-claim-nonce", binding["claim_nonce"], "--expected-kind", "task",
            "--json", timeout=timeout,
        ))
        if (
            settled.get("deferred") or settled.get("ref") != task_id
            or settled.get("worktree_id") != binding["worktree_id"]
            or settled.get("disposition") != "released"
        ):
            raise TaskError("native creator settlement did not confirm release")
    except (OSError, ValueError, TaskError) as exc:
        return {**result, "claimNote": str(exc)}
    return {**result, "claimSettled": True}


def _capture_result(process: Any) -> dict[str, Any]:
    from .queue_records import TaskError

    if process is None:
        raise TaskError("native creator claim owner is unavailable")
    try:
        result = json.loads(process.stdout)
    except (TypeError, ValueError) as exc:
        raise TaskError("native creator claim owner returned an invalid response") from exc
    if process.returncode or not isinstance(result, dict) or result.get("error"):
        raise TaskError(
            str(result.get("error", "native creator claim operation failed"))
            if isinstance(result, dict) else "native creator claim operation failed"
        )
    return result


def terminal_rejection(task: dict[str, Any] | None, task_id: str) -> str | None:
    """Keep unknown, live and execution-bearing abandonment protected."""
    if not is_handoff_task(task) or (task or {}).get("id") != task_id:
        return "authoritative handoff task could not be confirmed"
    assert task is not None
    if task.get("status") == "completed":
        return None
    if task.get("status") != "abandoned":
        return "handoff task is not confirmed completed or abandoned"
    if (
        task.get("attempts") != 0 or task.get("owner") or task.get("owner_session_id")
        or task.get("started_at") is not None or task.get("claimed_at") is not None
    ):
        return "abandoned handoff has execution history; creator obligation remains protected"
    return None


def reconcile_claim(
    queue: TaskQueue, task_id: str, *, binding: object = None, owner_ref: str | None = None,
) -> dict[str, Any]:
    """Reconcile the current task only; never replay abandon or release by leaf ID."""
    from .handoff_claim_binding import bind_claim
    from .procutil import run_agent_worktrees_capture
    from .queue_records import TaskError

    current = queue.get(task_id)
    if current is None:
        raise TaskError(f"no such task: {task_id}")
    task_id = current.id
    if binding is not None:
        current = bind_claim(queue, task_id, binding)
    if owner_ref is not None:
        if current.handoff_claim is not None:
            from .handoff_claim_binding import validated_binding
            selected = validated_binding(current.handoff_claim, asdict(current))
            expected_owner = f'{selected.get("machine")}/{selected.get("project")}/{selected.get("worktree_id")}'
            if expected_owner != owner_ref:
                raise TaskError("explicit owner differs from the recorded creator binding")
        else:
            if rejection := terminal_rejection(asdict(current), task_id):
                raise TaskError(rejection)
            parts = owner_ref.split("/")
            if (
                len(parts) != 3 or not all(parts)
                or parts[2] != handoff_worktree(asdict(current))
                or current.target_machine and parts[0].casefold() != current.target_machine.casefold()
            ):
                raise TaskError("explicit creator owner does not match the handoff target")
            observed = _capture_result(run_agent_worktrees_capture(
                "claims", "binding", task_id, "--owner-ref", owner_ref, "--apply", "--json", timeout=15.0,
            ))
            current = bind_claim(queue, task_id, observed.get("claim_binding"))
    current = queue.get(task_id)
    task = asdict(current) if current is not None else None
    return {
        "id": task_id, "taskStatus": (task or {}).get("status"),
        "claimBinding": (task or {}).get("handoff_claim"),
        **_release_task_claim(task_id, worktree=handoff_worktree(task), task=task),
    }
