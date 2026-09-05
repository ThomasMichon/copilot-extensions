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

import contextlib
import copy
import json
import logging
import platform
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .companion import (
    CompanionError,
    CompanionIndeterminate,
    DefaultCompanionController,
    companion_authority_fingerprint,
)
from .procutil import no_window_kwargs
from .registrations import RegistrationKind

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class UnsupportedKind(RuntimeError):
    """A registration whose kind the daemon cannot yet run in a subprocess."""


def _is_connection_error(exc: BaseException) -> bool:
    """True if ``exc`` is a network-level failure reaching the coordinator.

    Distinguishes "couldn't connect" (a moved/dead ephemeral port -- rebuild the
    client and re-resolve the rendezvous file) from a *live* coordinator returning
    an HTTP error (a :class:`~agent_dispatch.client.DispatchError`, which is a
    plain ``RuntimeError`` and must NOT trigger a reconnect). Covers builtin
    socket errors and, when installed, ``httpx`` transport errors
    (``ConnectError``/``ConnectTimeout``/``ReadError`` all subclass
    ``httpx.TransportError``).
    """
    if isinstance(exc, OSError):
        # ConnectionError and TimeoutError are OSError subclasses -- a refused,
        # reset, or timed-out socket to a moved/dead coordinator port.
        return True
    try:
        import httpx
    except Exception:  # pragma: no cover -- httpx is a hard dep in practice
        return False
    return isinstance(exc, httpx.TransportError)


def supervisor_lease_scope(machine: str | None, env: str) -> str:
    """The single-instance election scope for a host's supervisor daemon."""
    return f"supervisor:{machine or 'local'}:{env or 'default'}"


def _spec_fingerprint(reg: dict) -> str:
    """A stable fingerprint of a registration's runtime-relevant identity.

    Changes only when the ``kind``, ``spec``, or explicit runtime revision
    changes, so a reconcile restarts a unit exactly when its definition changed
    -- not on unrelated ``updated_at`` churn.
    """
    spec = dict(reg.get("spec") or {})
    spec.pop("reactive", None)
    spec.pop("reactive_interval", None)
    spec.pop("managed_runtime", None)
    runtime_revision = reg.get("runtime_revision")
    if isinstance(runtime_revision, dict):
        runtime_revision = dict(runtime_revision)
        runtime_revision.pop("managed_runtime", None)
    return json.dumps(
        {
            "kind": reg.get("kind"),
            "spec": spec,
            "runtime_revision": runtime_revision,
            "companion_runtime": reg.get("companion_runtime"),
        },
        sort_keys=True,
        default=str,
    )


def _runtime_equivalence_fingerprint(reg: dict) -> str:
    """Fingerprint effective behavior, filling omitted lane defaults."""
    kind = str(reg.get("kind") or "")
    spec = dict(reg.get("spec") or {})
    spec.pop("reactive", None)
    spec.pop("reactive_interval", None)
    if kind in {RegistrationKind.SUPERVISED_LANE, RegistrationKind.EVALUATOR}:
        defaults = {
            "labels": [],
            "max_concurrent": 1,
            "max_attempts": 3,
            "label_max_attempts": {},
            "interval": 30.0,
            "heartbeat": True,
            "verify_timeout": 0,
            "embody_backend": "headless",
            "headless_labels": [],
            "cli_labels": [],
            "disposable_cli_labels": [],
            "headless_agent": "task-worker",
        }
        for key, value in defaults.items():
            if spec.get(key) is None:
                spec[key] = value
        for key in (
            "labels",
            "headless_labels",
            "cli_labels",
            "disposable_cli_labels",
        ):
            spec[key] = sorted(set(spec.get(key) or []))
        for key in ("interval",):
            try:
                spec[key] = float(spec[key])
            except (TypeError, ValueError):
                pass
        for key in ("max_concurrent", "max_attempts", "verify_timeout"):
            try:
                spec[key] = int(spec[key])
            except (TypeError, ValueError):
                pass
        label_attempts = {}
        for key, value in (spec.get("label_max_attempts") or {}).items():
            try:
                label_attempts[str(key)] = int(value)
            except (TypeError, ValueError):
                label_attempts[str(key)] = value
        spec["label_max_attempts"] = label_attempts
    return json.dumps(
        {"kind": kind, "spec": spec},
        sort_keys=True,
        default=str,
    )


