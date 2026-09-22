"""Default spawn/liveness/conclusion callables and embody-backend factories.

Split out of :mod:`agent_dispatch.supervisor` (see that module's own docstring
for the invariant these functions serve). This module is deliberately
**stateless**: every function here is a pure default or a factory that closes
over only its own arguments -- none of it touches :class:`Supervisor` instance
state. That is what makes it a safe, low-risk extraction: nothing here is
monkeypatched via ``self``, and every name is re-exported unchanged from
``supervisor.py`` so existing call sites and tests (which patch
``agent_dispatch.supervisor.<name>``) are unaffected.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol


class SpawnPreparationRetained(RuntimeError):
    """A created worktree could not be recorded; retain the reservation fence."""


#: A spawn function: given a task snapshot, launch a worker and report
#: ``(ok, handle)`` where ``handle`` carries ``session``/``worktree`` (on
#: success) or ``error`` (on failure).
SpawnFn = Callable[[dict], "tuple[bool, dict]"]

#: A liveness probe: ``(worktree, machine) -> session dict`` when the embodied
#: session is **confirmed alive**, else ``None`` (dead *or* unresolvable).
LivenessFn = Callable[[str, "str | None"], "dict | None"]

#: A liveness **verdict** resolver: ``(worktree, machine, owner_session_id) ->
#: 'live' | 'gone' | 'unknown'`` (identity-keyed; ``unknown`` is never treated as
#: death). Injectable so tests drive verdicts deterministically.
VerdictFn = Callable[[str, "str | None", "str | None"], str]

#: A local agent-worktrees directory-presence probe:
#: ``(worktree, project) -> True`` (still on disk, any tracking status),
#: ``False`` (confirmed absent), or ``None`` (resolver failure -- never
#: treated as absence). ``project`` names the target project explicitly (a
#: CWD-neutral supervisor cannot rely on CWD-based project discovery). Used
#: only to retire an unleased, worktree-only spawn reservation whose task
#: never captured an ``owner_session_id`` -- see
#: :meth:`Supervisor.release_requested_bodies`.
WorktreeDirectoryPresentFn = Callable[[str, "str | None"], "bool | None"]

#: A nudge sender: ``(worktree, machine, task) -> sent?``. Delivers a non-blocking
#: steering message to a stalled-but-live embodied session. Injectable for tests.
NudgeFn = Callable[[str, "str | None", dict], bool]

#: A re-drive sender for a spawned-but-unclaimed embodied worker. The session is
#: known live, but the task is still queued/unowned, so the supervisor re-sends
#: the idempotent autopilot seed instead of spawning a duplicate.
RedriveFn = Callable[[str, "str | None", dict, dict, dict], bool]

#: Prime a terminal CLI worker for ground-layer managed GC:
#: ``(worktree, session) -> structured outcome``.
ConclusionFn = Callable[[str, "str | None"], dict]
AttemptConclusionFn = Callable[[str, str | None, str, str], dict]

#: Stop a local headless bridge session while preserving its durable record.
LocalColdFn = Callable[[str], bool]

#: Stop a remote fleet bridge session while preserving it for later resume.
FleetColdFn = Callable[[str, str], bool]


def _default_liveness(worktree: str, machine: str | None) -> dict | None:
    """Resolve an embodied session's liveness via the agent-bridge registry.

    Delegates to :func:`agent_dispatch.tracking.resolve_live_session` (shells the
    ``agent-bridge`` CLI, cross-machine over SSH when the owner is remote). All
    failure modes collapse to ``None`` -- so ``None`` means "not confirmed alive",
    which is why the supervisor only *heartbeats* on a positive result and never
    treats ``None`` as proof-of-death.
    """
    from . import tracking

    peer = machine if tracking.remote_dispatch.is_peer_machine(machine) else None
    return tracking.resolve_live_session(worktree, machine=peer)


def _reservation_made_progress(reservation: dict, task: dict) -> bool:
    """Whether this spawned body durably advanced the task after reservation.

    A headless body commonly ends its one turn after posting a card/progress beat.
    That is a successful embodiment round, not a failed spawn attempt. Compare the
    durable activity timestamps to this reservation so stale progress from an
    earlier body cannot mask a newly crashing replacement.
    """
    try:
        reserved_at = float(reservation.get("reserved_at") or 0)
    except (TypeError, ValueError):
        reserved_at = 0.0
    timestamps: list[object] = []
    card = task.get("card")
    if isinstance(card, dict):
        timestamps.append(card.get("ts"))
    progress = task.get("latest_progress")
    if isinstance(progress, str):
        try:
            progress = json.loads(progress)
        except json.JSONDecodeError:
            progress = None
    if isinstance(progress, dict):
        timestamps.append(progress.get("ts"))
    for value in timestamps:
        try:
            if float(value) > reserved_at:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _default_verdict(worktree: str, machine: str | None, owner_session_id: str | None) -> str:
    """Resolve an embodied session's liveness to a **tri-state verdict** via the
    agent-bridge registry (shells the CLI, cross-machine over SSH). Delegates to
    :func:`agent_dispatch.tracking.liveness_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import tracking

    return tracking.liveness_verdict(worktree, machine=machine, owner_session_id=owner_session_id)


def _default_worktree_directory_present(
    worktree: str, project: str | None = None
) -> bool | None:
    """Whether ``worktree`` still exists on disk, per the local agent-worktrees
    registry (any tracking status). ``project`` is threaded through to name the
    target project explicitly, since this runs from a CWD-neutral supervisor.
    Delegates to :func:`agent_dispatch.tracking.worktree_directory_present`;
    ``None`` on any resolver failure, never a guess."""
    from . import tracking

    return tracking.worktree_directory_present(worktree, project=project)


def _default_nudge(worktree: str, machine: str | None, task: dict) -> bool:
    """Deliver a non-blocking nudge to a stalled-but-live embodied session.

    Builds a terse *notify*-kind steering message pointing the worker back at its
    goal (or at recording a blocker) and shells it via
    :func:`agent_dispatch.bridge.send_nudge`. Best-effort -- a failed send is not
    fatal (recovery, not the nudge, handles a genuinely-gone worker)."""
    from . import bridge

    tid = task.get("id")
    goal = task.get("goal") or task.get("title") or "your dispatched task"
    message = (
        f"[agent-dispatch] You appear stalled on task {tid} -- no progress "
        f"recorded recently. Goal: {goal}. Continue toward it and record a "
        f"progress beat (agent-dispatch progress {tid} --phase <p> --summary "
        f"<line>), or record a blocker (--blocker <why>); if it is already done, "
        f"complete it; if it is not yours, yield it."
    )
    return bridge.send_nudge(worktree, message)


def make_redrive_sender(route: str = "") -> RedriveFn:
    """Build a re-drive sender that uses the same coordinator route as spawn.

    A re-drive targets a **live** embodied session that never claimed its
    spawned task -- the same worktree/session the supervisor originally
    embodied with the full autopilot seed. That worker already had a chance to
    read the charter (``agent-dispatch charter show autopilot``) the first
    time it embodied, so the re-drive seed is built ``concise=True``: a short
    reminder of the task-specific mechanics that points back at the charter
    command instead of re-inlining the whole behavioral essay a second time.
    """

    def redrive(
        worktree: str,
        machine: str | None,
        task: dict,
        session: dict,
        reservation: dict,
    ) -> bool:
        from . import bridge, embody

        task_id = str(task.get("id") or "")
        if not task_id:
            return False
        worker_id = f"redrive-{uuid.uuid4().hex[:8]}"
        prompt = embody.autopilot_worker_prompt(
            task_id, worker_id=worker_id, route=route, concise=True
        )
        session_id = session.get("session_id")
        expected_session_id = session_id if isinstance(session_id, str) else None
        return bridge.redrive_embodied_worker(
            worktree,
            prompt,
            machine=machine,
            expected_session_id=expected_session_id,
            idempotency_key=f"{reservation.get('key')}:redrive",
        )

    return redrive


def _default_redrive(
    worktree: str,
    machine: str | None,
    task: dict,
    session: dict,
    reservation: dict,
) -> bool:
    """Re-send the autopilot seed to a live worker that never claimed its task."""
    return make_redrive_sender()(worktree, machine, task, session, reservation)


def _default_conclusion(worktree: str, session: str | None) -> dict:
    from . import embody

    return embody.conclude_disposable_worker(worktree, session)


def _default_attempt_conclusion(
    worktree: str,
    session: str | None,
    reservation_key: str,
    driver: str,
) -> dict:
    from . import embody

    return embody.conclude_dispatch_attempt(
        worktree,
        session,
        reservation_key,
        owner=driver,
    )


def _worktree_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.worktree_from_owner(owner)


def _worktree_from_reservation(reservation: dict, owner: str | None = None) -> str | None:
    """Best-effort worktree handle for a spawn reservation.

    Newer reservations persist ``worktree`` directly. Older rows sometimes only
    have the mux session handle (``wt-<worktree>``); decode that enough to
    reconcile and re-drive rather than leaving the worker invisible forever.
    """
    worktree = reservation.get("worktree")
    if isinstance(worktree, str) and worktree:
        return worktree
    handle = reservation.get("session_handle")
    if isinstance(handle, str) and handle.startswith("wt-") and len(handle) > 3:
        return handle[3:]
    return _worktree_from_owner(owner)


def _machine_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.machine_from_owner(owner)


#: A **fleet-body** liveness verdict resolver: ``(host, bridge_session_id) ->
#: 'live' | 'gone' | 'unknown'``. Probes a headless fleet body's agent-bridge
#: session on its pool host over SSH; ``unknown`` is never treated as death.
#: Injectable so tests drive verdicts deterministically.
FleetVerdictFn = Callable[[str, str], str]
FleetActivityFn = Callable[[str, str], str | None]
FleetEndFn = Callable[[str, str], bool]

#: A **local-body** liveness verdict resolver: ``(bridge_session_id) ->
#: 'live' | 'gone' | 'unknown'``. Probes a *local* headless body's agent-bridge
#: session on this host (no SSH); ``unknown`` is never treated as death.
#: Injectable so tests drive verdicts deterministically.
LocalBodyVerdictFn = Callable[[str], str]
LocalBodyActivityFn = Callable[[str], str | None]
LocalAcpSessionFn = Callable[[str], str | None]
LocalBodyTargetDirFn = Callable[[str], str | None]
LocalEndFn = Callable[[str], bool]
LocalResumeFn = Callable[[str, str], bool]
ScriptBodyVerdictFn = Callable[[int, "str | None"], str]

#: Prefix stamped on the reservation ``session_handle`` of a headless fleet body,
#: encoding its recovery handle as ``fleet-body:<host>:<bridge-session-id>`` (see
#: :meth:`agent_dispatch.fleet.FleetSpawner.__call__`).
_FLEET_BODY_PREFIX = "fleet-body:"

#: Prefix stamped on the reservation ``session_handle`` of a **local** headless
#: body, encoding its recovery handle as ``local-body:<bridge-session-id>`` (see
#: :func:`make_headless_spawn`). Unlike a fleet body there is no host component --
#: the session lives on *this* machine's agent-bridge daemon.
_LOCAL_BODY_PREFIX = "local-body:"

#: Prefix stamped on the reservation ``session_handle`` of a plain deterministic
#: script body, encoding its process recovery handle as ``script-body:<json>``
#: where the JSON object carries ``worker_id``, ``pid``, and an optional
#: ``start_token`` that protects against PID reuse.
_SCRIPT_BODY_PREFIX = "script-body:"
_TERMINAL_BRIDGE_SESSION = re.compile(
    r"\bSession\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s+"
    r"entered\s+(?:failed|ended|stopped)\b"
)
_TIMED_OUT_BRIDGE_SESSION = re.compile(
    r"\bTimed out waiting for session\s+"
    r"([A-Za-z0-9][A-Za-z0-9._-]*)\s+to become idle\b"
)


def _failed_bridge_session(result: object) -> str | None:
    text = "\n".join(
        str(getattr(result, name, "") or "")
        for name in ("stdout", "stderr")
    )
    for pattern in (_TERMINAL_BRIDGE_SESSION, _TIMED_OUT_BRIDGE_SESSION):
        if match := pattern.search(text):
            return match.group(1)
    return None


class _SpawnReleaseClient(Protocol):
    """The narrow slice of :class:`DispatchClient` this module depends on.

    A structural ``Protocol`` (not an import of ``DispatchClient`` itself)
    keeps this module free of a dependency back on ``client.py``, while still
    giving callers real static checking in place of a bare ``object``.
    """

    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
        session_handle: str | None = None,
        worktree: str | None = None,
    ) -> object: ...


def request_failed_created_spawn_release(
    client: _SpawnReleaseClient,
    key: str,
    handle: dict,
    spawn_task: dict,
    detail: str,
) -> None:
    """Fence a failed headless spawn, retaining its body identity if known.

    Companion to :func:`_failed_bridge_session` above: a headless spawn that
    already created its own worktree must retain any bridge session identity
    it managed to capture *before* fencing the reservation for release, so
    the supervisor's exact-absence liveness/cleanup path (not a permanent
    fence with no recovery handle) resolves the attempt.
    """
    failed_session = handle.get("session")
    client.request_spawn_release(
        key,
        detail=detail,
        disposition="failed",
        session_handle=(
            failed_session
            if isinstance(failed_session, str) and failed_session
            else None
        ),
        worktree=handle.get("worktree") or spawn_task.get("spawn_worktree"),
    )


def _parse_fleet_body_handle(session_handle: str | None) -> tuple[str, str] | None:
    """Decode a ``fleet-body:<host>:<bridge-session-id>`` reservation handle.

    Returns ``(host, bridge_session_id)`` for a headless fleet body whose recovery
    handle was captured at spawn, else ``None`` (a worktree-backed embody, a
    fleet body whose session id could not be captured, or any other handle).
    """
    if not session_handle or not session_handle.startswith(_FLEET_BODY_PREFIX):
        return None
    rest = session_handle[len(_FLEET_BODY_PREFIX) :]
    host, _sep, sid = rest.partition(":")
    if not host or not sid:
        return None
    return host, sid


def _default_fleet_verdict(host: str, bridge_session_id: str) -> str:
    """Resolve a headless fleet body's liveness to a tri-state verdict by probing
    its agent-bridge session on the pool ``host`` over SSH. Delegates to
    :func:`agent_dispatch.embody.fleet_body_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import embody

    return embody.fleet_body_verdict(host, bridge_session_id)


