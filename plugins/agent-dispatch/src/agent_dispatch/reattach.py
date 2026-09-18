"""Phase 9 reattach primitive (ThomasMichon/copilot-extensions#2884):
formalize the manual ``create`` (same ``dedup_key``) -> ``claim`` -> ``start``
-> ``agent-bridge resume``/``send`` sequence used to recover a task whose
tracked reservation is dead while an earlier attempt's embody session is
still alive and fully resumable.

Background: a task's ``dedup_key`` only releases from the create-dedup index
once the task reaches a **terminal** status (see
:data:`agent_dispatch.queue_records.Status.TERMINAL` and
``TaskQueue.create``'s docstring) -- so this primitive is viable only against
an *already*-terminal task, typically one just abandoned for exactly this
reason (see :func:`guard_abandon_liveness` and ``agent-dispatch abandon
--override-live``). :func:`reattach` re-mints the same logical work under the
same ``dedup_key``, atomically claims it under the live session's recovery-
handle identity (``local-body:<session>`` / ``fleet-body:<host>:<session>``,
the same convention :mod:`agent_dispatch.spawn_factories` decodes), starts it,
binds the exact session id for liveness tracking
(``DispatchClient.bind_owner_session``), and -- unless disabled -- delivers a
resume prompt so the live session picks its new task straight back up. This
never spawns a new session: it only ever reattaches an already-live one,
confirmed by liveness probe before anything is created.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import bridge
from .client import DispatchError
from .doctor import (
    SESSION_LIVE,
    _default_fleet_session_verdict,
    _default_local_session_verdict,
    session_handle_verdict,
)
from .queue_records import Status

if TYPE_CHECKING:
    from .client import DispatchClient


def add_abandon_override_live_argument(p: argparse.ArgumentParser) -> None:
    """Add ``abandon --override-live`` (Phase 9 liveness guard escape hatch).

    Kept alongside :func:`guard_abandon_liveness`/:func:`cli_abandon` (not in
    ``__main__.py``'s ``build_parser``) to keep that module's CLI-wiring diff
    thin -- see ``tools/check-module-size.py``.
    """
    p.add_argument(
        "--override-live",
        action="store_true",
        help="skip the liveness guard (Phase 9): by default, abandon refuses "
        "when the task's current reservation session is confirmed live, "
        "since that would discard the only tracking link to a still-good "
        "session (resume it, or `agent-dispatch reattach` it, instead)",
    )


def build_reattach_subparser(
    sub: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Build the ``reattach`` subparser (caller still wires ``func``)."""
    p = sub.add_parser(
        "reattach",
        help="reattach a terminal task's still-live session to a fresh "
        "tracked task -- formalizes create(same dedup_key)+claim+start+"
        "bind_owner_session+resume (Phase 9 / copilot-extensions#2884)",
    )
    p.add_argument("task_id", help="the terminal task to re-mint")
    p.add_argument("session_id", help="the confirmed-live agent-bridge session to reattach")
    p.add_argument(
        "--host", help="the fleet pool host the session lives on; omit for a local body",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="claim/start/bind only -- skip delivering the agent-bridge resume "
        "prompt (deliver it by hand instead)",
    )
    return p


class ReattachError(RuntimeError):
    """Raised when a task cannot be safely reattached to a resumed session."""


@dataclass(frozen=True)
class ReattachResult:
    """The outcome of one successful :func:`reattach` call."""

    task_id: str
    worker_id: str
    session_id: str
    resumed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "session_id": self.session_id,
            "resumed": self.resumed,
        }


def _handle_for(session_id: str, host: str | None) -> str:
    """The ``local-body:``/``fleet-body:`` recovery-handle string for a
    session -- used both as the atomic create-and-claim owner and as the
    liveness-probe handle, so both always agree on exactly which body a
    reattach targets."""
    return f"fleet-body:{host}:{session_id}" if host else f"local-body:{session_id}"


def _recovered_context(client: DispatchClient, old_task_id: str) -> str:
    """Render the source task's progress log + answered steer as inline text.

    Neither transfers through ``create()`` -- ``task_progress``/``task_steer``
    rows are keyed to the *old* ``task_id``, and ``create`` deliberately mints
    a brand-new one -- so this is the mechanism that actually carries them
    forward: folded into the new task's own ``prompt`` (durable, visible via
    ``show``), rather than only referenced from the resume prompt. Best-effort:
    a fetch failure (:class:`DispatchError`) drops that section rather than
    blocking the reattach on enrichment.
    """
    sections: list[str] = []
    try:
        progress = client.progress_log(old_task_id)
    except DispatchError:
        progress = []
    if progress:
        lines = [
            f"- [{entry.get('phase') or '(no phase)'}] {entry.get('summary', '')}"
            + (f" -- {entry['detail']}" if entry.get("detail") else "")
            for entry in progress
        ]
        sections.append("Prior progress log (from task {}):\n{}".format(
            old_task_id, "\n".join(lines)
        ))
    try:
        steers = client.steer_log(old_task_id)
    except DispatchError:
        steers = []
    if steers:
        lines = [f"- {entry.get('fields')}" for entry in steers]
        sections.append("Prior steer answers (from task {}):\n{}".format(
            old_task_id, "\n".join(lines)
        ))
    return "\n\n".join(sections)


