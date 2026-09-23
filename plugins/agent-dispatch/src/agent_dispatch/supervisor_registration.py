"""Registration-set and process-spec primitives for the supervisor daemon.

Split out of ``supervisor_daemon.py`` (which now holds only the
``SupervisorDaemon`` class itself) purely to keep both modules under their
line-count ceiling as the repo grows. Everything here is pure/stateless --
fingerprinting a registration, diffing a desired set against what is
currently running, building a subprocess command line for a unit, and the
launcher/handle protocols the daemon spawns processes through -- with no
dependency on the daemon class. ``supervisor_daemon.py`` re-imports every
name here (see its own header) so existing ``from .supervisor_daemon
import ...`` call sites and tests are unaffected by the split.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .companion import CompanionResolution
from .procutil import no_window_kwargs
from .registrations import RegistrationKind


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


# Live self-update: the daemon notices its own running version has been
# superseded by a newer, fully-installed one (the ``current-version`` marker --
# see ``self_update.py``, shared with the coordinator's own analogous loop in
# ``coordinator.py``) and hands off to it by spawning a successor daemon from
# that version's interpreter, then exiting.
#
# Unlike the coordinator, the daemon has no HTTP routing table or in-flight
# request to race: ``reconcile_once`` runs to completion before the next tick,
# so every tick boundary is already a safe cutover point and no confirmation
# counter is needed -- ``stale_target`` itself already fails safe on a torn
# marker or an incomplete slot.
#
# Default-ON / opt-out, like the coordinator's generation self-retire
# (``AGENT_DISPATCH_SELF_RETIRE``) -- NOT opt-in like the coordinator's own
# self-update loop. There is no operator-facing protocol in this harness's
# launch paths to set an opt-in env var before a daemon's first boot, so an
# opt-in flag here would simply never get flipped and the gap in #2259 would
# persist unfixed for everyone. ``AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE=0`` (or
# false/no/off) is the escape hatch. Deliberately a *separate* flag from the
# coordinator's ``AGENT_DISPATCH_SELF_UPDATE`` -- one arms the coordinator's
# routing-table cutover, the other this daemon's respawn; sharing a flag would
# couple two independently-soaked mechanisms. Validated end-to-end (real
# process spawn, real single-instance lease handoff) by the clean-room
# ``agent-dispatch-supervisor-self-update`` scenario before defaulting on.
_SELF_UPDATE_DEFAULT_POLL_S = 60.0
_SELF_UPDATE_DEFAULT_COOLDOWN_S = 900.0
#: Distinct exit code ``serve()`` returns after handing off to a spawned
#: successor -- lets an external supervisor loop (if one is watching exit
#: codes) tell an intentional version handoff apart from a crash, though the
#: primary mechanism is the spawn itself, not this code.
SELF_UPDATE_EXIT_CODE = 42


def _self_update_settings() -> tuple[bool, float, float]:
    """``(enabled, poll_seconds, cooldown_seconds)`` for the daemon's own
    self-update loop.

    ``enabled`` is True (default-ON / opt-out) unless
    ``AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE`` is explicitly falsy
    (``0``/``false``/``no``/``off``) -- when disabled, the check never runs (see
    ``self_update_enabled`` on ``SupervisorDaemon``). Poll cadence and the
    cooldown between spawn attempts (so a spawn that fails to land isn't
    retried every tick) are overridable via
    ``AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S`` and
    ``AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_COOLDOWN_S``. A value that fails to
    parse, or parses to a non-finite float (``nan``/``inf``), falls back to the
    default.
    """
    import math
    import os

    enabled = os.environ.get(
        "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE", ""
    ).strip().lower() not in ("0", "false", "no", "off")
    try:
        poll = float(
            os.environ.get("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S", "")
            or _SELF_UPDATE_DEFAULT_POLL_S
        )
        if not math.isfinite(poll):
            raise ValueError(poll)
        poll = max(1.0, poll)
    except ValueError:
        poll = _SELF_UPDATE_DEFAULT_POLL_S
    try:
        cooldown = float(
            os.environ.get("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_COOLDOWN_S", "")
            or _SELF_UPDATE_DEFAULT_COOLDOWN_S
        )
        if not math.isfinite(cooldown):
            raise ValueError(cooldown)
        cooldown = max(0.0, cooldown)
    except ValueError:
        cooldown = _SELF_UPDATE_DEFAULT_COOLDOWN_S
    return enabled, poll, cooldown


def _spawn_self_update_successor(python_path: Any, respawn_argv: list[str]) -> None:
    """Fire-and-forget spawn of a successor daemon from *python_path*.

    ``python_path`` is the newer version's own interpreter (resolved by
    :func:`agent_dispatch.self_update.stale_target`); ``respawn_argv`` is this
    process's own ``sys.argv[1:]`` (the exact ``supervise serve ...`` CLI
    invocation, flags included), so the successor reconciles the identical
    scope this daemon was reconciling -- reusing this argv, rather than
    rebuilding one from parsed options, is what guarantees no flag (e.g.
    ``--machine``) is silently dropped.

    Does **not** itself swallow failures -- a bad *python_path* or a
    ``Popen``/exec error raises straight out of this function; the caller
    catches and logs it. Uses ``breakaway=True`` so the successor survives a
    Job-contained launch mode (this daemon may itself live in a Windows Job the
    OS can tear down as a whole tree when this process exits) -- mirrors
    ``coordinator._spawn_self_deploy``.

    Scrubs ``PYTHONPATH``/``PYTHONHOME`` from the child's environment before
    spawning. Unlike the coordinator's one-shot ``deploy`` (which exits after a
    single cutover), this loop re-evaluates the same staleness check on every
    successor -- if either var leaked in from this (stale) process and caused
    ``-m agent_dispatch`` to resolve the *old* package from the new
    interpreter, the successor would see itself as still-stale and spawn
    another successor, unbounded. The versioned slot's own interpreter already
    resolves its own site-packages without either var set.
    """
    import os

    from agent_procutil import detached_kwargs, windowless_python, windowless_python_env

    cmd = [windowless_python(python_path), "-m", "agent_dispatch", *respawn_argv]
    env = {
        k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")
    }
    env.update(windowless_python_env(python_path))
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    kwargs.update(detached_kwargs(breakaway=True))
    subprocess.Popen(cmd, **kwargs)  # noqa: S603


def _spec_fingerprint(reg: dict) -> str:
    """A stable fingerprint of a registration's runtime-relevant identity.

    Changes only when the ``kind``, ``spec``, or explicit runtime revision
    changes, so a reconcile restarts a unit exactly when its definition changed
    -- not on unrelated ``updated_at`` churn.
    """
    if reg.get("managed_launch_digest"):
        return reg["managed_launch_digest"]
    spec = dict(reg.get("spec") or {})
    spec.pop("reactive", None)
    spec.pop("reactive_interval", None)
    runtime_revision = reg.get("runtime_revision")
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
            "script_labels": [],
            "disposable_cli_labels": [],
            "headless_agent": "task-worker",
            "no_pair": False,
        }
        for key, value in defaults.items():
            if spec.get(key) is None:
                spec[key] = value
        for key in (
            "labels",
            "headless_labels",
            "cli_labels",
            "script_labels",
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
        spec["no_pair"] = bool(spec.get("no_pair"))
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
    # only when a spec pins a non-default body so older store-backed specs stay
    # byte-stable).
    if spec.get("embody_backend"):
        argv += ["--embody-backend", str(spec["embody_backend"])]
    for label in spec.get("headless_labels", []) or []:
        argv += ["--headless-label", str(label)]
    for label in spec.get("cli_labels", []) or []:
        argv += ["--cli-label", str(label)]
    for label in spec.get("script_labels", []) or []:
        argv += ["--script-label", str(label)]
    for label in spec.get("disposable_cli_labels", []) or []:
        argv += ["--disposable-cli-label", str(label)]
    if spec.get("no_pair"):
        argv.append("--no-pair")
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
    if spec.get("charter"):
        argv += ["--charter", str(spec["charter"])]
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
    companion_resolution: CompanionResolution | None = None
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