def _default_fleet_activity(host: str, bridge_session_id: str) -> str | None:
    from . import embody

    return embody.fleet_body_activity(host, bridge_session_id)


def _parse_local_body_handle(session_handle: str | None) -> str | None:
    """Decode a ``local-body:<bridge-session-id>`` reservation handle.

    Returns the local agent-bridge ``session_id`` for a headless body embodied on
    *this* machine whose recovery handle was captured at spawn, else ``None`` (a
    worktree-backed embody, a fleet body, a headless body whose session id could
    not be captured, or any other handle).
    """
    if not session_handle or not session_handle.startswith(_LOCAL_BODY_PREFIX):
        return None
    sid = session_handle[len(_LOCAL_BODY_PREFIX) :]
    return sid or None


def _default_local_body_verdict(bridge_session_id: str) -> str:
    """Resolve a *local* headless body's liveness to a tri-state verdict by
    probing its agent-bridge session on this host (no SSH). Delegates to
    :func:`agent_dispatch.embody.local_body_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import embody

    return embody.local_body_verdict(bridge_session_id)


def _default_local_body_activity(bridge_session_id: str) -> str | None:
    from . import tracking

    for session in tracking.list_local_body_sessions():
        if str(session.get("session_id") or "") == bridge_session_id:
            return tracking.session_activity(session)
    return None


def _default_local_acp_session(bridge_session_id: str) -> str | None:
    from . import tracking

    for session in tracking.list_local_body_sessions():
        if str(session.get("session_id") or "") != bridge_session_id:
            continue
        acp_session_id = session.get("acp_session_id")
        return str(acp_session_id) if acp_session_id else None
    return None


def _default_local_body_target_dir(bridge_session_id: str) -> str | None:
    """Return an absolute target directory from a local body snapshot."""
    from . import tracking

    for session in tracking.list_local_body_sessions():
        if str(session.get("session_id") or "") != bridge_session_id:
            continue
        target_dir = session.get("target_dir")
        if not isinstance(target_dir, str) or not target_dir:
            return None
        path = Path(target_dir)
        return str(path) if path.is_absolute() else None
    return None


def _target_directory_missing(target_dir: str) -> bool | None:
    """Classify an absolute target directory without treating stat errors as loss."""
    path = Path(target_dir)
    if not path.is_absolute():
        return None
    try:
        target_stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return None
    return not stat.S_ISDIR(target_stat.st_mode)


def _default_local_cold(bridge_session_id: str) -> bool:
    from . import bridge

    return bridge.stop_worker(bridge_session_id)


def _default_local_end(bridge_session_id: str) -> bool:
    from . import bridge

    return bridge.end_worker(bridge_session_id)


def _default_local_resume(bridge_session_id: str, prompt: str) -> bool:
    from . import bridge

    return bridge.resume_worker(bridge_session_id, prompt)


def _default_fleet_cold(host: str, bridge_session_id: str) -> bool:
    from . import embody

    return embody.stop_fleet_body(host, bridge_session_id)


def _default_fleet_end(host: str, bridge_session_id: str) -> bool:
    from . import embody

    return embody.stop_fleet_body(host, bridge_session_id)


def _cleanup_script_task_file(task_file: str | None) -> None:
    if not isinstance(task_file, str) or not task_file:
        return
    try:
        Path(task_file).unlink(missing_ok=True)
    except OSError:
        pass


def _encode_script_body_handle(
    worker_id: str,
    pid: int,
    start_token: str | None,
    *,
    task_file: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "worker_id": worker_id,
            "pid": pid,
            "start_token": start_token,
            "task_file": task_file,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{_SCRIPT_BODY_PREFIX}{payload}"


def _parse_script_body_handle(
    session_handle: str | None,
) -> tuple[str, int, str | None, str | None] | None:
    """Decode a ``script-body:<json>`` reservation handle.

    Returns ``(worker_id, pid, start_token, task_file)`` for a script body embodied on this
    host, else ``None``.
    """
    if not session_handle or not session_handle.startswith(_SCRIPT_BODY_PREFIX):
        return None
    raw = session_handle[len(_SCRIPT_BODY_PREFIX) :]
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    worker_id = payload.get("worker_id")
    pid = payload.get("pid")
    start_token = payload.get("start_token")
    task_file = payload.get("task_file")
    if not isinstance(worker_id, str) or not worker_id:
        return None
    if not isinstance(pid, int) or pid <= 0:
        return None
    if start_token is not None and not isinstance(start_token, str):
        return None
    if task_file is not None and not isinstance(task_file, str):
        return None
    return worker_id, pid, start_token, task_file


def _default_script_body_verdict(pid: int, start_token: str | None) -> str:
    """Resolve a local script body's liveness to a tri-state verdict.

    PID reuse is fenced with ``start_token`` when available. A missing process is
    ``gone``; a running process with a matching start token is ``live``; any probe
    uncertainty is ``unknown`` rather than a false death.
    """
    from . import companion

    try:
        if not companion._process_exists(pid):
            return _tracking().GONE
    except Exception:
        return _tracking().UNKNOWN
    if not start_token:
        return _tracking().LIVE
    try:
        current = companion.process_start_token(pid)
    except Exception:
        return _tracking().UNKNOWN
    if current is None:
        return _tracking().UNKNOWN
    return _tracking().LIVE if current == start_token else _tracking().GONE


def _load_script_spec(task: dict) -> tuple[list[str], str | None, dict[str, str], int | None]:
    raw_payload = task.get("payload_inline")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        raise ValueError("script embodiment requires a JSON payload_inline object")
    try:
        payload = json.loads(raw_payload)
    except ValueError as exc:
        raise ValueError("script payload_inline is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("script payload_inline must be a JSON object")
    spec = payload.get("script") if isinstance(payload.get("script"), dict) else payload
    if not isinstance(spec, dict):
        raise ValueError("script payload must be an object or carry a 'script' object")

    argv_value = spec.get("argv")
    path_value = spec.get("path")
    args_value = spec.get("args") or []
    cwd_value = spec.get("cwd")
    env_value = spec.get("env") or {}
    heartbeat_value = spec.get("heartbeat_seconds")

    if path_value is not None:
        if argv_value is not None:
            raise ValueError("script payload cannot specify both 'path' and 'argv'")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("script payload 'path' must be a non-empty string")
        if not isinstance(args_value, list) or not all(
            isinstance(item, str) and item for item in args_value
        ):
            raise ValueError("script payload 'args' must be a list of non-empty strings")
        script_path = Path(path_value).expanduser()
        if not script_path.is_absolute():
            raise ValueError("script payload 'path' must be absolute")
        if not script_path.is_file():
            raise ValueError(f"script payload path does not exist: {script_path}")
        from .procutil import resolve_own_runtime_python

        argv = [resolve_own_runtime_python(), str(script_path), *args_value]
    else:
        if not isinstance(argv_value, list) or not argv_value:
            raise ValueError("script payload must provide either 'path' or a non-empty 'argv'")
        if not all(isinstance(item, str) and item for item in argv_value):
            raise ValueError("script payload 'argv' must be a list of non-empty strings")
        argv = list(argv_value)

    cwd: str | None = None
    if cwd_value is not None:
        if not isinstance(cwd_value, str) or not cwd_value.strip():
            raise ValueError("script payload 'cwd' must be a non-empty string when provided")
        cwd_path = Path(cwd_value).expanduser()
        if not cwd_path.is_absolute():
            raise ValueError("script payload 'cwd' must be absolute")
        cwd = str(cwd_path)

    if not isinstance(env_value, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in env_value.items()
    ):
        raise ValueError("script payload 'env' must be an object of string:string pairs")
    env = dict(env_value)

    heartbeat_seconds: int | None = None
    if heartbeat_value is not None:
        if not isinstance(heartbeat_value, int) or heartbeat_value <= 0:
            raise ValueError("script payload 'heartbeat_seconds' must be a positive integer")
        heartbeat_seconds = heartbeat_value

    return argv, cwd, env, heartbeat_seconds


def _tracking():
    """Lazy accessor for the ``tracking`` module (its verdict constants)."""
    from . import tracking

    return tracking


def make_embody_spawn(
    *,
    driver: str = "agent-dispatch",
    verify_timeout: int = 0,
    route: str = "",
    all_repos: bool = False,
    no_pair: bool = False,
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker via ``agent-worktrees``.

    Degrades cleanly: if the ``agent-worktrees`` CLI is absent, the spawn reports
    failure (the supervisor fails the reservation, leaving the task queued).

    The supervisor runs CWD-neutral (a service whose working directory is its own
    runtime dir, not any repo), so the spawn **names the target project
    explicitly** -- derived from the task's lane -- via embody's ``--project``
    global, rather than relying on git-like CWD discovery (which would fail with
    "Could not resolve a project for 'embody'"). See the
    ``project-scoped-invocation`` pattern.

    ``route`` is the coordinator routing intent handed to the worker's
    ``agent-dispatch`` commands (``""`` for local discovery, ``" --shared"`` for
    the shared moniker); never a raw ``--url`` (the caller rejects that).
    """
    from . import embody

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"embody-{uuid.uuid4().hex[:8]}"
        try:
            result = embody.spawn_embodied_worker(
                task["id"],
                worker_id=worker_id,
                driver=driver,
                project=embody.project_for_task(task),
                worktree_id=task.get("spawn_worktree"),
                route=route,
                repo=None if all_repos else task.get("repo"),
                all_repos=all_repos,
                verify_timeout=verify_timeout,
            )
        except embody.EmbodyUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            return False, {"error": (result.stderr or "").strip()[:200] or "nonzero exit"}
        handle = embody.parse_handle(result)
        if not handle.get("session"):
            # A zero exit with no recognizable session id is not a usable
            # success: record_spawn would persist a SPAWNED reservation with
            # session_handle=None, indistinguishable from a reservation that
            # never reached spawning anything at all -- an ambiguity that
            # would let a later release_requested_bodies() cleanup pass
            # wrongly treat a genuinely-launched (but unidentifiable) body as
            # confirmed absent. Fail loudly instead so this attempt is
            # retried rather than silently spawning an untrackable body.
            return False, {
                "error": "embody reported success but returned no session id"
            }
        return True, handle

    spawn.requires_reusable_worktree = True
    spawn.allocation_driver = driver
    spawn.allocation_interface = "cli"
    spawn.allocation_no_pair = no_pair
    return spawn


