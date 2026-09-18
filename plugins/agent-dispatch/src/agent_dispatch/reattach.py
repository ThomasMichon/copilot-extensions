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
same ``dedup_key`` (create -> reserve_spawn -> record_spawn -> claim -> start,
so the new task carries a real ``spawn_reservation`` and not just a bare
``owner`` string -- the thing doctor's and the abandon guard's liveness
checks actually read), claims it under the live session's recovery-handle
identity (``local-body:<session>`` / ``fleet-body:<host>:<session>``, the
same convention :mod:`agent_dispatch.spawn_factories` decodes), starts it,
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

from . import bridge, bridge_remote
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
    session -- used as the claim owner, the persisted reservation
    ``session_handle``, and the liveness-probe handle, so all three always
    agree on exactly which body a reattach targets.

    ``host`` is normalized (case/whitespace) *here*, at the single point
    that mints the handle, rather than downstream at delivery time -- an
    un-normalized alias would otherwise be baked into the persisted
    ``session_handle`` and the resume prompt's literal ``--worker-id`` text
    (which a leading/trailing space would split at, corrupting the CLI
    invocation the prompt tells the worker to run).
    """
    if host is None:
        return f"local-body:{session_id}"
    return f"fleet-body:{bridge_remote.normalize_host(host)}:{session_id}"


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
        f"started under {worker_id} (a synthetic recovery-handle owner, not "
        "your CWD's machine/worktree identity, so you MUST pass "
        f"`--worker-id {worker_id}` explicitly on every owner-gated command -- "
        f"`agent-dispatch progress {task_id} {worker_id} --summary ...`, "
        f"`agent-dispatch complete {task_id} {worker_id} --result-ref ...`, "
        "etc. -- an omitted owner resolves from CWD identity instead and will "
        f"fail). Read it with `agent-dispatch show {task_id}` (its prompt "
        "embeds any recovered prior progress log/steer answers) and continue "
        "the work from where you left off."
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
    there -- see the module docstring), it actually carries a ``dedup_key``
    to re-mint against, and it is **not** producer-managed (a
    ``producer_fence`` can't be safely replayed here -- see below).

    On success: retire the source task's own stale reservation when it
    shares an ``exclusive_key`` with the new one (``reserve_spawn`` checks
    active reservations for that key across *every* task, and an
    abandoned/dead-lettered task's reservation is never auto-released --
    see below); create the new task **pinned to an unclaimable synthetic
    target** (``target_worktree`` = the recovery-handle itself, which no
    ordinary worker ever advertises -- closing the claim-race window between
    ``create`` and this function's own ``claim`` call below), carrying
    forward the source task's full metadata (payload, requires/excludes/
    affinity, ``exclusive_key``, source/origin/evaluator refs) plus its
    progress log and answered steer folded into the new task's own prompt
    via :func:`_recovered_context` (neither transfers automatically);
    ``reserve_spawn`` + ``record_spawn`` (so the new task carries a real
    ``spawn_reservation`` with the recovery-handle as its ``session_handle``
    -- the thing doctor's and the abandon guard's liveness checks actually
    read; a bare ``owner`` string alone is not liveness-tracked); then
    ``claim`` (task-id-directed, under the recovery-handle owner, at the
    same pinned target so the gate matches), ``start``, and
    ``bind_owner_session`` (the exact session id those same checks key on)
    -- then, unless ``resume=False``, deliver a resume prompt via
    agent-bridge so the live session picks its new task straight back up.
    ``resumed`` reports whether that delivery itself succeeded; a ``False``
    here does not undo the reattach -- the new task is claimed and started
    either way, and an operator can always deliver the prompt by hand
    (``agent-bridge resume <session_id>``).

    **Why not producer-managed tasks:** a generation-managed producer (or a
    managed required label) fences creation with a ``producer_fence`` tied to
    a specific producer generation and request id; replaying that fence
    verbatim would silently reuse the old request id, and omitting it would
    make ``create`` reject the reattach outright. Neither is safe to do
    implicitly, so a producer-managed source task is rejected up front,
    before any mutation.

    **Known limitation (abandon --override-live race):** overriding the
    abandon guard leaves the source task's reservation active; supervisor
    reconciliation may treat that terminal task's still-active reservation
    as cleanup work and end its body before an operator gets to reattach.
    The liveness check is ordered as late as reasonably possible (after the
    cheap validation reads, immediately before the exclusive_key retirement
    and ``create``/``reserve_spawn``/``claim`` sequence) to minimize -- there
    is no server-side atomic fence available to eliminate -- the window
    between "confirmed live" and "actually bound"; reattach promptly after
    an override-live abandon.

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
    normalized_host = bridge_remote.normalize_host(host) if host is not None else None
    # Validation-only reads first (no dependency on session_id's liveness),
    # so the liveness check below sits as close as possible to the mutating
    # sequence it gates -- minimizing (never eliminating; there is no
    # server-side atomic fence for this) the window between "confirmed live"
    # and "actually bound".
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
    if old_task.get("producer_fence"):
        raise ReattachError(
            f"task {task_id!r} is producer-managed (carries a producer_fence) -- "
            "reattach cannot safely replay that fence (it would reuse the old "
            "request id) or safely omit it (create would reject the reattach); "
            "recover it through its owning producer instead"
        )
    recovered = _recovered_context(client, task_id)
    new_prompt = old_task.get("prompt") or ""
    if recovered:
        new_prompt = f"{new_prompt}\n\n{recovered}" if new_prompt else recovered
    verdict, _, _ = session_handle_verdict(
        worker_id, local_verdict=local_session_verdict, fleet_verdict=fleet_session_verdict
    )
    if verdict != SESSION_LIVE:
        raise ReattachError(
            f"session {session_id!r} is not confirmed live ({verdict}); "
            "refusing to reattach a task into a dead/unknown session"
        )
    exclusive_key = old_task.get("exclusive_key")
    if exclusive_key:
        # `reserve_spawn` fences an `exclusive_key` across EVERY task sharing
        # it, not only the new one -- a terminal source task's reservation is
        # never auto-released, so without retiring it here, the new task's
        # own reserve_spawn below would find the stale reservation still
        # ACTIVE and abort before claiming anything.
        old_reservation = old_task.get("spawn_reservation") or {}
        old_key = old_reservation.get("key")
        if old_key:
            client.fail_spawn(
                old_key,
                detail=(
                    f"reattach: retiring stale reservation for exclusive_key "
                    f"{exclusive_key!r} (superseded by a fresh reattach)"
                ),
            )
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
        exclusive_key=exclusive_key,
        source=old_task.get("source"),
        origin_ref=old_task.get("origin_ref"),
        evaluator_ref=old_task.get("evaluator_ref"),
        goal=old_task.get("goal"),
        done_criteria=old_task.get("done_criteria"),
        # Pinned to the recovery handle itself (never the source task's own
        # target) -- no ordinary worker advertises this synthetic worktree,
        # so nothing can claim the row in the create -> reserve_spawn ->
        # claim window below except this function's own matching claim call.
        target_worktree=worker_id,
        target_repo=old_task.get("target_repo"),
    )
    new_task_id = new_task["id"]
    if new_task.get("status") != Status.QUEUED or new_task.get("owner") is not None:
        raise ReattachError(
            f"lost the dedup race for {dedup_key!r}: task {new_task_id!r} is "
            f"already {new_task.get('status')!r} (owner "
            f"{new_task.get('owner')!r})"
        )
    reservation = client.reserve_spawn(new_task_id, reserved_by=worker_id)
    if not reservation.get("reserved"):
        raise ReattachError(
            f"could not reserve a spawn for freshly re-minted task {new_task_id!r} "
            "-- an active reservation already exists (unexpected for a brand-new "
            "task; investigate before retrying)"
        )
    reservation_key = reservation["reservation"]["key"]
    client.record_spawn(reservation_key, session_handle=worker_id)
    claimed = client.claim(
        worker_id=worker_id,
        capabilities=old_task.get("requires") or [],
        repo=old_task.get("repo"),
        machine=None,
        worktree=worker_id,
        task_id=new_task_id,
    )
    if claimed is None:
        client.fail_spawn(
            reservation_key, detail="reattach: claim failed after spawn reservation"
        )
        raise ReattachError(
            f"reserved a spawn for task {new_task_id!r} but could not claim it "
            "(gate mismatch on requires/target after carrying the source task's "
            "metadata forward) -- the reservation was failed so a future attempt "
            "can retry"
        )
    client.start(new_task_id, worker_id)
    client.bind_owner_session(new_task_id, worker_id, session_id)
    resumed = False
    if resume:
        resumed = resume_fn(
            session_id, prompt or _reattach_prompt(new_task_id, worker_id),
            host=normalized_host,
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