def registration_logical_ids(reg: dict) -> set[str]:
    """Stable names that can identify one unit across legacy/direct sources."""

    result = {
        str(value)
        for value in (reg.get("logical_id"), reg.get("id"))
        if value not in (None, "")
    }
    spec = reg.get("spec") or {}
    for key in ("id", "name"):
        if spec.get(key) not in (None, ""):
            result.add(str(spec[key]))
    schedules = spec.get("schedules")
    if isinstance(schedules, list) and schedules and isinstance(schedules[0], dict):
        if schedules[0].get("id") not in (None, ""):
            result.add(str(schedules[0]["id"]))
    return result


def registration_override_logical_ids(reg: dict) -> set[str]:
    """Logical names suitable for an override token, excluding the concrete id."""
    return registration_logical_ids(reg) - {str(reg.get("id") or "")}


def registration_override_ids(reg: dict) -> set[str]:
    """Concrete and logical override tokens that apply to one registration."""
    from .overrides import logical_override_id

    owner = str(reg.get("owner") or "local")
    return {
        str(reg.get("id") or ""),
        *(
            logical_override_id(owner, logical_id)
            for logical_id in registration_override_logical_ids(reg)
        ),
    } - {""}


@dataclass
class DesiredRegistrationSet:
    """One reconciled desired set plus source-migration diagnostics."""

    registrations: dict[str, dict] = field(default_factory=dict)
    deduplicated: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)