def make_headless_spawn(
    *,
    agent: str = "task-worker",
    route: str = "",
    all_repos: bool = False,
    no_pair: bool = False,
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker as a **headless
    agent-bridge ACP** session -- no mux, no CLI-start-prompt.

    This is the embodiment for **self-contained, bounded** tasks that need no
    human attach: a scheduled/reactive sweep that claims a task, runs it to a
    deliberate completion, and is torn down. It sidesteps the CLI-start-prompt
    delivery path entirely (a seeded CLI session can race the input caret and
    never deliver its seed), so a headless-marked task never deadlocks on that
    path.

    It reuses the **same autopilot seed** as the CLI backend
    (:func:`agent_dispatch.embody.autopilot_worker_prompt` -- claim-under-identity,
    contract-net evaluation, deferred completion), so a headless-embodied task is
    driven identically to a CLI-embodied one; only the *body* differs. Degrades
    cleanly: if the ``agent-bridge`` CLI is absent, the spawn reports failure (the
    supervisor fails the reservation, leaving the task queued).

    The supervisor pre-creates and records the headless body's worktree before
    launch, then passes that exact target to agent-bridge. It also records a
    ``local-body:<bridge-session-id>`` recovery handle, so a
    body that ends before completing (crash, or an explicit ``agent-bridge end``
    after a run cancel) is **liveness-recovered**: the supervisor probes the
    session locally and, on a confirmed-gone verdict, settles the orphaned
    ``spawned`` reservation -- freeing the label's concurrency slot instead of
    starving it. Reconciliation still settles the reservation when the task
    reaches a terminal state.

    ``route`` is the coordinator routing intent handed to the worker's
    ``agent-dispatch`` commands (``""`` for local discovery, ``" --shared"`` for
    the shared moniker); never a raw ``--url`` (the caller rejects that).
    """
    from . import bridge, embody

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"headless-{uuid.uuid4().hex[:8]}"
        seed = embody.autopilot_worker_prompt(
            task["id"],
            worker_id=worker_id,
            route=route,
            repo=None if all_repos else task.get("repo"),
            all_repos=all_repos,
            explicit_worker_identity=True,
        )
        prior_session = _parse_local_body_handle(task.get("spawn_session_handle"))
        try:
            result = bridge.spawn_or_resume_worker(
                task["id"],
                agent=agent,
                worker_id=worker_id,
                prompt=seed,
                prior_session_id=prior_session,
                liveness_fn=embody.local_body_verdict,
                target_dir=task.get("spawn_worktree_path"),
                worktree_id=task.get("spawn_worktree"),
                wait=False,
                json_output=True,
            )
        except bridge.BridgeCarriedSessionBusy as exc:
            return False, {"error": str(exc), "deferred": True}
        except bridge.BridgeUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            handle = {
                "error": (result.stderr or "").strip()[:200] or "nonzero exit"
            }
            if failed_session := _failed_bridge_session(result):
                handle["session"] = f"{_LOCAL_BODY_PREFIX}{failed_session}"
            return False, handle
        # Capture the created local agent-bridge session id and encode it as a
        # `local-body:<sid>` recovery handle so a *gone* body (ended/cancelled)
        # is liveness-recovered by the supervisor -- freeing its spawn slot --
        # instead of orphaning its `spawned` reservation forever. When the id
        # can't be captured, fall back to the opaque worker id (degrade safe:
        # unprobeable, exactly the pre-fix behavior).
        sid = embody.parse_fleet_body_session(result)
        handle = f"{_LOCAL_BODY_PREFIX}{sid}" if sid else worker_id
        return True, {
            "session": handle,
            "worktree": task.get("spawn_worktree"),
        }

    spawn.requires_reusable_worktree = True
    spawn.allocation_driver = "agent-dispatch"
    spawn.allocation_interface = "acp"
    spawn.allocation_agent = agent
    spawn.allocation_no_pair = no_pair
    spawn.allocation_project_for = lambda _task: (
        bridge.registered_agent_project(agent, strict=True) or ""
    )
    return spawn


def make_script_spawn(
    *,
    route: str = "",
    all_repos: bool = False,
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker as a plain deterministic
    subprocess rather than an agent session.

    The task's ``payload_inline`` must be JSON describing the command to run:

    - ``{"path": "C:\\absolute\\worker.py", "args": [...], "cwd": "...", "env": {...}}``
      runs the file with this plugin's own runtime Python; or
    - ``{"argv": ["python-or-exe", "..."], "cwd": "...", "env": {...}}``
      runs the exact argv directly.

    The spawned process receives task/coordinator context via environment
    variables so it can drive the ordinary claim/start/progress/complete/
    abandon lifecycle through :mod:`agent_dispatch.script_worker` without ever
    invoking an LLM.
    """

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"script-{uuid.uuid4().hex[:8]}"
        try:
            argv, cwd, extra_env, heartbeat_seconds = _load_script_spec(task)
        except ValueError as exc:
            return False, {"error": str(exc)}

        task_file: tempfile.NamedTemporaryFile[str] | None = None
        task_file_path: str | None = None
        try:
            task_file = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=f"-{task.get('id') or 'task'}.json",
                delete=False,
            )
            json.dump(task, task_file, sort_keys=True)
            task_file.flush()
            task_file_path = task_file.name
            task_file.close()

            env = dict(os.environ)
            env.update(extra_env)
            env.update(
                {
                    "AGENT_DISPATCH_SCRIPT_TASK_ID": str(task["id"]),
                    "AGENT_DISPATCH_SCRIPT_WORKER_ID": worker_id,
                    "AGENT_DISPATCH_SCRIPT_ROUTE": (
                        "shared" if route.strip() == "--shared" else "local"
                    ),
                    "AGENT_DISPATCH_SCRIPT_REPO": (
                        "" if all_repos else str(task.get("repo") or "")
                    ),
                    "AGENT_DISPATCH_SCRIPT_ALL_REPOS": "1" if all_repos else "0",
                    "AGENT_DISPATCH_SCRIPT_TASK_FILE": task_file_path,
                }
            )
            if heartbeat_seconds is not None:
                env["AGENT_DISPATCH_SCRIPT_HEARTBEAT_SECONDS"] = str(heartbeat_seconds)

            from .procutil import _process_tree_kwargs

            process = subprocess.Popen(  # noqa: S603 -- deterministic argv from task payload
                argv,
                cwd=cwd or None,
                env=env,
                stdin=subprocess.DEVNULL,
                **_process_tree_kwargs(),
            )
        except OSError as exc:
            if task_file_path:
                Path(task_file_path).unlink(missing_ok=True)
            return False, {"error": str(exc)}
        start_token = None
        try:
            from . import companion

            start_token = companion.process_start_token(process.pid)
        except Exception:
            start_token = None
        return True, {
            "session": _encode_script_body_handle(
                worker_id,
                process.pid,
                start_token,
                task_file=task_file_path,
            ),
            "worktree": None,
        }

    spawn.requires_reusable_worktree = False
    spawn.allocation_driver = "agent-dispatch"
    spawn.allocation_interface = "script"
    return spawn