def _reattach_prompt(task_id: str, worker_id: str) -> str:
    return (
        f"You have been resumed to reattach to agent-dispatch task {task_id} "
        f"(worker id: {worker_id}), recovering from a prior attempt whose "
        "tracked reservation died -- your own session and its work were never "
        "affected and remain intact. Use the payload-local `agent-dispatch` "
        "CLI (no `--url`) for each command; this task is already claimed and "
        f"started under you. Read it with `agent-dispatch show {task_id}` "
        "(its prompt embeds any recovered prior progress log/steer answers) "
        "and continue the work from where you left off."
    )


def reattach(
    client: DispatchClient,
    task_id: str,
    session_id: str,
    *,
    host: str | None = None,
    prompt: str | None = None,
    resume: bool = True,
    local_session_verdict: Callable[[str], str] | None = None,
    fleet_session_verdict: Callable[[str, str], str] | None = None,
    resume_fn: Callable[..., bool] | None = None,
) -> ReattachResult:
    """Reattach a terminal task's still-live session to a fresh tracked task.

    Refuses (:class:`ReattachError`) unless: ``session_id`` is itself
    confirmed live right now (never reattach into a dead/unknown session --
    the whole point is recovering a session that is genuinely still there),
    the source task is genuinely terminal (a ``dedup_key`` only re-mints
    there -- see the module docstring), and it actually carries a
    ``dedup_key`` to re-mint against.

    On success: atomic create-and-claim (under the recovery-handle owner,
    carrying forward the source task's full metadata -- payload, requires/
    excludes/affinity, source/origin/evaluator refs -- plus its progress log
    and answered steer folded into the new task's own prompt via
    :func:`_recovered_context`, since neither transfers automatically),
    ``start``, ``bind_owner_session`` (the exact id doctor's and the abandon
    guard's liveness checks key on) -- then, unless ``resume=False``,
    deliver a resume prompt via agent-bridge so the live session picks its
    new task straight back up. ``resumed`` reports whether that delivery
    itself succeeded; a ``False`` here does not undo the reattach -- the new
    task is claimed and started either way, and an operator can always
    deliver the prompt by hand (``agent-bridge resume <session_id>``).

    The three probe/delivery callables default to ``None`` and are resolved
    to their module-attribute defaults *inside* the function body -- an
    explicit, call-time lookup (not a def-time-bound default parameter
    value), the same fix ``doctor.diagnose_many`` needed so a
    ``monkeypatch.setattr`` on the module actually takes effect for callers
    that don't override these explicitly.
    """
    local_session_verdict = local_session_verdict or _default_local_session_verdict
    fleet_session_verdict = fleet_session_verdict or _default_fleet_session_verdict
    resume_fn = resume_fn or bridge.resume_session
    worker_id = _handle_for(session_id, host)
    verdict, _, _ = session_handle_verdict(
        worker_id, local_verdict=local_session_verdict, fleet_verdict=fleet_session_verdict
    )
    if verdict != SESSION_LIVE:
        raise ReattachError(
            f"session {session_id!r} is not confirmed live ({verdict}); "
            "refusing to reattach a task into a dead/unknown session"
        )
    old_task = client.get(task_id)
    if old_task.get("status") not in Status.TERMINAL:
        raise ReattachError(
            f"task {task_id!r} is {old_task.get('status')!r}, not terminal -- "
            "reattach only re-mints a terminal task's dedup_key (abandon it "
            "first if it should stop tracking its current dead reservation)"
        )
    dedup_key = old_task.get("dedup_key")
    if not dedup_key:
        raise ReattachError(
            f"task {task_id!r} has no dedup_key -- nothing to safely re-mint "
            "the same logical work under"
        )
    recovered = _recovered_context(client, task_id)
    new_prompt = old_task.get("prompt") or ""
    if recovered:
        new_prompt = f"{new_prompt}\n\n{recovered}" if new_prompt else recovered
    new_task = client.create(
        old_task.get("title") or "",
        repo=old_task.get("repo"),
        prompt=new_prompt,
        dedup_key=dedup_key,
        requires=old_task.get("requires") or [],
        excludes=old_task.get("excludes") or [],
        affinity=old_task.get("affinity") or {},
        labels=old_task.get("labels") or [],
        payload_ref=old_task.get("payload_ref"),
        payload_inline=old_task.get("payload_inline"),
        source=old_task.get("source"),
        origin_ref=old_task.get("origin_ref"),
        evaluator_ref=old_task.get("evaluator_ref"),
        goal=old_task.get("goal"),
        done_criteria=old_task.get("done_criteria"),
        target_machine=old_task.get("target_machine"),
        target_worktree=old_task.get("target_worktree"),
        target_repo=old_task.get("target_repo"),
        claim_as=worker_id,
    )
    if new_task.get("owner") != worker_id:
        raise ReattachError(
            f"lost the dedup race for {dedup_key!r}: task {new_task.get('id')!r} "
            f"is already claimed by {new_task.get('owner')!r}"
        )
    new_task_id = new_task["id"]
    client.start(new_task_id, worker_id)
    client.bind_owner_session(new_task_id, worker_id, session_id)
    resumed = False
    if resume:
        resumed = resume_fn(
            session_id, prompt or _reattach_prompt(new_task_id, worker_id), host=host,
        )
    return ReattachResult(
        task_id=new_task_id, worker_id=worker_id, session_id=session_id, resumed=resumed,
    )