def merge_registration_sources(
    direct: Iterable[dict], declared: Iterable[dict]
) -> DesiredRegistrationSet:
    """Merge direct and declared registrations without duplicate children.

    An equivalent declaration supersedes a legacy/direct row while it is present;
    the row is retained in the coordinator store so removing the declaration
    restores it.  Same-logical-name entries with different specs are both kept and
    reported rather than silently choosing one.
    """

    direct_regs = list(direct)
    declared_regs = list(declared)
    result = DesiredRegistrationSet(
        registrations={reg["id"]: reg for reg in direct_regs}
    )
    for declared_reg in declared_regs:
        declared_fp = _runtime_equivalence_fingerprint(declared_reg)
        declared_ids = registration_logical_ids(declared_reg)
        for direct_reg in direct_regs:
            direct_id = direct_reg["id"]
            if direct_id not in result.registrations:
                continue
            if _runtime_equivalence_fingerprint(direct_reg) == declared_fp:
                result.registrations.pop(direct_id, None)
                result.deduplicated.append(direct_id)
                result.replacements[direct_id] = declared_reg["id"]
                continue
            shared = sorted(declared_ids & registration_logical_ids(direct_reg))
            if shared:
                result.conflicts.append(
                    f"{direct_id} <> {declared_reg['id']} "
                    f"(logical unit {shared[0]!r}; specs differ)"
                )
        result.registrations[declared_reg["id"]] = declared_reg
    result.deduplicated = sorted(set(result.deduplicated))
    result.conflicts = sorted(set(result.conflicts))
    return result


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
    for label in spec.get("disposable_cli_labels", []) or []:
        argv += ["--disposable-cli-label", str(label)]
    # Fleet dispatch: fan bodies across a pool of remote hosts, each driving the
    # origin task back over SSH. Mirrors ``ProfileDeclaration.to_supervise_args``;
    # emitted from spec["fleet"] (which declaration_to_spec now carries). Absent for
    # a non-fleet lane, so store-backed / non-fleet registrations are unaffected.
    fleet = spec.get("fleet") or {}
    if fleet.get("pool"):
        argv += ["--pool", ",".join(str(h) for h in fleet["pool"])]
        if fleet.get("origin"):
            argv += ["--origin", str(fleet["origin"])]
        if fleet.get("headless"):
            argv.append("--headless")
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
    - ``emitter``        -> either a periodic command emitter (``emitter serve``)
      or the legacy reactive producer (``webhook``), over a materialized config.

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
        return base + [
            "supervise",
            "--supervisor-id",
            str(reg["id"]),
            *_lane_flags(spec),
        ]

    if kind == RegistrationKind.EVALUATOR:
        eval_ref = spec.get("evaluator")
        if spec.get("evaluator_spec") is not None:
            eval_ref = _need_materialize("evaluator", spec["evaluator_spec"])
        if not eval_ref:
            raise UnsupportedKind(
                "evaluator registration needs 'evaluator_spec' (inline) or "
                "'evaluator' (a path)"
            )
        argv = base + [
            "supervise",
            "--supervisor-id",
            str(reg["id"]),
            "--evaluator",
            str(eval_ref),
        ]
        if spec.get("evaluator_ref"):
            argv += ["--evaluator-ref", str(spec["evaluator_ref"])]
        return argv + _lane_flags(spec)

    if kind == RegistrationKind.SCHEDULE:
        entry = spec.get("schedules") and spec or {"schedules": [spec]}
        path = _need_materialize("schedule", entry)
        argv = base + ["schedule", "serve", path]
        if spec.get("interval") or (isinstance(entry, dict) and entry.get("interval")):
            argv += ["--interval", str(spec.get("interval") or entry.get("interval"))]
        return argv

    if kind == RegistrationKind.EMITTER:
        path = _need_materialize("emitter", spec)
        if "command" in spec or "repository_issue_loop" in spec:
            holder = str(reg.get("machine") or platform.node() or "local")
            return base + ["emitter", "serve", path, "--holder", holder]
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
    registration: dict = field(default_factory=dict)
    companion_resolution: Any | None = None
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
    recovered: list[str] = field(default_factory=list)
    unhealthy: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: Direct registrations suppressed by equivalent declarations this cycle.
    deduplicated: list[str] = field(default_factory=list)
    #: Same-logical-id direct/declaration pairs preserved because specs differ.
    conflicts: list[str] = field(default_factory=list)
    #: Units whose subprocess is currently alive.
    running: list[str] = field(default_factory=list)
    #: Units tracked but not currently running -- a crashed unit awaiting its
    #: restart backoff (distinct from ``running`` so status output isn't misleading).
    backing_off: list[str] = field(default_factory=list)
    #: Units retained after exhausting their restart budget.
    dead: list[str] = field(default_factory=list)


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
        overrides_source: Callable[[], Mapping[str, dict]] | None = None,
        client_factory: Callable[[], Any] | None = None,
        companion_controller: Any | None = None,
        runtime_materializer: Any | None = None,
        runtime_executor: Any | None = None,
    ):
        self.client = client
        #: Rebuilds the coordinator client by **re-resolving** its endpoint (the
        #: rendezvous ``endpoint.json``). The coordinator binds an OS-assigned
        #: ephemeral port, so a coordinator restart moves the port; a daemon that
        #: cached the old port at startup would wedge -- every reconcile failing to
        #: reach a dead port. On a connection-level failure the serve loop calls
        #: this to re-resolve and reconnect, mirroring the rendezvous cutover
        #: ladder. ``None`` disables reconnect (tests inject a fixed fake client).
        self._client_factory = client_factory
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
        #: Optional provider of the operator **override** map ({registration id ->
        #: record}). Called every reconcile and its overridden-off ids are subtracted
        #: from the desired set *after* the declared + store-backed sets are merged --
        #: so an override is a higher-precedence veto that discovery cannot undo (a
        #: re-declared unit stays wound down). None defaults to reading the local
        #: ``overrides.json`` store; a callable that raises is treated as "no
        #: overrides" (best-effort, never fatal).
        self.overrides_source = overrides_source
        #: The declared set most recently read successfully -- returned when a later
        #: discovery read *errors*, so a transient filesystem/env blip never winds
        #: down live declared units (only a successful read that no longer lists a
        #: unit does). Empty until the first successful read.
        self._last_declared: list[dict] = []
        self._last_merge_diagnostics: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self._companion_controller = companion_controller
        self._runtime_materializer = runtime_materializer
        self._runtime_executor = runtime_executor
        self._owns_runtime_executor = runtime_executor is None
        self._last_companion_desired: dict[str, tuple[str, Any | None]] = {}
        self._companion_resolutions: dict[str, Any] = {}
        self._managed_runtime_authorities: dict[str, str] = {}
        self._managed_runtime_results: dict[str, tuple[Any, ...]] = {}
        self._managed_runtime_futures: dict[str, tuple[str, Future[Any]]] = {}
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

    def _overridden_off(self) -> set[str]:
        """Registration ids an operator has disabled via the override store.

        Best-effort: read via the injected ``overrides_source`` or, by default, the
        local ``overrides.json``. Any failure yields an empty set (no override in
        effect) rather than tearing down units on a bad read -- the override is a
        *stop* signal, so its absence must fail safe toward "keep running what is
        declared".
        """
        from . import overrides as ov

        try:
            if self.overrides_source is not None:
                data = self.overrides_source()
            else:
                from .config import overrides_path

                data = ov.load_overrides(overrides_path())
        except Exception:  # pragma: no cover - override read is environment-dependent
            log.exception("failed to read operator overrides; treating as none")
            return set()
        return ov.overridden_off_ids(data)

    def _desired(self) -> dict[str, dict]:
        """Active registrations scoped to this daemon's (machine, env), minus
        operator overrides.

        Three inputs, one desired set: the coordinator's store-backed registrations,
        the registrar's **declared** profile set, then the operator **override** veto.
        Declared ids are namespaced (``declared:...``) to avoid colliding with
        *derived* store ids; if a caller-supplied store id ever collides, the
        **declared** entry wins (the declaration is the source of truth). Finally,
        any id an operator has **overridden off** is dropped from the desired set --
        applied *last*, so the override outranks both the declaration and the
        discovery layer and a later re-sync cannot quietly revive the unit (vision
        Behavior *overrides-take-precedence*). A dropped id is then wound down by the
        reconcile's stop-not-desired step."""
        regs = self.client.list_registrations(
            machine=self.machine, env=self.env, include_paused=False
        )
        declared = self._declared()
        merged = merge_registration_sources(regs, declared)
        diagnostics = (tuple(merged.deduplicated), tuple(merged.conflicts))
        if diagnostics != self._last_merge_diagnostics:
            for rid in merged.deduplicated:
                log.info(
                    "equivalent declaration supersedes direct registration %s; "
                    "keeping the direct row dormant for reversible migration",
                    rid,
                )
            for conflict in merged.conflicts:
                log.warning(
                    "direct/declaration registration conflict: %s; preserving both",
                    conflict,
                )
            self._last_merge_diagnostics = diagnostics
        desired = merged.registrations
        overridden = self._overridden_off()
        for rid in overridden:
            desired.pop(rid, None)
            replacement = merged.replacements.get(rid)
            if replacement:
                desired.pop(replacement, None)
        for rid, registration in list(desired.items()):
            if registration_override_ids(registration) & overridden:
                desired.pop(rid, None)
        self._resolve_companion_desired(desired)
        self._materialize_managed_runtime_desired(desired)
        self._deduplicated = merged.deduplicated
        self._conflicts = merged.conflicts
        return desired

    def _companion(self) -> Any:
        if self._companion_controller is None:
            self._companion_controller = DefaultCompanionController(
                self._spec_dir() / "companions"
            )
        return self._companion_controller

    def _resolve_companion_desired(self, desired: dict[str, dict]) -> None:
        current: dict[str, Any] = {}
        present = {
            rid
            for rid, registration in desired.items()
            if registration.get("kind") == RegistrationKind.PLUGIN_COMPANION
        }
        for rid in set(self._last_companion_desired) - present:
            self._last_companion_desired.pop(rid, None)
        for rid in present:
            registration = desired[rid]
            authority = companion_authority_fingerprint(registration)
            try:
                resolution = self._companion().resolve(
                    registration, machine=self.machine, env=self.env
                )
            except CompanionIndeterminate as exc:
                previous = self._last_companion_desired.get(rid)
                if previous is not None and previous[0] == authority:
                    resolution = previous[1]
                    log.warning(
                        "companion %s configuration is indeterminate; "
                        "retaining its last confirmed desired state: %s",
                        rid,
                        exc,
                    )
                else:
                    desired.pop(rid, None)
                    log.warning(
                        "companion %s configuration is indeterminate without "
                        "matching confirmed authority; withholding it: %s",
                        rid,
                        exc,
                    )
                    continue
            except CompanionError as exc:
                desired.pop(rid, None)
                self._last_companion_desired.pop(rid, None)
                log.error("invalid companion %s: %s", rid, exc)
                continue
            else:
                self._last_companion_desired[rid] = (authority, resolution)

            if resolution is None:
                desired.pop(rid, None)
            else:
                desired[rid] = resolution.registration
                current[rid] = resolution
        managed_companions = {
            rid
            for rid, unit in self._units.items()
            if unit.kind == RegistrationKind.PLUGIN_COMPANION
        }
        try:
            self._companion().reconcile_receipts(
                set(current) | managed_companions
            )
        except (CompanionError, CompanionIndeterminate, OSError) as exc:
            log.warning("companion receipt reconciliation is incomplete: %s", exc)
        self._companion_resolutions = current

    def _managed_runtime(self) -> Any:
        if self._runtime_materializer is None:
            from .managed_runtime import ManagedRuntimeMaterializer

            self._runtime_materializer = ManagedRuntimeMaterializer()
        return self._runtime_materializer

    def _runtime_pool(self) -> Any:
        if self._runtime_executor is None:
            self._runtime_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="agent-dispatch-runtime",
            )
        return self._runtime_executor

    def _harvest_managed_runtime_futures(self) -> None:
        for rid, (authority, future) in list(self._managed_runtime_futures.items()):
            if not future.done():
                continue
            self._managed_runtime_futures.pop(rid, None)
            try:
                result = tuple(future.result())
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                log.error("failed to materialize companion runtime %s: %s", rid, exc)
                continue
            self._managed_runtime_authorities[rid] = authority
            self._managed_runtime_results[rid] = result

    def _materialize_managed_runtime_desired(
        self, desired: Mapping[str, dict]
    ) -> None:
        """Prepare declared runtime cells without changing companion launch state."""
        self._harvest_managed_runtime_futures()
        present = {
            rid
            for rid, registration in desired.items()
            if registration.get("kind") == RegistrationKind.PLUGIN_COMPANION
            and isinstance(registration.get("spec"), dict)
            and registration["spec"].get("managed_runtime")
        }
        for rid in set(self._managed_runtime_authorities) - present:
            self._managed_runtime_authorities.pop(rid, None)
            self._managed_runtime_results.pop(rid, None)
        for rid, (_authority, future) in list(self._managed_runtime_futures.items()):
            if rid not in present and future.cancel():
                self._managed_runtime_futures.pop(rid, None)
        for rid in present:
            registration = desired[rid]
            authority = companion_authority_fingerprint(registration)
            if self._managed_runtime_authorities.get(rid) == authority:
                continue
            pending = self._managed_runtime_futures.get(rid)
            if pending is not None:
                continue
            future = self._runtime_pool().submit(
                self._managed_runtime().materialize,
                copy.deepcopy(registration),
            )
            self._managed_runtime_futures[rid] = (authority, future)
        self._harvest_managed_runtime_futures()

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
        resolution = self._companion_resolutions.get(rid)
        if reg.get("kind") == RegistrationKind.PLUGIN_COMPANION:
            if resolution is None:
                summary.skipped.append(rid)
                return
            try:
                launched = self._companion().launch(
                    resolution, fingerprint=_spec_fingerprint(reg)
                )
            except (CompanionError, CompanionIndeterminate, OSError) as exc:
                log.error("failed to launch companion %s: %s", rid, exc)
                summary.skipped.append(rid)
                return
            proc = launched.process
        else:
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
            registration=reg,
            companion_resolution=resolution,
            proc=proc,
            started_at=self.clock(),
            restarts=restarts,
        )
        if reg.get("kind") == RegistrationKind.PLUGIN_COMPANION and launched.recovered:
            summary.recovered.append(rid)
        else:
            getattr(summary, bucket).append(rid)

    def _stop(self, rid: str) -> bool:
        unit = self._units.pop(rid, None)
        if unit is None:
            return False
        proc = unit.proc
        if proc is not None and unit.kind == RegistrationKind.PLUGIN_COMPANION:
            try:
                self._companion().stop(unit.companion_resolution, proc)
            except (CompanionError, CompanionIndeterminate, OSError, subprocess.SubprocessError):
                log.exception("error stopping companion %s; forcing retirement", rid)
                with contextlib.suppress(
                    CompanionError,
                    CompanionIndeterminate,
                    OSError,
                    subprocess.SubprocessError,
                ):
                    self._companion().retire_crashed(
                        unit.companion_resolution, proc
                    )
        elif proc is not None and proc.poll() is None:
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
        summary.deduplicated = list(self._deduplicated)
        summary.conflicts = list(self._conflicts)

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
            if (
                unit.kind == RegistrationKind.PLUGIN_COMPANION
                and unit.proc.poll() is None
            ):
                try:
                    healthy = self._companion().health(
                        unit.companion_resolution
                    )
                except (
                    CompanionError,
                    CompanionIndeterminate,
                    OSError,
                    subprocess.SubprocessError,
                ) as exc:
                    log.warning(
                        "companion %s health is indeterminate; keeping it: %s",
                        rid,
                        exc,
                    )
                else:
                    if healthy is False:
                        summary.unhealthy.append(rid)
                        self._stop(rid)
                        self._start(reg, summary, bucket="restarted")
                        continue
            if unit.proc.poll() is None:
                continue  # still running
            # crashed
            if unit.kind == RegistrationKind.PLUGIN_COMPANION:
                try:
                    self._companion().retire_crashed(
                        unit.companion_resolution, unit.proc
                    )
                except (
                    CompanionError,
                    CompanionIndeterminate,
                    OSError,
                    subprocess.SubprocessError,
                ) as exc:
                    log.error(
                        "could not retire crashed companion %s tree: %s",
                        rid,
                        exc,
                    )
                    summary.skipped.append(rid)
                    continue
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
        summary.dead = sorted(rid for rid, u in self._units.items() if u.dead)
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
        if self._runtime_executor is not None and self._owns_runtime_executor:
            self._runtime_executor.shutdown(wait=False, cancel_futures=True)

    def _reconnect(self) -> bool:
        """Rebuild the coordinator client by re-resolving its endpoint.

        Called when a reconcile cycle fails at the connection level -- the classic
        cause being a coordinator restart that moved its OS-assigned ephemeral
        port, leaving this daemon pointed at a dead one. Re-resolving reads the
        fresh rendezvous ``endpoint.json`` so the next tick reaches the live
        coordinator. Best-effort: a factory that itself raises leaves the old
        client in place and the loop simply retries next tick. Returns True when
        the client was rebuilt.
        """
        if self._client_factory is None:
            return False
        try:
            new_client = self._client_factory()
        except Exception:  # pragma: no cover -- re-resolve is environment-dependent
            log.exception("failed to re-resolve coordinator endpoint; will retry")
            return False
        old = self.client
        self.client = new_client
        close = getattr(old, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
        log.info("re-resolved coordinator endpoint after a connection failure")
        return True

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
                except Exception as exc:  # pragma: no cover -- never die on a blip
                    log.exception("supervisor reconcile cycle failed")
                    # A connection-level failure most likely means the coordinator
                    # restarted onto a new ephemeral port; re-resolve its endpoint
                    # so the next tick reconnects instead of wedging forever.
                    if _is_connection_error(exc):
                        self._reconnect()
                if once:
                    break
                try:
                    self.sleep(self.poll_interval)
                except KeyboardInterrupt:
                    break
        finally:
            self.shutdown()
            if self._client_factory is not None:
                close = getattr(self.client, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
            if single_instance:
                self.release_singleton()
        return 0
