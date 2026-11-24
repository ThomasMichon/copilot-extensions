"""Fleet dispatch: turn queued tasks into embody bodies on a **pool of hosts**.

The base supervisor (:mod:`agent_dispatch.supervisor`) spawns an embody body on
its **own** machine. Fleet dispatch lets an always-on supervisor instead fan
bodies out across a **pool of capable-but-not-always-on hosts** -- the shape a
containerized, always-on producer needs when the real work should run on
workstations elsewhere in the mesh.

The design (Model C) keeps the strong guarantee where it belongs:

- **Origin-owned lease.** The spawn reservation and the task lease stay on the
  supervisor's selected local/central coordinator, so at-most-once is **fleet-wide**, not
  per-pool-host. Only the *body* runs remotely; it drives the origin task back
  over the existing bidirectional SSH mesh (see
  :func:`agent_dispatch.embody.spawn_fleet_embodied_worker`). No new network bind
  is introduced on the origin.
- **Liveness-gated selection.** A pool host is a candidate only when it is
  reachable over SSH **and** has ``agent-worktrees`` (or ``agent-bridge`` in
  headless-fleet mode) -- so it can actually embody. The first live candidate by
  policy (config order) is chosen. A task pinned to a ``target_machine`` runs
  only on that pool host: an asleep pinned host defers the task, and a pin
  outside the pool is never served by this fleet (its body could not claim it).
- **Defer, don't fail, when the pool is asleep.** :meth:`FleetSpawner.can_spawn`
  is the supervisor's **capacity gate**: when no host is live, the task is skipped
  for this cycle **without a reservation**, so an all-asleep pool never burns
  spawn attempts toward the dead-letter bound. Selection is cached briefly so the
  gate and the subsequent spawn agree on the same host without re-probing.
- **Optional placement ordering.** A lane may hand the order of a managed subset
  of its pool to a trusted provider (:mod:`agent_dispatch.fleet_placement`),
  called at most once per cycle before any reservation. Unmanaged hosts keep
  their configured positions and never depend on the provider.

Nothing here is consumer-specific: a :class:`FleetSpawner` is keyed only on host
aliases + task ids.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from . import embody
from .embody_prompts import validate_fleet_route
from .fleet_placement import PlacementOrdering
from .procutil import run_ssh_command
from .queue_common import machine_matches

log = logging.getLogger("agent-dispatch.fleet")

#: A liveness probe for a pool host: ``alias -> bool`` (reachable + can embody).
LivenessFn = Callable[[str], bool]

#: How long a host's liveness result is trusted before it is re-probed. Keeps the
#: capacity gate + the spawn from double-probing the same host within a cycle.
_LIVENESS_TTL = 15.0


def _ssh_alias(host: str) -> str:
    """Lowercased SSH alias for ``host`` (SSH ``Host`` blocks are lowercase)."""
    return host.strip().lower()


def host_can_embody(host: str, *, timeout: float = 8.0) -> bool:
    """True when ``host`` is reachable over SSH **and** has ``agent-worktrees``.

    A single cheap probe that doubles as reachability + capability: SSH to the
    host (its SSH alias, never a raw IP; ``BatchMode`` so a missing key fails
    fast) and check ``command -v agent-worktrees``. Any failure -- ssh absent,
    unreachable host, timeout, or no ``agent-worktrees`` -- returns False, so an
    asleep or unprovisioned host is simply not a candidate.
    """
    return _host_has_command(host, "agent-worktrees", timeout=timeout)


def host_can_bridge(host: str, *, timeout: float = 8.0) -> bool:
    """True when ``host`` is reachable over SSH **and** has ``agent-bridge``.

    The **headless-fleet** liveness probe -- a headless fleet body embodies via
    the pool host's ``agent-bridge`` (``agent-bridge create``), not
    ``agent-worktrees``, so this checks for that binary instead. Same fail-closed
    semantics as :func:`host_can_embody`: any failure returns False so an asleep
    or unprovisioned host is not a candidate.

    A headless body is created through the Bridge carrier rather than a POSIX
    remote command line, so this probe also accepts a Windows OpenSSH host whose
    login shell is ``cmd.exe`` or PowerShell (see :func:`host_bridge_shell`).
    """
    return host_bridge_shell(host, timeout=timeout) is not None


#: :func:`host_bridge_shell` result for a host whose SSH login shell is POSIX.
POSIX_SHELL = "posix"
#: :func:`host_bridge_shell` result for a ``cmd.exe``/PowerShell login shell.
WINDOWS_SHELL = "windows"


def host_bridge_shell(host: str, *, timeout: float = 8.0) -> str | None:
    """Classify the SSH login shell under which ``host`` resolves ``agent-bridge``.

    Returns :data:`POSIX_SHELL`, :data:`WINDOWS_SHELL`, or ``None`` when the
    host is unreachable or has no ``agent-bridge``. Fleet liveness needs only
    presence (:func:`host_can_bridge`); the SSH create fallback in
    :func:`agent_dispatch.embody.spawn_fleet_headless_worker` sends a
    POSIX-quoted command line, so it also requires :data:`POSIX_SHELL`.
    """
    return _host_command_shell(host, "agent-bridge", timeout=timeout, windows=True)


def require_posix_bridge_host(host: str, *, timeout: float = 8.0) -> None:
    """Raise :class:`~agent_dispatch.embody.EmbodyUnavailable` unless ``host``
    resolves ``agent-bridge`` under a POSIX login shell.

    Guards the POSIX-quoted SSH create fallback: fleet liveness also admits
    Windows OpenSSH hosts (for the carrier path), whose ``cmd.exe`` or
    PowerShell login shell would split that command line.
    """
    host = host.strip().lower()
    shell = host_bridge_shell(host, timeout=timeout)
    if shell is None:
        raise embody.EmbodyUnavailable(
            f"pool host {host!r} is unreachable over ssh or has no agent-bridge"
        )
    if shell != POSIX_SHELL:
        raise embody.EmbodyUnavailable(
            f"pool host {host!r} resolves agent-bridge under a non-POSIX shell; "
            "without the local Agent Bridge carrier a headless body cannot start there"
        )


def _posix_command_probe(command: str) -> str:
    """A probe only a POSIX shell can run: ``command -v`` inside ``case``/``esac``.

    A bare ``command -v`` does not prove POSIX semantics: PowerShell resolves
    ``command`` to ``Get-Command``, which succeeds with nonempty output when the
    binary exists. PowerShell and ``cmd.exe`` both reject the ``case`` construct
    before running anything, so success here means a POSIX shell resolved it.
    """
    return f"case posix in posix) command -v {command};; esac"


def _host_has_command(
    host: str, command: str, *, timeout: float = 8.0, windows: bool = False
) -> bool:
    """SSH to ``host`` (SSH alias) and check that ``command`` resolves there."""
    return _host_command_shell(host, command, timeout=timeout, windows=windows) is not None


def _host_command_shell(
    host: str, command: str, *, timeout: float = 8.0, windows: bool = False
) -> str | None:
    """Classify the login shell under which ``command`` resolves on ``host``.

    The shared reachability + capability probe behind :func:`host_can_embody`
    and :func:`host_bridge_shell`. ``BatchMode`` so a missing key fails fast;
    any failure (ssh absent, unreachable, timeout, or missing binary) returns
    ``None``.

    The POSIX probe (:func:`_posix_command_probe`) counts only when it exits 0
    **and** prints the resolved command. With ``windows``, a host that fails
    the POSIX probe (other than an SSH-level failure, exit 255) gets one
    ``where.exe <command>`` probe within the same ``timeout`` budget;
    ``where.exe`` behaves the same under ``cmd.exe`` and PowerShell.
    """
    exe = shutil.which("ssh")
    if exe is None:
        return None
    probes = [(POSIX_SHELL, _posix_command_probe(command))]
    if windows:
        probes.append((WINDOWS_SHELL, f"where.exe {command}"))
    deadline = time.monotonic() + timeout
    budget = timeout
    for shell, probe in probes:
        cmd = [
            exe, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            _ssh_alias(host), probe,
        ]
        try:
            result = run_ssh_command(cmd, timeout=budget)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode == 0 and (result.stdout or "").strip():
            return shell
        if result.returncode == 255:
            return None
        budget = deadline - time.monotonic()
        if budget <= 0:
            return None
    return None


def _selector_tokens(value: object) -> set[str]:
    """Selector tokens from a task's ``excludes``/``requires`` (list or JSON text)."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            return set()
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {t for t in value if isinstance(t, str)}


