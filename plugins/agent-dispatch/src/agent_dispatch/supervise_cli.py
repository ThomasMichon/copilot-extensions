"""``supervise`` CLI command family, extracted from ``__main__.py``.

Registration scoping/status-file helpers plus the ``supervise
register|status|list|remove|serve|daemon-status|override`` subcommands and
the transitional bare-``supervise`` foreground loop. Split out to keep
``__main__.py`` under its module-size ceiling (see
``tools/check-module-size.py``) rather than growing an already very large
file further -- this is purely a move, no behavior change.

This module still needs a handful of names that genuinely belong to
``__main__.py`` (``_client``, ``_emit``, ``_scope_repo``, ``_spawn_route``,
``_parse_label_max_attempts``, ``_REPO_UNRESOLVED``). ``python -m
agent_dispatch`` (the marketplace launcher) loads ``__main__.py`` as
``sys.modules["__main__"]``, never as ``sys.modules["agent_dispatch.__main__"]``
-- a naive ``from .__main__ import X`` would therefore import and execute an
*independent second copy* of that module rather than the one actually
running, silently diverging any monkeypatched/mutated state (tests patch the
live module). ``loop_commands._resolve_cli_module()`` already solves exactly
this for ``loop_commands.py``; reuse it here via the same ``_proxy()``
pattern instead of duplicating the resolution logic.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .loop_commands import _resolve_cli_module


def _proxy(name: str):
    """Delegate to ``agent_dispatch.__main__.<name>`` via ``_resolve_cli_module``."""

    def _fn(*args, **kwargs):
        return getattr(_resolve_cli_module(), name)(*args, **kwargs)

    return _fn


_client = _proxy("_client")
_emit = _proxy("_emit")
_scope_repo = _proxy("_scope_repo")
_spawn_route = _proxy("_spawn_route")
_parse_label_max_attempts = _proxy("_parse_label_max_attempts")

def _registration_scope(args: argparse.Namespace) -> tuple[str | None, str]:
    """Resolve the (machine, env) a registration is scoped to.

    ``--machine`` / ``--env`` win; otherwise the machine is this host's resolved
    alias and the env is ``AGENT_DISPATCH_ENV`` (default ``"default"``). This is
    the *one supervisor per machine-and-environment* the registration binds to.
    """
    import os

    from . import remote_dispatch

    machine = getattr(args, "machine", None) or remote_dispatch.local_machine()
    env = getattr(args, "env", None) or os.environ.get("AGENT_DISPATCH_ENV") or "default"
    return machine, env


def _supervisor_runtime_status_path(scope: str) -> Path:
    from .config import run_dir
    from .single_instance import lock_path_for

    return lock_path_for(run_dir(), scope).with_suffix(".status.json")


def _write_supervisor_runtime_status(scope: str, summary: Any) -> None:
    path = _supervisor_runtime_status_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve cycle_started_at from the just-finished cycle's own start-marker
    # write (below) rather than recomputing it, so a reader can diff
    # updated_at (this, the finish time) against cycle_started_at to see the
    # last cycle's own duration, not just "when did anything last happen."
    previous, _err = _read_supervisor_runtime_status(scope)
    payload = {
        "updated_at": time.time(),
        "cycle_started_at": (previous or {}).get("cycle_started_at"),
        "cycle_id": (previous or {}).get("cycle_id"),
        "running": summary.running,
        "backing_off": summary.backing_off,
        "dead": getattr(summary, "dead", []),
    }
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _write_supervisor_cycle_start(scope: str, cycle_id: int, started_at: float) -> None:
    """Mark a reconcile cycle as started, *before* it runs.

    Written and read independently of :func:`_write_supervisor_runtime_status`
    (which only lands after a cycle **finishes**) so a stuck cycle is
    detectable while it is still stuck: ``cycle_started_at`` advances every
    cycle regardless of outcome, while ``updated_at`` only advances on a
    completed one. A large, growing gap between the two -- not just an old
    ``updated_at`` alone -- is the specific, unambiguous signature of a wedge
    (the process alive but blocked inside one reconcile call), distinct from a
    dead process (no writer at all) or a genuinely idle one (both advance
    together, cheaply, every ``poll_interval``).
    """
    path = _supervisor_runtime_status_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous, _err = _read_supervisor_runtime_status(scope)
    payload = dict(previous or {})
    payload["cycle_started_at"] = started_at
    payload["cycle_id"] = cycle_id
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _read_supervisor_runtime_status(scope: str) -> tuple[dict | None, str | None]:
    path = _supervisor_runtime_status_path(scope)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, f"{path}: expected a JSON object"
    return payload, None


def _build_registration_spec(args: argparse.Namespace) -> dict:
    """Assemble the ``spec`` dict a registration stores from the register args.

    An explicit ``--spec`` (inline JSON or ``@path``) is used verbatim for any
    kind; otherwise a ``supervised-lane`` spec is built from the convenience lane
    flags (repo/labels/limits/evaluator) so the singleton daemon can later
    reconstruct the supervise invocation from the stored row.
    """
    raw = getattr(args, "spec", None)
    if raw:
        text = raw
        if raw.startswith("@"):
            try:
                text = Path(raw[1:]).expanduser().read_text(encoding="utf-8")
            except OSError as exc:
                raise SystemExit(
                    f"supervise register: could not read --spec file {raw[1:]!r}: {exc}"
                ) from exc
        try:
            spec = json.loads(text)
        except ValueError as exc:
            raise SystemExit(f"supervise register: bad --spec JSON: {exc}") from exc
        if not isinstance(spec, dict):
            raise SystemExit("supervise register: --spec must be a JSON object")
        return spec

    kind = getattr(args, "kind", None) or "supervised-lane"
    if kind not in ("supervised-lane", "evaluator"):
        raise SystemExit(f"supervise register: --spec is required for kind {kind!r}")

    all_repos = bool(getattr(args, "all_repos", False))
    spec: dict = {}
    if all_repos:
        spec["all_repos"] = True
    else:
        repo = _scope_repo(args)
        if not repo:
            raise SystemExit(
                "supervise register: could not resolve a lane; pass --repo or --all-repos"
            )
        spec["repo"] = repo
    labels = [label for label in (getattr(args, "label", None) or []) if label]
    if labels:
        spec["labels"] = labels
    spec["max_concurrent"] = getattr(args, "max_concurrent", 1)
    spec["max_attempts"] = getattr(args, "max_attempts", 3)
    lma = _parse_label_max_attempts(getattr(args, "label_max_attempts", None))
    if lma:
        spec["label_max_attempts"] = lma
    # Embody backend default is headless; record it (+ any per-label overrides) so
    # the daemon rebuilds the same lane. Omit the default to keep older specs stable.
    backend = getattr(args, "embody_backend", None) or "headless"
    if backend != "headless":
        spec["embody_backend"] = backend
    headless = [label for label in (getattr(args, "headless_label", None) or []) if label]
    if headless:
        spec["headless_labels"] = headless
    cli = [label for label in (getattr(args, "cli_label", None) or []) if label]
    if cli:
        spec["cli_labels"] = cli
    script = [label for label in (getattr(args, "script_label", None) or []) if label]
    if script:
        spec["script_labels"] = script
    disposable_cli = [
        label for label in (getattr(args, "disposable_cli_label", None) or []) if label
    ]
    if disposable_cli:
        spec["disposable_cli_labels"] = disposable_cli
    if getattr(args, "headless_agent", None):
        spec["headless_agent"] = args.headless_agent
    if getattr(args, "charter", None):
        # Mirrors _cmd_supervise's own runtime guard (and Body.__post_init__ for
        # the YAML-declaration path): agent-worktrees embody has no
        # charter-binding flag, so reject the combination here too rather than
        # persisting a registration the daemon can never actually start.
        if backend in ("cli", "script") or cli or script:
            raise SystemExit(
                "supervise register: --charter is only supported for a fully "
                "headless lane today (agent-worktrees embody has no "
                "charter-binding flag) -- drop --embody-backend cli/script and "
                "any --cli-label/--script-label, or drop --charter."
            )
        spec["charter"] = args.charter
    if getattr(args, "evaluator", None):
        if kind != "evaluator":
            raise SystemExit(
                "supervise register: --evaluator is only valid with "
                "--kind evaluator (a supervised-lane does not run an evaluator)"
            )
        spec["evaluator"] = args.evaluator
    if getattr(args, "evaluator_ref", None):
        if kind != "evaluator":
            raise SystemExit(
                "supervise register: --evaluator-ref is only valid with --kind evaluator"
            )
        spec["evaluator_ref"] = args.evaluator_ref
    spec["interval"] = getattr(args, "interval", 30.0)
    if kind == "evaluator" and not spec.get("evaluator"):
        raise SystemExit(
            "supervise register --kind evaluator: pass --evaluator <spec-path> "
            "(or --spec with an inline 'evaluator_spec')"
        )
    return spec


def _cmd_supervise_register(args: argparse.Namespace) -> int:
    """``supervise register`` -- add a durable registration and RETURN its handle.

    This is the *supervise-registers-and-returns* behavior: registering supervised
    work writes a registration row and completes, emitting the registration info
    back to the caller, instead of becoming the foreground loop. The singleton
    supervisor daemon (a later increment) is what runs the registered unit.
    """
    kind = getattr(args, "kind", None) or "supervised-lane"
    spec = _build_registration_spec(args)
    machine, env = _registration_scope(args)
    with _client(args) as c:
        rec = c.register_registration(
            kind,
            spec,
            reg_id=getattr(args, "id", None),
            machine=machine,
            env=env,
        )
    if getattr(args, "ensure", False):
        rec = {**rec, "daemon": _ensure_supervisor_daemon(args, machine, env)}
    return _emit(rec)


def _cmd_supervise_status(args: argparse.Namespace) -> int:
    """``supervise status <id>`` -- query a registration by its handle."""
    with _client(args) as c:
        return _emit(c.get_registration(args.id))


def _cmd_supervise_list(args: argparse.Namespace) -> int:
    """``supervise list`` -- list registrations on this (or a filtered) scope."""
    machine = getattr(args, "machine", None)
    env = getattr(args, "env", None)
    with _client(args) as c:
        return _emit(
            c.list_registrations(
                kind=getattr(args, "kind", None),
                machine=machine,
                env=env,
                include_paused=not getattr(args, "active", False),
            )
        )


def _cmd_supervise_remove(args: argparse.Namespace) -> int:
    """``supervise remove <id>`` -- drop a registration by its handle."""
    with _client(args) as c:
        return _emit(c.remove_registration(args.id))


def _spawn_supervisor_daemon_detached(machine: str | None, env: str) -> bool:
    """Best-effort: launch the singleton supervisor daemon as a detached child.

    Runs ``agent-dispatch supervise serve`` for this (machine, env) fully detached
    so it outlives this CLI process. If a daemon is already running the new child's
    single-instance election stands it down cleanly (pin-not-failover), so a double
    launch is self-correcting. Returns whether the spawn was issued.
    """
    from .procutil import resolve_own_runtime_python, runtime_root, windowless_daemon_kwargs

    # Canonically-resolved current-version slot, not sys.executable -- see
    # resolve_own_runtime_python's docstring for why a raw sys.executable here is
    # a footgun (this is the sibling spawn site to _spawn_coordinator_process).
    argv = [resolve_own_runtime_python(), "-m", "agent_dispatch", "supervise", "serve"]
    if machine:
        argv += ["--machine", machine]
    if env:
        argv += ["--env", env]
    try:
        subprocess.Popen(  # noqa: S603
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Launch from the runtime root, never an inherited (possibly payload) CWD.
            cwd=str(runtime_root()),
            **windowless_daemon_kwargs(),
        )
        return True
    except OSError as exc:  # pragma: no cover -- launch failure is environmental
        print(f"supervise register: could not ensure daemon: {exc}", file=sys.stderr)
        return False


def _ensure_supervisor_daemon(args: argparse.Namespace, machine: str | None, env: str) -> dict:
    """Ensure a singleton supervisor daemon is running for this (machine, env).

    ``--ensure`` only ever ensures a daemon for the *local* host. A registration's
    ``--machine`` names where the work should run, not an instruction for this
    invocation to become that machine: a scope that names a different machine
    than this host's own resolved identity is a foreign scope and is a no-op
    here -- silently starting a same-named-but-elsewhere supervisor process
    would leave a persistent, wrongly-scoped daemon running on the wrong box
    with nothing (a local ``daemon-status``/``supervise list`` check included)
    to reveal it (github.com/ThomasMichon/copilot-extensions#2791). The correct
    machine's *own* session is expected to run its own ``--ensure`` (or its
    already-installed supervisor service) for its own scope.

    Otherwise, checks the supervisor lease first; if a daemon already holds it,
    this is a no-op. Otherwise it launches one detached. Best-effort and
    fail-soft -- a failure to ensure never fails the register call.
    """
    from . import remote_dispatch
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for
    from .supervisor_daemon import supervisor_lease_scope

    if remote_dispatch.is_peer_machine(machine):
        local = remote_dispatch.local_machine()
        print(
            f"supervise register --ensure: scope names machine '{machine}', "
            f"but this host resolves as '{local}' -- not starting a local "
            "supervisor for a different machine's scope. Run --ensure on "
            f"'{machine}' itself (or its own installed supervisor service) "
            "to ensure that scope.",
            file=sys.stderr,
        )
        return {"ensured": False, "reason": "not-local-machine", "local_machine": local}

    scope = supervisor_lease_scope(machine, env)
    try:
        if is_locked(lock_path_for(run_dir(), scope)):
            return {"ensured": False, "reason": "already running"}
    except OSError:  # noqa: BLE001 -- fail-soft; fall through to a spawn attempt
        pass
    spawned = _spawn_supervisor_daemon_detached(machine, env)
    return {"ensured": spawned, "reason": "spawned" if spawned else "spawn failed"}


def _cmd_supervise_serve(args: argparse.Namespace) -> int:
    """``supervise serve`` -- run the singleton supervisor daemon (foreground).

    One master per (machine, env): reads the registration registry and runs each
    active registration in its own subprocess, reconciling on every tick.
    Single-instance-guarded -- a second daemon for the same scope stands down.
    """
    # Never hold the Copilot plugin payload dir as CWD (Windows locks it against
    # `copilot plugin update`); this daemon is lazy-started and inherits the
    # launching session's CWD. Relocate before the long-lived loop.
    from . import procutil
    from .supervisor_daemon import SupervisorDaemon, supervisor_lease_scope

    procutil.relocate_off_payload()

    machine, env = _registration_scope(args)

    if machine is None and not getattr(args, "no_declared", False):
        # Fail-loud companion to the fail-closed reconcile: an unidentified host
        # will SKIP every machine-pinned declaration (it cannot confirm membership),
        # so a discovered machine-scoped pool would silently never run. Surface it
        # so the operator can pass --machine (or fix host identity) -- aperture-labs
        # #5001.
        print(
            "agent-dispatch supervise serve: WARNING -- could not resolve this "
            "host's machine name; machine-scoped declarations will be SKIPPED "
            "(machine-agnostic ones still run). Pass --machine <alias> to scope "
            "this daemon.",
            file=sys.stderr,
        )

    declared_source = None
    if not getattr(args, "no_declared", False):
        from . import registrar_discovery

        registrar_sources = registrar_discovery.RegistrarSources()
        if getattr(args, "legacy_env", False):
            # Back-compat bridge: pointer-discovered declarations PLUS the host's
            # legacy supervisor.env / supervisors/*.env profiles, deduped by name
            # (a first-class declaration wins). Lets a host switch its supervisor
            # unit to `serve` without dropping its existing profiles mid-migration.
            declared_source = registrar_sources.discover_with_legacy
        else:
            declared_source = registrar_sources.discover
    elif getattr(args, "legacy_env", False):
        # --no-declared drops pointer discovery but --legacy-env still bridges the
        # legacy env profiles.
        from . import registrar_discovery

        declared_source = registrar_discovery.read_legacy_env_profiles

    from .supervisor_daemon import _self_update_settings

    _su_enabled, _su_poll, _su_cooldown = _self_update_settings()

    with _client(args) as c:
        daemon = SupervisorDaemon(
            c,
            machine,
            env,
            poll_interval=getattr(args, "interval", 5.0),
            declared_source=declared_source,
            # Rebuild the client by re-resolving the coordinator endpoint after a
            # connection failure -- the coordinator's ephemeral port moves on
            # restart, so a cached one would wedge the daemon (#3825).
            client_factory=lambda: _client(args, ensure=False),
            # Live self-update (#2259): default-ON / opt-out via
            # AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE=0. Reuses this process's own
            # sys.argv[1:] to respawn the successor with the identical
            # `supervise serve ...` invocation (flags included), so no manual
            # argv reconstruction can silently drop one.
            self_update_enabled=_su_enabled,
            self_update_poll_interval=_su_poll,
            self_update_cooldown=_su_cooldown,
            self_update_argv=list(sys.argv[1:]) if _su_enabled else None,
        )

        def _on_cycle_start(cycle_id: int, started_at: float) -> None:
            _write_supervisor_cycle_start(
                supervisor_lease_scope(machine, env),
                cycle_id,
                started_at,
            )

        def _on_cycle(summary) -> None:
            _write_supervisor_runtime_status(
                supervisor_lease_scope(machine, env),
                summary,
            )
            changed = summary.started or summary.stopped or summary.restarted or summary.revived
            if changed:
                print(
                    f"supervise serve: started={summary.started} "
                    f"stopped={summary.stopped} restarted={summary.restarted} "
                    f"revived={summary.revived} running={summary.running} "
                    f"backing_off={summary.backing_off}",
                    file=sys.stderr,
                )

        return daemon.serve(
            once=getattr(args, "once", False),
            single_instance=not getattr(args, "no_single_instance", False),
            on_cycle=_on_cycle,
            on_cycle_start=_on_cycle_start,
        )


def _cmd_supervise_daemon_status(args: argparse.Namespace) -> int:
    """``supervise daemon-status`` -- is a daemon running here, and what would it
    run."""
    from .config import overrides_path, run_dir
    from .overrides import load_overrides, overridden_off_ids
    from .single_instance import is_locked, lock_path_for
    from .supervisor_daemon import (
        registration_override_ids,
        supervisor_lease_scope,
    )

    machine, env = _registration_scope(args)
    scope = supervisor_lease_scope(machine, env)
    running = is_locked(lock_path_for(run_dir(), scope))
    with _client(args) as c:
        regs = c.list_registrations(machine=machine, env=env, include_paused=True)
    overrides = load_overrides(overrides_path())
    off = overridden_off_ids(overrides)
    # Annotate each registration with its override state so the overridden-off set
    # is legible right beside what is declared/registered (vision: legibility).
    for reg in regs:
        matching = sorted(registration_override_ids(reg) & off)
        if matching:
            reg["overridden_off"] = True
            reg["override_ids"] = matching
            rec = overrides.get(matching[0]) or {}
            reg["override_reason"] = rec.get("reason")
    return _emit(
        {
            "scope": scope,
            "machine": machine,
            "env": env,
            "running": running,
            "registrations": regs,
            "overrides": overrides,
        }
    )


def _cmd_supervise_override(args: argparse.Namespace) -> int:
    """``supervise override {disable,enable,list}`` -- the operator kill-switch.

    A fast, local, reversible enable/disable veto on a supervised unit (addressed
    by its registration id), applied out of band via the local override store. The
    daemon subtracts overridden-off ids from its desired set on the next reconcile,
    so a disabled unit winds down and stays down until re-enabled -- even across a
    repo re-sync of its declaration.
    """
    from .config import overrides_path
    from .overrides import (
        clear_override,
        load_overrides,
        overridden_off_ids,
        set_override,
    )

    action = getattr(args, "override_command", None)
    path = overrides_path()
    if action == "disable":
        record = set_override(path, args.id, disabled=True, reason=getattr(args, "reason", None))
        return _emit({"id": args.id, "overridden_off": True, **record})
    if action == "enable":
        cleared = clear_override(path, args.id)
        return _emit({"id": args.id, "overridden_off": False, "cleared": cleared})
    # list (default)
    overrides = load_overrides(path)
    off = sorted(overridden_off_ids(overrides))
    return _emit(
        {
            "path": str(path),
            "overridden_off": off,
            "overrides": overrides,
        }
    )


def _cmd_supervise(args: argparse.Namespace) -> int:
    """Run the embody spawn supervisor over the lane (once, or as a loop).

    Turns queued (optionally label-gated) tasks into host embody autopilots,
    exactly once each, via the atomic spawn reservation. See the ``supervisor``
    module for the spawn-at-most-once safety model.

    A ``supervise <register|status|list|remove>`` subcommand instead manages
    durable **registrations** (the *registered-supervision* surface): registering
    adds a unit and returns its handle rather than becoming this loop. The bare
    ``supervise`` (no subcommand) remains the transitional foreground loop until
    the singleton daemon subsumes it.
    """
    sub = getattr(args, "supervise_command", None)
    if sub == "register":
        return _cmd_supervise_register(args)
    if sub == "status":
        return _cmd_supervise_status(args)
    if sub == "list":
        return _cmd_supervise_list(args)
    if sub == "remove":
        return _cmd_supervise_remove(args)
    if sub == "serve":
        return _cmd_supervise_serve(args)
    if sub == "daemon-status":
        return _cmd_supervise_daemon_status(args)
    if sub == "override":
        return _cmd_supervise_override(args)

    from .supervisor import (
        Supervisor,
        make_embody_spawn,
        make_headless_spawn,
        make_label_routed_spawn,
        make_script_spawn,
        make_redrive_sender,
    )

    all_repos = bool(getattr(args, "all_repos", False))
    repo = None if all_repos else _scope_repo(args)
    if not all_repos and not repo:
        print(_resolve_cli_module()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    pool = [h for h in (getattr(args, "pool", "") or "").split(",") if h.strip()]
    # Embody backend default is HEADLESS: a dispatched/supervised task is a
    # self-contained, autonomous body that needs no human attach, and headless
    # sidesteps the CLI-start-prompt path entirely. `--embody-backend cli` opts the
    # whole lane back to CLI/mux (attachable); `--embody-backend script` opts the
    # whole lane to a plain deterministic subprocess. Per-label overrides fine-tune
    # either way (`--cli-label` forces CLI when the default is headless/script;
    # `--headless-label` or `--script-label` force those bodies when the default
    # is CLI or one another).
    backend = getattr(args, "embody_backend", None) or "headless"
    headless_labels = [label for label in (getattr(args, "headless_label", None) or []) if label]
    cli_labels = [label for label in (getattr(args, "cli_label", None) or []) if label]
    script_labels = [label for label in (getattr(args, "script_label", None) or []) if label]
    disposable_cli_labels = [
        label for label in (getattr(args, "disposable_cli_label", None) or []) if label
    ]
    charter = getattr(args, "charter", None)
    if charter and (backend in ("cli", "script") or cli_labels or script_labels):
        print(
            "agent-dispatch supervise: --charter is only supported for a fully "
            "headless lane today (agent-worktrees embody has no charter-binding "
            "flag yet) -- drop --embody-backend cli/script and any "
            "--cli-label/--script-label, or drop --charter.",
            file=sys.stderr,
        )
        return 2
    capacity_gate = None
    redrive_fn = None
    if pool:
        from . import remote_dispatch
        from .fleet import FleetSpawner

        origin = getattr(args, "origin", None) or remote_dispatch.local_machine()
        if not origin:
            print(
                "agent-dispatch supervise --pool: could not resolve this machine's "
                "alias for fleet bodies to report back to; pass --origin <alias>.",
                file=sys.stderr,
            )
            return 2
        # Fleet bodies are headless by default too (the `--headless` flag remains an
        # explicit force); only `--embody-backend cli` makes fleet bodies CLI.
        fleet_headless = bool(getattr(args, "headless", False)) or backend != "cli"
        if charter and not fleet_headless:
            print(
                "agent-dispatch supervise: --charter is only supported for a "
                "headless fleet lane today -- drop --embody-backend cli, or "
                "drop --charter.",
                file=sys.stderr,
            )
            return 2
        if disposable_cli_labels:
            print(
                "agent-dispatch supervise: --disposable-cli-label is supported "
                "only for local worker bodies.",
                file=sys.stderr,
            )
            return 2
        if backend == "script" or script_labels:
            print(
                "agent-dispatch supervise: the script embodiment is supported "
                "only for local (non-pool) worker bodies.",
                file=sys.stderr,
            )
            return 2
        if getattr(args, "no_pair", False):
            print(
                "agent-dispatch supervise: --no-pair is supported only for "
                "local worker bodies.",
                file=sys.stderr,
            )
            return 2
        fleet = FleetSpawner(
            pool,
            origin=origin,
            headless=fleet_headless,
            agent=getattr(args, "headless_agent", None) or "task-worker",
            charter=getattr(args, "charter", None),
            all_repos=all_repos,
            verify_timeout=getattr(args, "verify_timeout", 0) or 0,
        )
        spawn_fn = fleet
        capacity_gate = fleet.can_spawn
        if headless_labels or cli_labels or script_labels:
            print(
                "agent-dispatch supervise: per-label --headless-label/--cli-label/"
                "--script-label are ignored in fleet (--pool) mode; the whole pool is "
                f"{'headless' if fleet_headless else 'CLI'} "
                "(set --embody-backend to change).",
                file=sys.stderr,
            )
        body = "headless agent-bridge ACP" if fleet_headless else "CLI-embodied"
        print(
            f"agent-dispatch supervise: fleet mode -- pool={','.join(fleet.pool)} "
            f"origin={origin} body={body}",
            file=sys.stderr,
        )
        # Preflight only a headless fleet lane: a CLI-embodied fleet body is a
        # worktree autopilot, not an agent-bridge agent.
        _preflight_agent = (
            (getattr(args, "headless_agent", None) or "task-worker") if fleet_headless else None
        )
        _preflight_pool = list(fleet.pool)
    else:
        # Local (non-fleet) spawn: hand the worker its coordinator routing intent
        # (discovery for the default local coordinator, or the --shared moniker);
        # a raw --url is refused here (a spawned local body must not be pinned to a
        # raw, possibly-dynamic endpoint).
        route = _spawn_route(args)
        redrive_fn = make_redrive_sender(route=route)
        headless_spawn = make_headless_spawn(
            agent=getattr(args, "headless_agent", None) or "task-worker",
            charter=getattr(args, "charter", None),
            route=route,
            all_repos=all_repos,
            no_pair=bool(getattr(args, "no_pair", False)),
        )
        script_spawn = make_script_spawn(
            route=route,
            all_repos=all_repos,
        )
        embody_spawn = make_embody_spawn(
            verify_timeout=getattr(args, "verify_timeout", 0) or 0,
            route=route,
            all_repos=all_repos,
            no_pair=bool(getattr(args, "no_pair", False)),
            charter=getattr(args, "charter", None),
        )
        watched = set(args.label or [])
        disposable = set(disposable_cli_labels)
        if disposable - watched:
            print(
                "agent-dispatch supervise: every --disposable-cli-label must "
                "also be watched by --label.",
                file=sys.stderr,
            )
            return 2
        if backend == "cli":
            default_spawn = embody_spawn
            overrides = {label: headless_spawn for label in headless_labels}
            overrides.update({label: script_spawn for label in script_labels})
            routed_parts = []
            if headless_labels:
                routed_parts.append(f"headless-ACP for label(s): {', '.join(headless_labels)}")
            if script_labels:
                routed_parts.append(f"script for label(s): {', '.join(script_labels)}")
            routed_note = (
                "CLI embody; " + "; ".join(routed_parts)
                if routed_parts
                else "CLI embody (all watched labels)"
            )
        elif backend == "script":
            default_spawn = script_spawn
            overrides = {label: embody_spawn for label in cli_labels}
            overrides.update({label: headless_spawn for label in headless_labels})
            routed_parts = []
            if cli_labels:
                routed_parts.append(f"CLI for label(s): {', '.join(cli_labels)}")
            if headless_labels:
                routed_parts.append(f"headless-ACP for label(s): {', '.join(headless_labels)}")
            routed_note = (
                "script embody; " + "; ".join(routed_parts)
                if routed_parts
                else "script embody (all watched labels)"
            )
        else:
            # Headless-default lane (the default): CLI/script are the per-label opt-out.
            default_spawn = headless_spawn
            overrides = {label: embody_spawn for label in cli_labels}
            overrides.update({label: script_spawn for label in script_labels})
            routed_parts = []
            if cli_labels:
                routed_parts.append(f"CLI for label(s): {', '.join(cli_labels)}")
            if script_labels:
                routed_parts.append(f"script for label(s): {', '.join(script_labels)}")
            routed_note = (
                "headless-ACP embody; " + "; ".join(routed_parts)
                if routed_parts
                else "headless-ACP embody (all watched labels)"
            )
        spawn_fn = (
            make_label_routed_spawn(default_spawn, overrides=overrides)
            if overrides
            else default_spawn
        )
        print(f"agent-dispatch supervise: {routed_note}", file=sys.stderr)
        # A local lane is headless when the default backend is headless, or when a
        # non-headless default routes a subset of labels to a headless body.
        _headless_active = backend == "headless" or bool(headless_labels)
        _preflight_agent = (
            (getattr(args, "headless_agent", None) or "task-worker") if _headless_active else None
        )
        _preflight_pool = None
    # Best-effort, fail-loud preflight: warn (never block) when the headless
    # embody agent isn't registered with agent-bridge on the host(s) where a body
    # will actually spawn -- turning a silent dead-letter (the classic bogus
    # `task-worker` default) into a diagnosable startup warning. Skipped for
    # `--once` so hot one-shot/cron polls stay cheap and side-effect-free.
    if not args.once and _preflight_agent:
        from . import bridge

        for _warning in bridge.preflight_headless_agent(_preflight_agent, pool=_preflight_pool):
            print(_warning, file=sys.stderr)
    from .client import ResolvingDispatchClient

    with ResolvingDispatchClient(lambda: _client(args, ensure=False)) as c:
        evaluator = None
        spec_path = getattr(args, "evaluator", None)
        if spec_path:
            from .producers.evaluator import EvaluatorError, SpecEvaluator

            try:
                spec = json.loads(Path(spec_path).expanduser().read_text(encoding="utf-8"))
                evaluator = SpecEvaluator(spec)
            except (OSError, ValueError, EvaluatorError) as exc:
                print(f"agent-dispatch supervise: bad --evaluator spec: {exc}", file=sys.stderr)
                return 2
            print(
                "agent-dispatch supervise: evaluator pass enabled -- advancing "
                "terminal tasks across the loop",
                file=sys.stderr,
            )
        sup = Supervisor(
            c,
            spawn_fn=spawn_fn,
            repo=repo,
            labels=args.label or None,
            max_concurrent=args.max_concurrent,
            max_attempts=args.max_attempts,
            label_max_attempts=_parse_label_max_attempts(
                getattr(args, "label_max_attempts", None)
            ),
            heartbeat=not args.no_heartbeat,
            publish_activity=True,
            reactive=(not bool(getattr(args, "no_reactive", False)) and not bool(args.once)),
            reactive_interval=getattr(args, "reactive_interval", 2.0) or 2.0,
            supervisor_id=getattr(args, "supervisor_id", None),
            disposable_cli_labels=disposable_cli_labels,
            capacity_gate=capacity_gate,
            evaluator=evaluator,
            redrive_fn=redrive_fn,
            evaluator_ref=getattr(args, "evaluator_ref", None),
            reserving_timeout=max(
                600.0,
                float(getattr(args, "verify_timeout", 0) or 0) + 120.0,
            ),
        )
        if args.once:
            return _emit({"spawned": sup.poll_once()})

        def _on_cycle(spawned: list[str]) -> None:
            if spawned:
                print(
                    f"agent-dispatch supervise: spawned {len(spawned)} task(s): "
                    f"{', '.join(spawned)}",
                    file=sys.stderr,
                )

        sup.serve(interval=args.interval, on_cycle=_on_cycle)
    return 0