def guard_abandon_liveness(
    task: dict[str, Any],
    *,
    local_verdict: Callable[[str], str] | None = None,
    fleet_verdict: Callable[[str, str], str] | None = None,
) -> str | None:
    """Return a refusal message iff ``task``'s *current* reservation session
    is confirmed live (Phase 9's abandon guard, #2884) -- else
    ``None`` (safe to abandon).

    Checks only the task's current reservation (the same
    ``spawn_reservation.session_handle`` doctor reads), never its full
    history -- an abandon is a judgment about the task's live owner right
    now, not about a shadowed earlier attempt (that is
    ``doctor --check-live-sessions``'s ``earlier_attempt_live``, a separate,
    non-blocking signal). An unknown or gone verdict never blocks: only a
    *confirmed*-live session does, since that is the one case that would
    otherwise discard the only tracking link to a still-good session.

    ``local_verdict``/``fleet_verdict`` default to ``None``, resolved to
    their module-attribute defaults inside the body (a call-time lookup, not
    a def-time-bound default) -- see :func:`reattach`'s docstring for why.
    """
    reservation = task.get("spawn_reservation") or {}
    session_handle = reservation.get("session_handle")
    if not session_handle:
        return None
    verdict, session_id, host = session_handle_verdict(
        session_handle,
        local_verdict=local_verdict or _default_local_session_verdict,
        fleet_verdict=fleet_verdict or _default_fleet_session_verdict,
    )
    if verdict != SESSION_LIVE:
        return None
    where = f" on host {host!r}" if host else ""
    return (
        f"task {task.get('id')!r}'s current session {session_id!r} is "
        f"confirmed live{where} -- abandoning it now would discard the only "
        "tracking link to a still-good session. Resume it directly (`agent-"
        "bridge resume`), or reattach once it IS terminal (`agent-dispatch "
        "reattach`), or pass --override-live to abandon anyway."
    )


class AbandonRefused(RuntimeError):
    """Raised by :func:`cli_abandon` when the liveness guard blocks."""


def cli_abandon(client: DispatchClient, args: argparse.Namespace) -> dict[str, Any]:
    """The full ``agent-dispatch abandon`` CLI flow: the Phase 9 liveness
    guard, the self-justifying ``--duplicate-of`` permission grant, the
    abandon call itself, and an optional resolution-plan attachment.

    Raises :class:`AbandonRefused` when the liveness guard blocks (not
    ``--override-live``, and the task's current reservation session is
    confirmed live) -- takes the parsed CLI namespace directly (rather than
    a long kwarg list) to keep ``__main__._cmd_abandon`` a thin one-line
    dispatch shim (see ``tools/check-module-size.py``).
    """
    permitted = args.permit
    reason = args.reason
    duplicate_of = getattr(args, "duplicate_of", None)
    if not getattr(args, "override_live", False):
        refusal = guard_abandon_liveness(client.get(args.task_id))
        if refusal:
            raise AbandonRefused(refusal)
    if duplicate_of:
        # A duplicate is self-justifying: retiring it is permitted, and the
        # dedup reference is folded into the reason so it lands in the audit
        # trail (never a silent drop).
        permitted = True
        dedup_note = f"duplicate of {duplicate_of}"
        reason = f"{reason}; {dedup_note}" if reason else dedup_note
    result = client.abandon(
        args.task_id, worker_id=args.worker_id, permitted=permitted, reason=reason
    )
    if getattr(args, "resolve", False):
        # Surface the drive-the-worktree-to-resolution plan alongside the abandon
        # so the required unwind is an explicit, actionable expectation -- never a
        # silent one. It is NOT auto-run: the destructive unwind stays worker-
        # driven (`agent-dispatch resolve --execute`), on the worker's OWN tree.
        from .resolution import plan_resolution

        plan = plan_resolution(
            "abandoned", base=getattr(args, "base", None), source_ref=duplicate_of, reason=reason,
        )
        result = {"abandon": result, "resolution": plan.to_dict()}
    return result
