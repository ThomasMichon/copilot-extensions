"""Phase 1 item 3: the interactive-embodiment transaction.

A new, coordinator-owned, atomic operation (never a bare picker navigation
verb) that opens an *interactive*, CLI-backed session an operator watches or
drives -- the "Open into a CLI session" action's real backend, and the
non-negotiable other half of Phase 1's ``cli_openable`` contract alongside
item 2's auto-suspend-on-dead-liveness.

Confirmed flow (operator-approved design, see the effort's Runbook/Journal):

1. Create a new worktree if the task has none, else resolve/reuse its
   existing one -- delegated to :func:`agent_dispatch.embody
   .prepare_reusable_worktree`, the SAME resolve-or-create logic
   ``Supervisor`` itself uses for a reusable-worktree spawn backend
   (:meth:`Supervisor._prepare_spawn_task`).
2. Bind that worktree to the task and auto-claim/resume it under the
   task's own worktree identity (``worker_id_for(machine, worktree)``) --
   the exact same ``claim``/``start``/``resume`` primitives (and their
   generation/owner-session CAS fencing) a normal pool claim uses.
3. Compose the succinct, non-railroaded ``--interactive`` seed
   (:func:`agent_dispatch.embody_prompts.interactive_worker_prompt`) --
   never a worker identity or pool/recipe framing.
4. Launch it via :func:`agent_dispatch.embody.spawn_embodied_worker`'s
   existing CLI-backed autopilot session mechanism (a new *seed*/path
   through it, not a new session-launch primitive), then record the
   resulting session handle on the spawn reservation.

Never resolves a pool or a named worker identity (:mod:`agent_dispatch
.worker_identities`) at any point -- the reservation this transaction takes
is the SAME at-most-one-in-flight primitive every embodiment path uses, not
a pool assignment.

**Ordering of step 2 relative to step 4 (bind vs. launch) deliberately
differs by starting status**, because ``owner_session_id`` -- the identity
item 2's liveness GC keys on -- is only known once the Copilot session in
step 4 actually exists, and only a **queued** task has a real "someone else
claims it first" race to close:

- A fresh **queued** task IS claimable by a concurrent pool worker, so
  ownership is split in two: ``claim`` (worktree identity only, no session
  yet) happens *before* the launch -- removing it from the claimable pool --
  and ``start(..., owner_session_id=<new session>)`` finalizes it
  *afterward*, once the session exists.
- A **suspended** task is not claimable by anyone (``claim`` only ever picks
  from ``queued``), so there is no race to close by binding early. Its
  single-step ``resume(..., adopt_owner_session_id=...)`` transition needs
  the new session id anyway, so it runs *after* the launch, atomically
  adopting the fresh session in one step.

**Failure/recovery story:**

- *Worktree preparation fails* (create_worktree/resolve_worktree/agent-
  worktrees unavailable): the spawn reservation is released (``fail_spawn``)
  before the error propagates; no Copilot process was ever launched.
- *A queued task's pre-launch claim loses the race* (another pool worker
  claimed it first): released the same way, before any launch is attempted.
- *The launch itself fails, exits nonzero, or reports no session id*: the
  reservation is released, and -- for the queued path only, which already
  moved the task to ``claimed`` before attempting the launch -- the claim
  itself is undone (``yield_task``) so the task returns to ``queued``
  rather than being stranded ``claimed`` with no session and no active
  reservation (a state item 2's liveness GC cannot recover on its own: it
  never escalates an uncaptured ``owner_session_id`` to ``gone``). The
  suspended/resume path never claims anything pre-launch, so it has nothing
  to undo here.
- *Launch succeeds but the follow-up start/resume bind fails* (a resume
  generation race, or a held task discovered only at bind time): the task is
  left with a live, unbound Copilot session and no ownership -- annoying to
  clean up by hand but not a correctness hazard, since nothing else can earn
  ownership of a ``suspended``/``claimed`` task out from under this failure
  without its own explicit action. This is the one edge this transaction
  does not self-heal automatically; a future revision could have the
  launched session itself detect its own unbound state and retry the bind.
"""

from __future__ import annotations

import subprocess
import uuid
from typing import Any, Protocol

from . import embody
from .embody_prompts import interactive_worker_prompt
from .queue import worker_id_for
from .queue_records import Status

#: Statuses the transaction will operate on. A ``proposed`` task is approved
#: to ``queued`` first (opening it into an interactive session is itself an
#: implicit approval); anything else (``claimed``/``started`` with a live
#: owner, a terminal status) is not eligible -- see the Plan's
#: ``cli_openable`` predicate, which this transaction is the action behind.
_ELIGIBLE_AFTER_APPROVAL = frozenset({Status.QUEUED, Status.SUSPENDED})