class FleetSpawner:
    """A :data:`~agent_dispatch.supervisor.SpawnFn` that dispatches embody bodies
    across a **pool** of remote hosts, with a liveness capacity gate.

    Wire it into a :class:`~agent_dispatch.supervisor.Supervisor` as **both** the
    ``spawn_fn`` and the ``capacity_gate``::

        fleet = FleetSpawner(["host-a", "host-b"], origin="my-alias")
        Supervisor(client, spawn_fn=fleet, capacity_gate=fleet.can_spawn, ...)

    ``pool`` is the ordered candidate list (first live host wins). ``origin`` is
    the supervisor machine's own SSH alias, which each dispatched body uses to
    report its lease back (Model C). ``liveness`` is injectable for testing;
    it defaults to :func:`host_can_embody` (or :func:`host_can_bridge` in headless
    mode).
    ``route`` retains an explicit origin-side local/central selector; when
    omitted it follows this process's configured task authority.

    ``headless`` selects the **headless-fleet** body: instead of a CLI/mux embody
    on the pool host (which can hit the "Loading..." startup-seed hang), the body
    is a headless agent-bridge ACP session
    (:func:`agent_dispatch.embody.spawn_fleet_headless_worker`). It is driven by
    the *same* fleet seed, so a headless-fleet task claims + loops + completes
    identically; only the body differs. ``agent`` names the agent-bridge agent used
    for a headless body (default ``task-worker``).
    """

    def __init__(
        self,
        pool: Sequence[str],
        *,
        origin: str,
        driver: str = embody.DEFAULT_DRIVER,
        headless: bool = False,
        agent: str = embody.DEFAULT_HEADLESS_AGENT,
        charter: str | None = None,
        route: str | None = None,
        all_repos: bool = False,
        verify_timeout: int = 0,
        liveness: LivenessFn | None = None,
        spawn_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        now: Callable[[], float] = time.time,
        placement: PlacementOrdering | None = None,
    ):
        self.pool = [h.strip() for h in pool if h and h.strip()]
        if not self.pool:
            raise ValueError("FleetSpawner requires a non-empty pool of host aliases")
        self.origin = origin.strip()
        if not self.origin:
            raise ValueError("FleetSpawner requires a non-empty origin alias")
        self.driver = driver
        self.headless = bool(headless)
        self.agent = agent
        self.charter = charter
        if route is None:
            from .client_authority import default_authority

            route = {"legacy": "", "local": " --local", "central": " --shared"}[default_authority()]
        self.route = validate_fleet_route(route)
        if self.charter and not self.headless:
            # agent-worktrees embody has no charter-binding flag; the CLI-embody
            # fleet worker (spawn_fleet_embodied_worker) has no charter parameter
            # at all. Fail fast at construction rather than silently launching
            # every task on the bare venue with the requested charter dropped.
            raise embody.EmbodyUnavailable(
                "FleetSpawner: --charter is only supported for a headless fleet "
                "lane (agent-worktrees embody has no charter-binding flag)"
            )
        self.all_repos = bool(all_repos)
        self.verify_timeout = verify_timeout
        default_liveness = host_can_bridge if self.headless else host_can_embody
        self._liveness = liveness or default_liveness
        default_spawn: Callable[..., subprocess.CompletedProcess[str]] = (
            embody.spawn_fleet_headless_worker
            if self.headless
            else embody.spawn_fleet_embodied_worker
        )
        self._spawn: Callable[..., subprocess.CompletedProcess[str]] = spawn_fn or default_spawn
        self._now = now
        self.placement = placement
        #: task_id -> chosen host, so can_spawn() and __call__() agree per cycle.
        self._selection: dict[str, str] = {}
        #: task_id -> the last ``select`` explanation (recorded as a task event).
        self._decisions: dict[str, dict[str, Any]] = {}
        #: host -> (checked_at, is_live); short-TTL so a cycle probes each host once.
        self._live_cache: dict[str, tuple[float, bool]] = {}

    # -- host selection ------------------------------------------------------

    def begin_placement_cycle(self) -> None:
        """Start a supervisor cycle: drop the previous cycle's provider result."""
        self._decisions.clear()
        if self.placement is not None:
            self.placement.begin_cycle()

    def _is_live(self, host: str) -> bool:
        now = self._now()
        cached = self._live_cache.get(host)
        if cached is not None and (now - cached[0]) < _LIVENESS_TTL:
            return cached[1]
        live = bool(self._liveness(host))
        self._live_cache[host] = (now, live)
        return live

    def _candidates(self, task: dict[str, Any]) -> list[str]:
        """Ordered candidate hosts for ``task``.

        An unpinned task may run on any pool host, in config order. A task
        pinned to a ``target_machine`` may run **only** on that host: the claim
        enforces the pin, so a body on any other host could not claim the task.
        The pin matches a pool host case-insensitively, like the claim does. A
        pin outside the pool has no candidate here.

        The claim also checks ``machine:`` selectors against the body's
        ``--machine``, so a host named in ``excludes`` (for example after a
        ``yield --exclude-self machine``) or not named by a ``machine:`` entry in
        ``requires`` is skipped. These tokens match exactly, as in the claim.
        """
        target = (task.get("target_machine") or "").strip()
        excludes = _selector_tokens(task.get("excludes"))
        machine_requires = {
            t for t in _selector_tokens(task.get("requires")) if t.startswith("machine:")
        }

        def admits(host: str) -> bool:
            token = f"machine:{host}"
            return token not in excludes and machine_requires <= {token}

        if not target:
            return [h for h in self.pool if admits(h)]
        pinned = [h for h in self.pool if machine_matches(target, h)][:1]
        return [h for h in pinned if admits(h)]

    def select(self, task: dict[str, Any]) -> str | None:
        """Pick the first live candidate host for ``task``; cache and return it,
        or ``None`` when the whole pool is asleep/unreachable this cycle.

        With a placement provider, managed hosts are reordered (or dropped)
        before liveness, so a hard pin to a managed host starts only when the
        provider kept it and it is live; it never moves to another host.
        """
        tid = str(task.get("id"))
        eligible = self._candidates(task)
        candidates = self.placement.order(eligible) if self.placement is not None else eligible
        probes: dict[str, bool] = {}
        selected: str | None = None
        for host in candidates:
            probes[host] = self._is_live(host)
            if probes[host]:
                selected = host
                break
        self._decisions[tid] = self._decision(tid, eligible, candidates, probes, selected)
        log.debug("fleet placement for task %s: %s", tid, json.dumps(self._decisions[tid], sort_keys=True))
        if selected is not None:
            self._selection[tid] = selected
            return selected
        self._selection.pop(tid, None)
        if self.placement is not None:
            log.info("fleet: no eligible host for task %s this cycle; deferring", tid)
        return None

    def _decision(
        self, tid: str, eligible: list[str], merged: list[str],
        probes: dict[str, bool], selected: str | None,
    ) -> dict[str, Any]:
        """Explain one ``select``: what the provider ranked and why ``selected`` won."""
        decision: dict[str, Any] = {
            "decision_id": uuid.uuid4().hex[:12],
            "task_id": tid,
            "pool": list(self.pool),
            "task_candidates": eligible,
            "merged_order": merged,
            "probes": probes,
            "selected": selected,
        }
        if self.placement is None:
            return decision
        decision.update(self.placement.audit())
        ordered = decision.get("provider_ordered") or []
        decision["omitted_by_provider"] = [h for h in self.placement.declaration.hosts if h not in ordered]
        decision["selected_provider_rank"] = (
            ordered.index(selected) + 1 if selected in ordered else None
        )
        skipped: list[dict[str, Any]] = []
        for rank, host in enumerate(ordered, 1):
            if host == selected:
                break
            reason = ("excluded_by_task" if host not in eligible
                      else "not_live" if probes.get(host) is False else "not_reached")
            skipped.append({"host": host, "rank": rank, "reason": reason})
        if selected is not None and selected not in self.placement.managed and ordered:
            skipped.append({"host": selected, "reason": "unmanaged_host_ahead_in_pool"})
        decision["skipped"] = skipped
        return decision

    def take_placement_decision(self, task_id: str) -> dict[str, Any] | None:
        """Remove and return this cycle's last ``select`` decision for ``task_id``."""
        return self._decisions.pop(str(task_id), None)

    def can_spawn(self, task: dict[str, Any]) -> bool:
        """Capacity gate: True iff a live pool host is available for ``task``.

        Used as the supervisor's ``capacity_gate`` so a task is reserved only when
        the fleet can actually take it -- an all-asleep pool defers the task
        (no reservation, no burned attempt) instead of failing it.
        """
        return self.select(task) is not None

    # -- spawn ---------------------------------------------------------------

    def __call__(self, task: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Spawn the task's body on its selected (or freshly selected) pool host.

        Returns ``(ok, handle)`` in the :data:`SpawnFn` contract. ``handle`` carries
        ``session``/``worktree`` (and the chosen ``machine`` + synthetic ``owner``)
        on success, or ``error`` on failure. A no-live-host result reports failure
        with ``deferred=True`` -- but the capacity gate normally prevents the
        supervisor from reserving in that case, so this is only a race backstop.
        """
        tid = str(task.get("id"))
        host = self._selection.get(tid) or self.select(task)
        if host is None:
            return False, {"error": "no live pool host", "deferred": True}
        # Synthetic, opaque lease-holder id assigned by the supervisor: it is what
        # the remote body claims/completes the ORIGIN task under (its own worktree
        # cannot identify it to the origin). Stable for this spawn attempt.
        owner = f"fleet-{tid}-{uuid.uuid4().hex[:6]}"
        routing = {"route": self.route} if self.route else {}
        try:
            if self.headless:
                # Headless-fleet body: an agent-bridge ACP session on the pool
                # host (no worktree, no CLI-start-prompt hang). No driver/
                # verify_timeout (those are CLI/mux concepts); it takes an agent.
                result = self._spawn(
                    host,
                    tid,
                    origin=self.origin,
                    owner=owner,
                    worker_id=owner,
                    machine=host,
                    agent=self.agent,
                    charter=self.charter,
                    repo=None if self.all_repos else task.get("repo"),
                    all_repos=self.all_repos,
                    **routing,
                )
            else:
                result = self._spawn(
                    host,
                    tid,
                    origin=self.origin,
                    owner=owner,
                    worker_id=owner,
                    machine=host,
                    driver=self.driver,
                    project=embody.project_for_task(task),
                    repo=None if self.all_repos else task.get("repo"),
                    all_repos=self.all_repos,
                    **routing,
                    verify_timeout=self.verify_timeout,
                )
        except embody.EmbodyUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[:200] or "nonzero exit"
            return False, {"error": f"embody on {host!r} failed: {detail}"}
        if self.headless:
            # A headless body is not a parallel worktree, so there is no worktree
            # handle to recover; its recovery handle is instead the pool host's
            # agent-bridge session. Encode `fleet-body:<host>:<bridge-session-id>`
            # as the session_handle so the supervisor can liveness-probe the body
            # over SSH and re-embody a *confirmed-gone* one (resuming from the
            # task's progress_log). If the session id can't be parsed, fall back to
            # the synthetic owner -- the body still runs, it just isn't
            # auto-recovered (degrade safe). The reservation is otherwise settled
            # on the task's terminal state (bounded sweeps drive their own loop).
            bridge_sid = embody.parse_fleet_body_session(result)
            session_token = f"fleet-body:{host}:{bridge_sid}" if bridge_sid else owner
            handle: dict[str, str | None] = {"session": session_token, "worktree": None}
        else:
            handle = embody.parse_handle(result)
        handle["machine"] = host
        handle["owner"] = owner
        # The reservation now holds this task's state; the per-cycle selection
        # cache only needs to bridge can_spawn() -> __call__() within one
        # iteration, so drop it here to keep the cache bounded to in-flight
        # selections over a long-running supervisor.
        self._selection.pop(tid, None)
        log.info("fleet-dispatched task %s to %s (owner %s)", tid, host, owner)
        return True, handle
