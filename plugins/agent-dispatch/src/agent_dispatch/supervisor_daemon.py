"""The **singleton supervisor daemon** -- the master runtime that runs the
registered work on one host.

Where :mod:`agent_dispatch.registrations` defines *what* is registered and the
coordinator store persists it, this module is *what runs it*: exactly **one**
supervisor process per machine-and-environment reads the registration registry
and drives each **active** registration in its **own subprocess**, reconciling
the running set against the registry on every tick -- starting a newly-registered
unit, restarting one whose spec changed, winding down one that was removed or
paused, and reviving one whose subprocess crashed. One busy or failing unit is
isolated in its own child, so it never blocks its siblings or the master.

Single-instance is enforced with the store's **pin-not-failover** election (a
schedule-lease over a ``supervisor:<machine>:<env>`` scope): the first daemon
wins the scope and a second **stands down** rather than spawning a rival loop --
the *one supervisor per machine-and-environment* the vision requires.

This increment lands the daemon mechanics and the **supervised-lane** unit
(reconstruct the ``agent-dispatch supervise`` foreground loop from a stored
spec). Folding the schedule / emitter / evaluator kinds into units the daemon
runs is the next increment; an unsupported kind is logged and skipped, never
fatal. The class is transport-light and fully injectable (launcher, clock,
sleep) so its reconcile logic is unit-tested without real subprocesses.

See ``visions/plugins/agent-dispatch`` -- Concept *the supervisor*, Feature
*registered-supervision*, Behavior *supervise-registers-and-returns*.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .procutil import no_window_kwargs
from .registrations import RegistrationKind

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class UnsupportedKind(RuntimeError):
    """A registration whose kind the daemon cannot yet run in a subprocess."""


def supervisor_lease_scope(machine: str | None, env: str) -> str:
    """The single-instance election scope for a host's supervisor daemon."""
    return f"supervisor:{machine or 'local'}:{env or 'default'}"


def _spec_fingerprint(reg: dict) -> str:
    """A stable fingerprint of a registration's runtime-relevant identity.

    Changes only when the ``kind`` or ``spec`` changes, so a reconcile restarts a
    unit exactly when its definition changed -- not on unrelated ``updated_at``
    churn.
    """
    return json.dumps(
        {"kind": reg.get("kind"), "spec": reg.get("spec")},
        sort_keys=True,
        default=str,
    )


def _lane_flags(spec: dict) -> list[str]:
    """The ``supervise`` lane flags shared by the supervised-lane and evaluator
    kinds (both drive the embody supervisor loop over a lane)."""
    argv: list[str] = []
    if spec.get("all_repos"):
        argv.append("--all-repos")
    elif spec.get("repo"):
        argv += ["--repo", str(spec["repo"])]
    for label in spec.get("labels", []) or []:
        argv += ["--label", str(label)]
    argv += ["--max-concurrent", str(spec.get("max_concurrent", 1))]
    argv += ["--max-attempts", str(spec.get("max_attempts", 3))]
    for k, v in (spec.get("label_max_attempts") or {}).items():
        argv += ["--label-max-attempts", f"{k}={v}"]
    # Embody backend + per-label overrides (headless is the default; emit the flag
    # only when a spec pins 'cli' so older store-backed specs stay byte-stable).
    if spec.get("embody_backend"):
        argv += ["--embody-backend", str(spec["embody_backend"])]
    for label in spec.get("headless_labels", []) or []:
        argv += ["--headless-label", str(label)]
    for label in spec.get("cli_labels", []) or []:
        argv += ["--cli-label", str(label)]
    if spec.get("headless_agent"):
        argv += ["--headless-agent", str(spec["headless_agent"])]
    argv += ["--interval", str(spec.get("interval", 30.0))]
    # Full supervise surface (a declaration is a lossless superset of the legacy env
    # profile): these keys are absent in older store-backed specs -- emitted only
    # when present, so existing supervised-lane registrations are unaffected.
    if spec.get("heartbeat") is False:
        argv.append("--no-heartbeat")
    if spec.get("reactive") is False:
        argv.append("--no-reactive")
    if spec.get("reactive_interval") is not None:
        argv += ["--reactive-interval", str(spec["reactive_interval"])]
    if spec.get("verify_timeout"):
        argv += ["--verify-timeout", str(spec["verify_timeout"])]
    return argv


