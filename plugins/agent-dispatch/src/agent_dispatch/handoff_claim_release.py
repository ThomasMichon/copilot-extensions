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
instead targets the claim explicitly by the handoff task's own
``target_worktree`` field, via ``claims release <id> --worktree
<target_worktree>``.

**Known, best-effort limitation (project resolution):** ``--worktree`` alone
selects a worktree *id* within whichever *project* agent-worktrees resolves
for the invocation -- and that resolution still depends on the releasing
process's own cwd (or, for a cross-project owner, a qualified
``machine/project/worktree_id`` owner-ref that only the ``add``/``settle``
verbs currently accept, not ``release``). The coordinator's own cwd is
generally not any project's checkout, and there is no ``--project`` flag to
force the choice explicitly. On a machine with exactly one registered
project this degrades safely (the sole-project fallback resolves it
correctly); on a genuinely multi-project machine the release can silently
target the wrong project (or none) and leave the claim active. This is the
same cheap, safe failure mode already documented below -- an operator
notices the still-blocked finalize and runs the release manually with an
explicit, correct invocation -- not a regression of the forgotten-handoff gap
this module exists to close (the claim still exists and still blocks
finalize; it just isn't retired automatically in that configuration).
Properly closing this would need either a ``release --owner-ref`` verb
(mirroring ``add``/``settle``) or a project-qualified reference on the
handoff task itself -- tracked as follow-up, not attempted here.

**Fire-and-forget, never blocks the caller's own request.** ``release_if_handoff``
dispatches the actual (up to 15s) subprocess call on a background daemon
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
from typing import Any


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
    affinity = task.get("affinity") if isinstance(task.get("affinity"), dict) else {}
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
    cancel_pending: bool = False,
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
        kwargs={"worktree": worktree, "timeout": timeout, "cancel_pending": cancel_pending},
        daemon=True,
        name=f"handoff-claim-release-{resolved_id}",
    )
    thread.start()


def _release_in_background(task_id: str, target_machine: Any, *, worktree: str | None, timeout: float,
                           cancel_pending: bool) -> None:
    """The release thread's body. The ledger is the baton's own machine's, so
    this host cancels only its own (like the claim release, which runs here
    too); resolving this machine's name may shell out, so it happens here,
    never on the caller's request."""
    if cancel_pending and target_machine:
        cancel_pending = handoff_is_local({"target_machine": target_machine}, _this_machine())
    _release_task_claim(task_id, worktree=worktree, timeout=timeout, cancel_pending=cancel_pending)


def _release_task_claim(
    task_id: str, *, worktree: str | None, timeout: float = 15.0, cancel_pending: bool = False,
) -> None:
    """``agent-worktrees claims release <task_id>``, explicitly targeting
    ``worktree`` (via ``--worktree``) rather than the calling process's own
    cwd, when known.

    Shells via :func:`agent_dispatch.procutil.run_agent_worktrees_capture` --
    the same helper every other agent-worktrees spawn in this plugin uses --
    so this release gets the same scrubbed environment
    (:func:`agent_dispatch.procutil.agent_worktrees_environment`, which
    strips ``PYTHONHOME``/``PYTHONPATH``/``VIRTUAL_ENV``/
    ``__PYVENV_LAUNCHER__`` so the resolved agent-worktrees interpreter can't
    inherit the coordinator's own Python environment and load the wrong
    stdlib/venv) and the same no-console-window, leak-safe process handling.
    Best-effort and non-fatal: ``None`` back from the helper (CLI unavailable,
    spawn error, or timeout) is silently accepted, matching
    :func:`agent_dispatch.hibernation_claims.release_hibernation_claim`.

    Runs synchronously on whatever thread calls it -- :func:`release_if_handoff`
    is the fire-and-forget entry point; call this directly only from a
    context (e.g. a test, or an already-background thread) that intends to
    block on it.
    """
    from .procutil import run_agent_worktrees_capture

    argv = ["claims", "release", task_id, "--json"]
    if worktree:
        argv += ["--worktree", worktree]
    run_agent_worktrees_capture(*argv, timeout=timeout)
    if cancel_pending and worktree:
        run_agent_worktrees_capture(
            "cancel-handoff", "--worktree-id", worktree, "--token", task_id, timeout=timeout
        )