#: The supervisor id stamped on reservations this transaction creates, so a
#: `spawn_reservations` row is legible as interactive-embodiment-owned in
#: `agent-dispatch reservations list` output, distinct from a pool supervisor.
DEFAULT_SUPERVISOR_ID = "agent-dispatch-interactive"


class InteractiveEmbodimentError(RuntimeError):
    """Raised when the interactive-embodiment transaction cannot proceed."""


class _Client(Protocol):
    """The narrow slice of :class:`agent_dispatch.client.DispatchClient` (or
    an equivalent local adapter over :class:`agent_dispatch.queue.TaskQueue`)
    this transaction depends on. A structural ``Protocol`` keeps this module
    free of a hard import-time dependency on ``client.py``."""

    def get(self, task_id: str) -> dict: ...

    def approve(self, task_id: str) -> dict: ...

    def reserve_spawn(
        self, task_id: str, *, reserved_by: str | None = None,
        allow_suspended_reembodiment: bool = False,
    ) -> dict: ...

    def record_spawn_worktree(self, key: str, worktree: str, **kwargs: Any) -> dict: ...

    def record_spawn(
        self, key: str, *, session_handle: str | None = None, worktree: str | None = None
    ) -> dict: ...

    def fail_spawn(self, key: str, *, detail: str | None = None) -> dict: ...

    def claim(
        self,
        *,
        task_id: str,
        machine: str | None = None,
        worktree: str | None = None,
        repo: str | None = None,
        all_repos: bool = False,
    ) -> dict | None: ...

    def start(
        self, task_id: str, worker_id: str, *, owner_session_id: str | None = None
    ) -> dict: ...

    def resume(
        self,
        task_id: str,
        worker_id: str,
        *,
        wake: bool = True,
        adopt_owner_session_id: str | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> dict: ...

    def yield_task(
        self,
        task_id: str,
        worker_id: str,
        *,
        note: str | None = None,
        release_spawn: bool = True,
    ) -> dict: ...


def _unwrap_reservation(result: Any) -> tuple[dict, bool]:
    """Unwrap a ``reserve_spawn`` result to ``(reservation, reserved)``.

    Accepts the real client's ``{"reserved": bool, "reservation": {...}}``
    shape or a bare reservation dict (a minimal test double that always owns
    what it returns, so it degrades to ``reserved=True``)."""
    if isinstance(result, dict) and "reservation" in result:
        return result["reservation"], bool(result.get("reserved"))
    return result, True


def _release_prelaunch_claim(
    client: _Client, task_id: str, worker_id: str, *, resuming: bool
) -> None:
    """Undo the pre-launch ``claim`` (queued path only) when the launch itself
    fails, so a failed embody attempt never leaves the task stuck ``claimed``
    with no session and no active reservation -- a state item 2's liveness GC
    cannot recover on its own (it never escalates an uncaptured
    ``owner_session_id`` to ``gone``). A resuming (suspended) task never
    claimed anything pre-launch, so there is nothing to release here; its own
    failure paths already fully explain themselves (see the module
    docstring). Best-effort: a failure here is logged by the caller's own
    exception chain, never masks the original error.
    """
    if resuming:
        return
    try:
        client.yield_task(
            task_id, worker_id,
            note="interactive-embodiment launch failed before session started",
            release_spawn=False,
        )
    except Exception:  # best-effort cleanup -- never mask the original error
        pass


def launch_interactive_embodiment(
    client: _Client,
    task_id: str,
    *,
    machine: str,
    driver: str = DEFAULT_SUPERVISOR_ID,
    interface: str = "cli",
    project: str | None = None,
    supervisor: str = DEFAULT_SUPERVISOR_ID,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run the interactive-embodiment transaction for one task.

    Returns ``{"task_id", "worktree", "session", "worker_id", "task"}`` on
    success. Raises :class:`InteractiveEmbodimentError` on any failure (an
    ineligible status, a lost claim/resume race, a held task, or
    ``agent-worktrees``/embody unavailability) -- always after releasing any
    spawn reservation this call itself took, except for the one edge
    documented in the module docstring (launch succeeds but the follow-up
    bind fails).
    """
    task = client.get(task_id)
    status = task.get("status")
    if status == Status.PROPOSED:
        task = client.approve(task_id)
        status = task.get("status")
    if status not in _ELIGIBLE_AFTER_APPROVAL:
        raise InteractiveEmbodimentError(
            f"task {task_id!r} is {status!r}; interactive embodiment requires "
            "proposed, queued, or suspended"
        )
    expected_generation = task.get("generation")
    expected_owner_session_id = task.get("owner_session_id")
    resuming = status == Status.SUSPENDED

    reservation, reserved = _unwrap_reservation(
        client.reserve_spawn(
            task_id, reserved_by=supervisor,
            allow_suspended_reembodiment=resuming,
        )
    )
    if not reserved:
        # An active reservation already exists for this task (another
        # concurrent open, or a not-yet-settled prior attempt) -- the
        # client contract is explicit that the caller must NOT spawn in
        # this case. Proceeding would overwrite/steal that reservation's
        # own session handle out from under whatever owns it.
        raise InteractiveEmbodimentError(
            f"task {task_id!r} already has an active spawn reservation "
            f"({reservation.get('key')!r}, state {reservation.get('state')!r}); "
            "refusing to spawn a second session"
        )
    key = reservation["key"]

    resolved_project = project or embody.project_for_task(task)
    try:
        prepared = embody.prepare_reusable_worktree(
            task,
            reservation,
            project=resolved_project,
            interface=interface,
            driver=driver,
            supervisor=supervisor,
            timeout=timeout,
        )
    except (embody.EmbodyUnavailable, OSError, subprocess.TimeoutExpired) as exc:
        # `create_worktree`/`resolve_worktree` (unlike embody's own launch
        # helpers) don't wrap a raw subprocess failure into
        # `EmbodyUnavailable` themselves -- catch the underlying exceptions
        # too so a missing/unlaunchable `agent-worktrees` binary or a stuck
        # subprocess still releases the reservation instead of leaving it
        # stuck `reserving` (PR #2913 review).
        client.fail_spawn(key, detail=str(exc)[:200])
        raise InteractiveEmbodimentError(str(exc)) from exc

    worktree_id = str(prepared["worktree"])
    ownership = str(prepared.get("ownership") or "unknown")
    if (
        reservation.get("worktree") != worktree_id
        or reservation.get("worktree_ownership") != ownership
    ):
        client.record_spawn_worktree(key, worktree_id, ownership=ownership)

    worker_id = worker_id_for(machine, worktree_id)

    # A queued task IS claimable by a concurrent pool worker -- close that
    # race by claiming (worktree identity only, no session known yet)
    # BEFORE launching. A suspended task has no such race (claim only ever
    # picks from queued), so its ownership bind is deferred until after the
    # launch, where it can atomically adopt the new session in one step.
    if not resuming:
        try:
            claimed = client.claim(
                task_id=task_id, machine=machine, worktree=worktree_id,
                repo=task.get("repo"),
            )
        except Exception as exc:
            client.fail_spawn(key, detail=str(exc)[:200])
            raise InteractiveEmbodimentError(str(exc)) from exc
        if claimed is None:
            client.fail_spawn(key, detail="claim lost the race before launch")
            raise InteractiveEmbodimentError(
                f"task {task_id!r} could not be claimed under {worker_id!r} "
                "(claimed by another owner, or no longer queued)"
            )

    seed = interactive_worker_prompt(task_id, status=status)
    try:
        result = embody.spawn_embodied_worker(
            task_id,
            worker_id=f"embody-{uuid.uuid4().hex[:8]}",
            driver=driver,
            project=resolved_project,
            worktree_id=worktree_id,
            seed=seed,
            timeout=timeout,
        )
    except embody.EmbodyUnavailable as exc:
        client.fail_spawn(key, detail=str(exc)[:200])
        _release_prelaunch_claim(client, task_id, worker_id, resuming=resuming)
        raise InteractiveEmbodimentError(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:200] or "embody exited nonzero"
        client.fail_spawn(key, detail=detail)
        _release_prelaunch_claim(client, task_id, worker_id, resuming=resuming)
        raise InteractiveEmbodimentError(detail)
    handle = embody.parse_handle(result)
    session_id = handle.get("session")
    if not session_id:
        client.fail_spawn(key, detail="embody reported success but returned no session id")
        _release_prelaunch_claim(client, task_id, worker_id, resuming=resuming)
        raise InteractiveEmbodimentError("embody launched but returned no session id")

    client.record_spawn(
        key, session_handle=session_id, worktree=handle.get("worktree") or worktree_id
    )

    try:
        if resuming:
            bound = client.resume(
                task_id, worker_id, wake=False,
                adopt_owner_session_id=session_id,
                expected_owner_session_id=expected_owner_session_id,
                expected_generation=expected_generation,
            )
        else:
            bound = client.start(task_id, worker_id, owner_session_id=session_id)
    except Exception as exc:
        # See the module docstring's failure story: a live, unbound session
        # is the one edge this transaction cannot self-heal automatically.
        raise InteractiveEmbodimentError(
            f"launched but failed to bind ownership: {exc}"
        ) from exc

    return {
        "task_id": task_id,
        "worktree": worktree_id,
        "session": session_id,
        "worker_id": worker_id,
        "task": bound,
    }