def build_command(
    reg: dict,
    *,
    python: str | None = None,
    materialize: Callable[[str, dict], str] | None = None,
) -> list[str]:
    """Build the subprocess argv that runs one registration.

    Each **kind** maps to an ``agent-dispatch`` runtime the daemon drives:

    - ``supervised-lane`` -> the embody supervisor loop (``supervise`` + lane flags);
    - ``evaluator``      -> the same loop with ``--evaluator`` (subsumes the
      foreground ``supervise --evaluator`` flag), the evaluator spec materialized
      to a file;
    - ``schedule``       -> the timer producer (``schedule serve``) over a
      one-entry spec materialized to a file (a *self-run emitter*, dedup-keyed
      ``sched:<id>:<epoch>`` by the producer);
    - ``emitter``        -> the reactive producer (``webhook``) over a config
      materialized to a file (dedup-keyed by the producer).

    Kinds that carry an inline spec dict need a ``materialize(name, spec) -> path``
    callback (the daemon supplies one that writes a per-registration file);
    building such a command without it raises. An unknown kind raises
    :class:`UnsupportedKind`.
    """
    kind = reg.get("kind")
    spec = reg.get("spec") or {}
    base = [python or sys.executable, "-m", "agent_dispatch"]

    def _need_materialize(name: str, payload: dict) -> str:
        if materialize is None:
            raise UnsupportedKind(
                f"registration kind {kind!r} needs a spec file but no materializer "
                "was provided"
            )
        return materialize(name, payload)

    if kind == RegistrationKind.SUPERVISED_LANE:
        return base + ["supervise", *_lane_flags(spec)]

    if kind == RegistrationKind.EVALUATOR:
        eval_ref = spec.get("evaluator")
        if spec.get("evaluator_spec") is not None:
            eval_ref = _need_materialize("evaluator", spec["evaluator_spec"])
        if not eval_ref:
            raise UnsupportedKind(
                "evaluator registration needs 'evaluator_spec' (inline) or "
                "'evaluator' (a path)"
            )
        return base + ["supervise", "--evaluator", str(eval_ref), *_lane_flags(spec)]

    if kind == RegistrationKind.SCHEDULE:
        entry = spec.get("schedules") and spec or {"schedules": [spec]}
        path = _need_materialize("schedule", entry)
        argv = base + ["schedule", "serve", path]
        if spec.get("interval") or (isinstance(entry, dict) and entry.get("interval")):
            argv += ["--interval", str(spec.get("interval") or entry.get("interval"))]
        return argv

    if kind == RegistrationKind.EMITTER:
        path = _need_materialize("emitter", spec)
        argv = base + ["webhook", "--config", path]
        argv += ["--host", str(spec.get("host", "127.0.0.1"))]
        argv += ["--port", str(spec.get("port", 9331))]
        return argv

    raise UnsupportedKind(
        f"daemon cannot run registration kind {kind!r}; skipping"
    )


class ProcHandle(Protocol):
    """The minimal subprocess interface the daemon drives (``subprocess.Popen``
    satisfies it; tests inject a fake)."""

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


class Launcher(Protocol):
    """Starts a registration's subprocess and returns its handle."""

    def launch(self, reg: dict, cmd: list[str]) -> ProcHandle: ...


class SubprocessLauncher:
    """Default launcher -- runs the built argv as a real, window-less child."""

    def __init__(self, *, cwd: str | None = None):
        self.cwd = cwd

    def launch(self, reg: dict, cmd: list[str]) -> ProcHandle:
        return subprocess.Popen(  # noqa: S603
            cmd,
            stdin=subprocess.DEVNULL,
            cwd=self.cwd,
            **no_window_kwargs(),
        )


@dataclass
class ManagedUnit:
    """A registration the daemon is currently running (or trying to)."""

    registration_id: str
    kind: str
    fingerprint: str
    proc: ProcHandle | None = None
    started_at: float = 0.0
    restarts: int = 0
    #: When set, the earliest time a crashed unit may be restarted (backoff).
    restart_after: float = 0.0
    #: True once the unit exceeded ``max_restarts`` -- retained (so it is not
    #: re-started) but never revived until its registration changes or is removed.
    dead: bool = False


@dataclass
class ReconcileSummary:
    """What one reconcile tick changed (returned for observability + tests)."""

    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    restarted: list[str] = field(default_factory=list)
    revived: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: Units whose subprocess is currently alive.
    running: list[str] = field(default_factory=list)
    #: Units tracked but not currently running -- a crashed unit awaiting its
    #: restart backoff (distinct from ``running`` so status output isn't misleading).
    backing_off: list[str] = field(default_factory=list)