def make_label_routed_spawn(default: SpawnFn, *, overrides: Mapping[str, SpawnFn]) -> SpawnFn:
    """Return a :data:`SpawnFn` that routes a task to an **override** backend when
    any of its labels has one, else to the ``default`` backend.

    This lets a *single* supervisor embody different task classes with different
    bodies -- e.g. self-contained sweep labels headless (bridge) while
    interactive/standalone worktree work stays CLI-first (embody) -- without
    splitting into multiple services. When a task carries several overridden
    labels, the first match in the task's own label order wins. With no overrides,
    the ``default`` is returned unwrapped (no behavior change).
    """
    if not overrides:
        return default

    def spawn(task: dict) -> tuple[bool, dict]:
        for label in task.get("labels") or []:
            fn = overrides.get(label)
            if fn is not None:
                return fn(task)
        return default(task)

    def requires_reusable_worktree(task: dict) -> bool:
        selected = default
        for label in task.get("labels") or []:
            if label in overrides:
                selected = overrides[label]
                break
        return bool(getattr(selected, "requires_reusable_worktree", False))

    def selected_attribute(task: dict, name: str, fallback: str) -> str:
        selected = default
        for label in task.get("labels") or []:
            if label in overrides:
                selected = overrides[label]
                break
        selector = getattr(selected, f"{name}_for", None)
        value = selector(task) if callable(selector) else getattr(selected, name, fallback)
        return value if isinstance(value, str) and value else fallback

    def selected_bool_attribute(task: dict, name: str, fallback: bool) -> bool:
        selected = default
        for label in task.get("labels") or []:
            if label in overrides:
                selected = overrides[label]
                break
        selector = getattr(selected, f"{name}_for", None)
        value = selector(task) if callable(selector) else getattr(selected, name, fallback)
        return bool(value)

    spawn.requires_reusable_worktree_for = requires_reusable_worktree
    spawn.allocation_driver_for = lambda task: selected_attribute(
        task, "allocation_driver", "agent-dispatch"
    )
    spawn.allocation_interface_for = lambda task: selected_attribute(
        task, "allocation_interface", "cli"
    )
    spawn.allocation_project_for = lambda task: selected_attribute(task, "allocation_project", "")
    spawn.allocation_no_pair_for = lambda task: selected_bool_attribute(
        task, "allocation_no_pair", False
    )
    return spawn