class SupervisorDaemon:
    """The one-per-host master that reconciles the registry into subprocesses.

    Drive :meth:`reconcile_once` on a cadence (or call :meth:`serve` to loop).
    Every dependency that touches the outside world -- the launcher, the clock,
    the sleep -- is injectable, so the reconcile logic is unit-tested with fakes
    and no real processes.
    """

    def __init__(
        self,
        client: Any,
        machine: str | None,
        env: str = "default",
        *,
        launcher: Launcher | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 5.0,
        restart_backoff: float = 5.0,
        max_restarts: int | None = None,
        lock: Any | None = None,
        declared_source: Callable[[], Iterable[Any]] | None = None,
    ):
        self.client = client
        self.machine = machine
        self.env = env or "default"
        self.launcher = launcher or SubprocessLauncher()
        self.clock = clock
        self.sleep = sleep
        self.poll_interval = max(0.5, float(poll_interval))
        #: Quiet window before a crashed unit is restarted (a crash-loop damper).
        self.restart_backoff = max(0.0, float(restart_backoff))
        #: Cap on automatic restarts of a crash-looping unit (None = unbounded).
        self.max_restarts = max_restarts
        #: The single-instance lock (injectable for tests). Built lazily from the
        #: run-dir + scope when first needed, unless one was supplied.
        self._lock = lock
        #: Optional provider of the DECLARED profile set (the registrar's discovered
        #: declarations). Called every reconcile so appearing/changing/vanishing
        #: declarations are hot-reconciled alongside store-backed registrations --
        #: the *declarations-are-the-source-of-truth* path. Returns ProfileDeclaration
        #: objects; None disables declared supervision (store-only, legacy behavior).
        self.declared_source = declared_source
        #: The declared set most recently read successfully -- returned when a later
        #: discovery read *errors*, so a transient filesystem/env blip never winds
        #: down live declared units (only a successful read that no longer lists a
        #: unit does). Empty until the first successful read.
        self._last_declared: list[dict] = []
        self._units: dict[str, ManagedUnit] = {}

    # -- registry view -------------------------------------------------------

    def _declared(self) -> list[dict]:
        """The declared profile set for this (machine, env), as registrations.

        Re-read every reconcile (that is the *watch*): the daemon's existing
        start/restart/wind-down logic turns a re-read into hot-reconcile. On a read
        *error* the last successfully-read set is returned (not empty), so a transient
        discovery failure never tears down live declared units; a successful read that
        drops a unit still winds it down.
        """
        if self.declared_source is None:
            return []
        from .registrar_reconcile import declared_registrations

        try:
            decls = list(self.declared_source())
        except Exception:  # pragma: no cover - discovery is environment-dependent
            log.exception("failed to read declared profile set; keeping the last known set")
            return self._last_declared
        self._last_declared = declared_registrations(decls, machine=self.machine, env=self.env)
        return self._last_declared

    def _desired(self) -> dict[str, dict]:
        """Active registrations scoped to this daemon's (machine, env).

        Two sources, one desired set: the coordinator's store-backed registrations
        plus the registrar's **declared** profile set. Declared ids are namespaced
        (``declared:...``) to avoid colliding with *derived* store ids; if a
        caller-supplied store id ever collides, the **declared** entry wins (the
        declaration is the source of truth)."""
        regs = self.client.list_registrations(
            machine=self.machine, env=self.env, include_paused=False
        )
        desired = {r["id"]: r for r in regs}
        for reg in self._declared():
            desired[reg["id"]] = reg
        return desired

    # -- unit lifecycle ------------------------------------------------------

    def _spec_dir(self) -> Path:
        from .config import run_dir

        scope = supervisor_lease_scope(self.machine, self.env)
        slug = scope.replace(":", "-")
        d = Path(run_dir()) / "supervisor" / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _materializer(self, reg: dict) -> Callable[[str, dict], str]:
        """A per-registration spec writer: persist an inline spec dict to a stable
        file under the run dir so the kind's runtime (a subprocess) can read it.

        The path is deterministic per (registration id, name), so a restart
        rewrites the same file rather than leaking new ones.
        """
        rid = reg["id"]

        def materialize(name: str, payload: dict) -> str:
            safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in rid)
            path = self._spec_dir() / f"{safe}.{name}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return str(path)

        return materialize

    def _start(self, reg: dict, summary: ReconcileSummary, *, bucket: str) -> None:
        rid = reg["id"]
        try:
            cmd = build_command(reg, materialize=self._materializer(reg))
        except UnsupportedKind as exc:
            log.warning("skipping registration %s: %s", rid, exc)
            summary.skipped.append(rid)
            return
        try:
            proc = self.launcher.launch(reg, cmd)
        except OSError as exc:  # pragma: no cover -- launch failure is environmental
            log.error("failed to launch registration %s: %s", rid, exc)
            summary.skipped.append(rid)
            return
        existing = self._units.get(rid)
        restarts = existing.restarts if existing else 0
        self._units[rid] = ManagedUnit(
            registration_id=rid,
            kind=reg.get("kind", ""),
            fingerprint=_spec_fingerprint(reg),
            proc=proc,
            started_at=self.clock(),
            restarts=restarts,
        )
        getattr(summary, bucket).append(rid)

    def _stop(self, rid: str) -> bool:
        unit = self._units.pop(rid, None)
        if unit is None:
            return False
        proc = unit.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:  # pragma: no cover -- best-effort teardown
                log.exception("error stopping registration %s", rid)
        return True

    # -- reconcile -----------------------------------------------------------

    def reconcile_once(self) -> ReconcileSummary:
        """Bring the running set in line with the registry, once.

        Order: stop units that vanished or were paused; restart units whose spec
        changed; revive crashed units (after a backoff, up to ``max_restarts``);
        start newly-registered units. Returns a summary of what changed.
        """
        summary = ReconcileSummary()
        desired = self._desired()

        # 1. stop units no longer desired (removed or paused)
        for rid in list(self._units):
            if rid not in desired:
                if self._stop(rid):
                    summary.stopped.append(rid)

        # 2. restart units whose definition changed
        for rid, reg in desired.items():
            unit = self._units.get(rid)
            if unit is not None and _spec_fingerprint(reg) != unit.fingerprint:
                self._stop(rid)
                self._start(reg, summary, bucket="restarted")

        # 3. revive crashed units (backoff-gated, cap-bounded)
        now = self.clock()
        for rid, reg in desired.items():
            unit = self._units.get(rid)
            if unit is None or unit.proc is None:
                continue
            if unit.proc.poll() is None:
                continue  # still running
            # crashed
            if self.max_restarts is not None and unit.restarts >= self.max_restarts:
                log.error(
                    "registration %s exceeded max restarts (%d); leaving stopped",
                    rid, self.max_restarts,
                )
                unit.dead = True
                unit.proc = None
                summary.skipped.append(rid)
                continue
            if unit.restart_after and now < unit.restart_after:
                continue  # still in backoff
            unit.restarts += 1
            self._units.pop(rid, None)
            self._start(reg, summary, bucket="revived")
            revived = self._units.get(rid)
            if revived is not None:
                revived.restarts = unit.restarts
                revived.restart_after = now + self.restart_backoff

        # 4. start newly-registered units
        for rid, reg in desired.items():
            if rid not in self._units:
                self._start(reg, summary, bucket="started")

        def _alive(u: ManagedUnit) -> bool:
            return u.proc is not None and u.proc.poll() is None

        summary.running = sorted(
            rid for rid, u in self._units.items() if not u.dead and _alive(u)
        )
        summary.backing_off = sorted(
            rid for rid, u in self._units.items() if not u.dead and not _alive(u)
        )
        return summary

    # -- serve loop ----------------------------------------------------------

    def _build_lock(self) -> Any:
        from .config import run_dir
        from .single_instance import SingleInstance, lock_path_for

        scope = supervisor_lease_scope(self.machine, self.env)
        return SingleInstance(lock_path_for(run_dir(), scope))

    def acquire_singleton(self) -> bool:
        """Win the single-instance election for this (machine, env), or stand down.

        Uses a crash-safe **OS lock file** over a ``supervisor:<machine>:<env>``
        scope: the kernel releases the lock automatically if the daemon dies, so a
        restart reacquires cleanly, while a *live* second daemon is refused
        (returns ``False``) and must NOT run -- the *one supervisor per
        machine-and-environment* guarantee, without a crash leaving a permanent
        lock.
        """
        if self._lock is None:
            self._lock = self._build_lock()
        return bool(self._lock.acquire())

    def release_singleton(self) -> None:
        """Release this daemon's single-instance lock (best-effort)."""
        if self._lock is None:
            return
        try:
            self._lock.release()
        except Exception:  # pragma: no cover -- best-effort
            log.exception("error releasing supervisor lock")

    def shutdown(self) -> None:
        """Wind down every running unit (best-effort)."""
        for rid in list(self._units):
            self._stop(rid)

    def serve(
        self,
        *,
        once: bool = False,
        single_instance: bool = True,
        on_cycle: Callable[[ReconcileSummary], None] | None = None,
    ) -> int:
        """Run the daemon: reconcile each tick until interrupted.

        Returns ``0`` normally, or ``3`` if it stood down because another daemon
        already holds this scope's singleton lease. With ``once`` it runs a single
        reconcile and returns (still lease-guarded). ``single_instance=False``
        skips the election (for tests / a deliberately unguarded run).
        """
        if single_instance and not self.acquire_singleton():
            log.info(
                "another supervisor daemon already holds %s; standing down",
                supervisor_lease_scope(self.machine, self.env),
            )
            return 3
        try:
            while True:
                try:
                    summary = self.reconcile_once()
                    if on_cycle is not None:
                        on_cycle(summary)
                except KeyboardInterrupt:
                    break
                except Exception:  # pragma: no cover -- never die on a blip
                    log.exception("supervisor reconcile cycle failed")
                if once:
                    break
                try:
                    self.sleep(self.poll_interval)
                except KeyboardInterrupt:
                    break
        finally:
            self.shutdown()
            if single_instance:
                self.release_singleton()
        return 0
