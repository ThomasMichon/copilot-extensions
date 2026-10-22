"""Update runtime / payload helpers extracted from ``update_cli``."""

from __future__ import annotations

import dataclasses
import enum
import functools
import json
import os
import socket
import subprocess
import time
from collections.abc import Collection, Mapping
from pathlib import Path

from . import bare_anchor_files, bare_anchor_sync, git_ops, output, update_events
from . import update_runtime_plan
from . import update_auth_context as _auth
from . import config as cfg
from .installer_capabilities import posix_zero_downtime_flag_supported  # noqa: F401 - planning seam
from .update_gate import (  # noqa: F401 - re-exported for callers and tests
    MACHINE_GATED_SKIP,
    MachineDetectionError,
    _machine_gate_blocks,
)
from .update_installer_process import (
    _installer_timeout,  # noqa: F401 - planning seam
    _run_human_installer,
    installer_deadline_status,  # noqa: F401 - planning seam
)


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _resolve_copilot(*args, **kwargs):
    return _core()._resolve_copilot(*args, **kwargs)


def describe_copilot_spawn_error(exc: OSError, *, cwd: str | Path | None = None) -> str:
    """Turn a failed ``copilot`` executable spawn into an accurate message.

    A bare ``FileNotFoundError`` really does mean "not found" (no ``copilot``
    on PATH, ENOENT) -- *unless* it was actually raised because the caller's
    own ``cwd`` doesn't exist (a stale project/registered-plugin context):
    CPython's ``subprocess`` machinery sets the exception's ``filename`` to
    whichever path it was operating on when the OS call failed, so a
    ``filename`` matching the passed-in ``cwd`` means the failure has nothing
    to do with copilot at all. Pass ``cwd`` (the same value given to
    ``subprocess.run``) so that case reports its real cause instead.

    Other ``OSError`` shapes mean copilot *was* resolved and is otherwise a
    valid executable, yet the OS still refused to spawn it -- most commonly
    Windows ``ERROR_CANT_ACCESS_FILE`` (winerror 1920, "The file cannot be
    accessed by the system"), which fires when the resolved executable is
    the *same* binary currently running THIS process: e.g. driving
    ``<project> update`` from inside a live Copilot CLI session on Windows,
    where the WindowsApps App Execution Alias reparse point for
    ``copilot.exe`` refuses to re-launch itself while it's already running.
    Reporting that case as "not found" is actively misleading -- copilot is
    right there, running this very command. Always include the raw OSError
    text too, so a genuinely novel failure mode is still diagnosable.
    """
    if isinstance(exc, FileNotFoundError):
        filename = getattr(exc, "filename", None)
        if cwd is not None and filename is not None and str(filename) == str(cwd):
            return (
                "'copilot' plugin command failed because its working "
                f"directory does not exist ({exc.strerror or exc}: {filename}) "
                "-- not a missing copilot executable"
            )
        return f"'copilot' CLI not found on PATH ({exc.strerror or exc})"
    if getattr(exc, "winerror", None) == 1920:
        return (
            "'copilot' CLI could not be launched because it is currently "
            "running as this very session -- the OS refuses to re-spawn its "
            "own executable. Re-run this update from outside an active "
            f"Copilot CLI session ({exc.strerror or exc})"
        )
    return f"'copilot' CLI not found or not executable ({exc.strerror or exc})"


def _refresh_marketplace(*args, **kwargs):
    from . import update_cli

    return update_cli._refresh_marketplace(*args, **kwargs)


def _browse_marketplace_plugins(*args, **kwargs):
    from . import update_cli

    return update_cli._browse_marketplace_plugins(*args, **kwargs)


def _uninstall_one_plugin_payload(*args, **kwargs):
    from . import update_cli

    return update_cli._uninstall_one_plugin_payload(*args, **kwargs)


def _update_one_plugin_payload(*args, **kwargs):
    from . import update_cli

    return update_cli._update_one_plugin_payload(*args, **kwargs)


def _project_update_context() -> Path | None:
    try:
        return Path(cfg.load_config().default_repo.anchor)
    except ValueError as exc:
        output.warn(
            f"Could not resolve project marketplace context; using current directory: {exc}"
        )
        return Path.cwd()


def _invocation_update_context() -> Path | None:
    from plugin_resolve import SETTINGS_RELS

    update_context_env = getattr(_core(), "_UPDATE_CONTEXT_ENV", "AGENT_WORKTREES_UPDATE_CONTEXT")
    invocation_cwd = getattr(_core(), "_INVOCATION_CWD", None)
    transported = os.environ.get(update_context_env)
    cwd = Path(transported) if transported else (invocation_cwd or Path.cwd())
    for candidate in (cwd, *cwd.parents):
        try:
            if any(candidate.joinpath(*rel).is_file() for rel in SETTINGS_RELS):
                return candidate
        except OSError as error:
            raise ValueError(
                f"cannot inspect plugin settings under {candidate}: {error}"
            ) from error
    return None


class _PluginActivation(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class _RegisteredPluginTarget:
    context: Path | None
    activation: _PluginActivation

    @property
    def required(self) -> bool:
        return self.activation is not _PluginActivation.INACTIVE


def _update_registered_plugins(
    targets: dict[str, _RegisteredPluginTarget] | None = None,
) -> bool:
    """Acquire required payloads first; stop before advisory work on failure."""
    from . import reconcile

    targets = dict(
        _core_helper("_registered_plugin_targets", _registered_plugin_targets)()
        if targets is None
        else targets
    )
    if not targets:
        output.info("No registered copilot-extensions plugins found")
        return True

    refreshed_contexts: set[Path | None] = set()
    browse_contexts: list[Path | None] = []
    refresh_marketplace = _core_helper("_refresh_marketplace", _refresh_marketplace)
    required_contexts = {target.context for target in targets.values() if target.required}
    for target in targets.values():
        context = target.context
        if context in refreshed_contexts:
            continue
        refreshed_contexts.add(context)
        _auth.prepare_marketplace(context, required=context in required_contexts)
        update_events.enter("payload_refresh")
        if refresh_marketplace(reconcile.MARKETPLACE, cwd=context):
            browse_contexts.append(context)

    if not browse_contexts:
        try:
            fallback_context = _core_helper(
                "_project_update_context", _project_update_context
            )()
        except Exception:
            fallback_context = None
        if fallback_context is not None and fallback_context not in refreshed_contexts:
            refreshed_contexts.add(fallback_context)
            _auth.prepare_marketplace(fallback_context, required=False)
            update_events.enter("payload_refresh")
            if refresh_marketplace(reconcile.MARKETPLACE, cwd=fallback_context):
                browse_contexts.append(fallback_context)

    if not browse_contexts:
        browse_contexts = []
    else:
        deduped: list[Path | None] = []
        seen_browse: set[Path | None] = set()
        for context in browse_contexts:
            if context in seen_browse:
                continue
            seen_browse.add(context)
            deduped.append(context)
        browse_contexts = deduped

    browse_marketplace_plugins = _core_helper(
        "_browse_marketplace_plugins", _browse_marketplace_plugins
    )
    uninstall_one_plugin_payload = _core_helper(
        "_uninstall_one_plugin_payload", _uninstall_one_plugin_payload
    )
    update_one_plugin_payload = _core_helper(
        "_update_one_plugin_payload", _update_one_plugin_payload
    )

    available = next(
        (
            browse_marketplace_plugins(
                reconcile.MARKETPLACE,
                cwd=context,
            )
            for context in browse_contexts
        ),
        None,
    )
    pending_purges: list[tuple[str, _RegisteredPluginTarget]] = []
    if available is not None:
        for name in sorted(list(targets)):
            target = targets[name]
            if target.activation is _PluginActivation.INACTIVE and name not in available:
                pending_purges.append((name, target))
                del targets[name]

    results: list[tuple[str, str, _RegisteredPluginTarget]] = []
    payloads_ok = True
    for name in sorted(targets, key=lambda name: (not targets[name].required, name)):
        target = targets[name]
        status = update_one_plugin_payload(
            name, reconcile.MARKETPLACE, cwd=target.context,
        )
        results.append((name, status, target))
        if target.required and not status.startswith("OK"):
            payloads_ok = False
            remaining = len(targets) - len(results) + len(pending_purges)
            output.warn(
                f"Required payload acquisition failed for {name}; "
                f"{remaining} remaining payload/inventory target(s) not attempted. "
                "Resolve the failure and retry update."
            )
            break

    purge_results: list[tuple[str, str, _RegisteredPluginTarget]] = []
    if payloads_ok:
        for name, target in pending_purges:
            status = uninstall_one_plugin_payload(
                name, reconcile.MARKETPLACE, cwd=target.context,
            )
            purge_results.append((name, status, target))

    output.header("Plugin Payload Update Summary")
    for name, status, target in results:
        if status.startswith("OK"):
            output.ok(name if status == "OK" else f"{name} ({status})")
        elif not target.required:
            output.warn(f"{name}: {status} (inactive installed inventory advisory)")
        else:
            output.warn(f"{name}: {status}")
    if purge_results:
        output.header("Retired Plugin Purge Summary")
        for name, status, _target in purge_results:
            if status.startswith("OK"):
                output.ok(f"{name} ({status})")
            else:
                output.warn(f"{name}: {status} (inactive retired inventory advisory)")
    for name, status, target in (*results, *purge_results):
        update_events.record("payload_refresh", f"{name}@{reconcile.MARKETPLACE}", status,
                             required=target.required)
    purges_ok = all(
        status.startswith("OK") or not target.required for _, status, target in purge_results
    )
    return payloads_ok and purges_ok


def _registered_plugin_targets() -> dict[str, _RegisteredPluginTarget]:
    """Collect active, inactive, and activation-unknown installed targets."""
    from . import reconcile

    targets: dict[str, _RegisteredPluginTarget] = {}

    def add(
        name: str,
        context: Path | None,
        *,
        activation: _PluginActivation,
    ) -> None:
        existing = targets.get(name)
        if existing is None:
            targets[name] = _RegisteredPluginTarget(context, activation)
            return
        preferred_context = existing.context if existing.context is not None else context
        precedence = {
            _PluginActivation.INACTIVE: 0,
            _PluginActivation.UNKNOWN: 1,
            _PluginActivation.ACTIVE: 2,
        }
        selected_activation = (
            existing.activation
            if precedence[existing.activation] >= precedence[activation]
            else activation
        )
        targets[name] = _RegisteredPluginTarget(
            preferred_context,
            selected_activation,
        )

    activation_unknown = False
    activation_warnings: list[str] = []

    def record_activation_error(message: str, error: Exception) -> None:
        nonlocal activation_unknown
        activation_unknown = True
        detail = update_events.bounded(str(error).replace("\r", " ").replace("\n", " "), 240)
        activation_warnings.append(f"{message}: {detail}")

    try:
        invocation_context = _core_helper(
            "_invocation_update_context", _invocation_update_context
        )()
    except Exception as exc:
        invocation_context = None
        record_activation_error(
            "Could not discover enabled plugins from the invocation context",
            exc,
        )
    if invocation_context is not None:
        try:
            for name in reconcile.read_enabled_plugins(invocation_context):
                add(
                    name,
                    invocation_context,
                    activation=_PluginActivation.ACTIVE,
                )
        except Exception as exc:
            record_activation_error(
                f"Could not read enabled plugins from invocation context {invocation_context}",
                exc,
            )

    try:
        config = cfg.load_config()
        repos = config.repos or {}
    except Exception as exc:
        repos = {}
        record_activation_error(
            "Could not read managed project configuration",
            exc,
        )

    seen_anchors: set[str] = set()
    for repo in repos.values():
        anchor = repo.anchor
        if not anchor or anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        try:
            context = Path(anchor)
            for name in reconcile.read_enabled_plugins(context):
                add(name, context, activation=_PluginActivation.ACTIVE)
        except Exception as exc:
            record_activation_error(
                f"Could not read enabled plugins from {anchor}",
                exc,
            )

    installed_names: list[str] = []
    try:
        installed_names = reconcile.read_installed_plugins()
    except Exception as exc:
        output.warn(f"Could not read installed plugin inventory: {exc}")

    try:
        for name in reconcile.read_user_enabled_plugins():
            add(name, None, activation=_PluginActivation.ACTIVE)
    except Exception as exc:
        record_activation_error(
            "Could not read user-global enabled plugins",
            exc,
        )

    inventory_activation = (
        _PluginActivation.UNKNOWN if activation_unknown else _PluginActivation.INACTIVE
    )
    for name in installed_names:
        add(name, None, activation=inventory_activation)

    warning_limit = 4
    for warning in activation_warnings[:warning_limit]:
        output.warn(warning)
    remaining = len(activation_warnings) - warning_limit
    if remaining > 0:
        output.warn(f"{remaining} additional plugin activation read failure(s) omitted")

    return targets


def _module_names(plugin_dir: Path) -> set[str]:
    """The sibling-module names handled by :func:`_update_modules` (``modules.json``)."""
    manifest = plugin_dir / "modules.json"
    try:
        data = json.loads(manifest.read_text())
    except Exception:
        return set()
    out: set[str] = set()
    for mod in data.get("modules", []) or []:
        name = mod.get("name")
        if name:
            out.add(str(name))
    return out


def _reconcile_registered_runtimes(
    plugin_dir: Path,
    platform_name: str,
    skip_modules: list[str] | None = None,
    *,
    force: bool = False,
    targets: dict[str, _RegisteredPluginTarget] | None = None,
    force_runtimes: Collection[str] = (),
    plan: tuple[update_runtime_plan.RuntimeUpdateStep, ...] | None = None,
) -> bool:
    """Rebuild the runtime venv of every enabled plugin the other steps skip."""
    if skip_modules is not None and len(skip_modules) == 0:
        return True

    if plan is not None:
        planned_results: list[str] = []
        for step in plan:
            started = time.monotonic()
            status = _execute_runtime_step(step)
            update_events.record(
                step.phase, f"{step.name}@copilot-extensions", status,
                changed=status.startswith("OK"), started=started,
            )
            planned_results.append(status)
            if status not in ("SKIPPED (current)", "payload-only"):
                report = output.ok if status.startswith("OK") else (
                    output.info if status.startswith("SKIPPED") else output.warn
                )
                report(f"{step.name}: {status}")
        return all(
            status.startswith(("OK", "SKIPPED")) or status == "payload-only"
            for status in planned_results
        )

    targets = (
        _core_helper("_registered_plugin_targets", _registered_plugin_targets)()
        if targets is None
        else targets
    )
    names = {name for name, target in targets.items() if target.required}

    excluded = _core_helper("_module_names", _module_names)(plugin_dir) | {"agent-worktrees"}
    if skip_modules:
        excluded |= set(skip_modules)
    names -= excluded
    if not names:
        return True

    reconcile_one_runtime = _core_helper("_reconcile_one_runtime", _reconcile_one_runtime)
    results: list[tuple[str, str]] = []
    for name in sorted(names):
        forced = force or name in force_runtimes
        results.append(
            (
                name,
                reconcile_one_runtime(
                    name,
                    platform_name,
                    force=forced,
                    gate_context=targets[name].context,
                ),
            )
        )

    acted = [(n, s) for n, s in results if s not in ("SKIPPED (current)", "payload-only")]
    if acted:
        output.header("Registered Runtime Reconcile")
        for name, status in acted:
            if status.startswith("OK"):
                output.ok(f"{name} ({status})")
            elif status.startswith("SKIPPED"):
                output.info(f"{name} ({status})")
            else:
                output.warn(f"{name}: {status}")
    return all(
        status.startswith("OK") or status.startswith("SKIPPED") or status == "payload-only"
        for _, status in results
    )


def _reconcile_one_runtime(
    name: str,
    platform_name: str,
    *,
    force: bool,
    gate_context: Path | None = None,
) -> str:
    """Reconcile a single registered plugin's runtime; returns a status string.

    ``gate_context`` is the repository that enables the plugin; it locates the
    machine-gate manifest exactly as the session-start reconcile does. A
    ``machine-gated`` runtime the gate does not allow on this machine is reported
    as :data:`MACHINE_GATED_SKIP` (even under ``--force``) and never installed.
    """
    started = time.monotonic()
    status = _reconcile_one_runtime_status(
        name, platform_name, force=force, gate_context=gate_context
    )
    update_events.record("runtime_reconcile", f"{name}@copilot-extensions", status,
                         changed=status.startswith("OK"), started=started)
    return status


def _reconcile_one_runtime_status(
    name: str,
    platform_name: str,
    *,
    force: bool,
    gate_context: Path | None = None,
) -> str:
    return _execute_runtime_step(update_runtime_plan.plan_registered(
        name, platform_name, force=force, gate_context=gate_context,
    ))


def _execute_runtime_step(step: update_runtime_plan.RuntimeUpdateStep) -> str:
    from . import reconcile

    if step.status:
        return step.status
    output.header(f"Updating Runtime: {step.name}")
    run = functools.partial(
        _run_human_installer, label=step.name, receipt_owned=True,
        cwd=step.payload, env=step.environment, deadline_s=step.deadline_s,
        target_version=step.target_version,
        receipt=functools.partial(
            reconcile.runtime_deployed_version, step.name, root=step.runtime_root,
        ),
    )
    status = run(list(step.argv)).status
    if step.fallback_argv and status.startswith("installer exited"):
        output.info(f"Module '{step.name}' update failed (not installed?), trying install...")
        status = run(list(step.fallback_argv)).status
        return "OK (installed)" if status == "OK" else status.replace(
            "installer exited", "install exited", 1,
        )
    return status


def _run_engine_installer(
    argv: list[str], plugin_dir: Path, timeout: int, env: Mapping[str, str],
    runtime_root: Path | None = None,
    target_version: str | None = None,
) -> int:
    """Run agent-worktrees' own runtime installer and record its result.

    A timeout is reported as such (never as success: a completed version slot
    does not prove this attempt activated it) and returns a failure code; with
    ``runtime_root`` an exit 0 that left the runtime off the payload version fails.
    """
    from . import reconcile

    run = _run_human_installer(
        argv, label="agent-worktrees", receipt_owned=True,
        cwd=plugin_dir, env=env, deadline_s=timeout,
        target_version=(target_version or reconcile.payload_version(plugin_dir)) if runtime_root else None,
        receipt=lambda: reconcile.runtime_deployed_version("agent-worktrees", root=runtime_root),
    )
    rc, status = (0 if run.status == "OK" else (run.rc or 1)), run.status
    if run.status.startswith("timed out"):
        output.err("agent-worktrees installer timed out")
    elif run.status != "OK":
        output.err(f"agent-worktrees installer failed: {run.status}")
    update_events.record("engine_runtime", "agent-worktrees@copilot-extensions", status,
                         changed=status.startswith("OK"))
    return rc


def _fast_forward_project_anchors() -> None:
    """Fast-forward each managed repo's anchor to its upstream default branch."""
    try:
        config = cfg.load_config()
    except Exception:
        return

    repos = config.repos or {}
    if not repos:
        return

    output.header("Syncing repo anchor(s)")
    seen: set[str] = set()
    for repo in repos.values():
        anchor = repo.anchor
        if not anchor or anchor in seen:
            continue
        seen.add(anchor)

        anchor_path = Path(anchor)
        if _auth.anchor_blocked(anchor_path):
            continue
        if not (anchor_path / ".git").exists():
            output.warn(f"Anchor not checked out, skipping: {anchor}")
            continue
        if bare_anchor_sync.is_bare(anchor_path):
            # No work tree: the dirty check / merge below can't run there.
            try:
                with _auth.repository_scope(anchor_path):
                    state, detail = bare_anchor_sync.sync_bare_anchor(
                        anchor, repo.default_branch, remote=repo.remote)
            except Exception as exc:  # e.g. a fetch timeout: report, keep updating
                state, detail = "error", str(exc) or type(exc).__name__
            if state == "error":
                _auth.anchor_failure(anchor, detail)
            # Like `repos sync`: files left stale after the ref moved need attention.
            files = bare_anchor_files.status_of(detail)
            stale = files is not None and files not in bare_anchor_files.QUIET_STATUSES
            report = output.ok if state == "synced" and not stale else output.info
            (output.warn if state == "error" or stale else report)(
                f"{anchor}: {state} ({update_events.redact(detail)})"
            )
            continue

        with _auth.repository_scope(anchor_path):
            skip = bare_anchor_sync.branch_skip_reason(anchor_path, repo.default_branch)
        if skip:
            output.info(f"{anchor}: {skip}")
            continue

        with _auth.repository_scope(anchor_path):
            ff = git_ops.fast_forward_worktree(
                anchor_path, remote=repo.remote,
                default_branch=repo.default_branch, do_fetch=True,
            )
        if ff.fetch_error:
            _auth.anchor_failure(anchor, ff.fetch_error)
            output.warn(f"{anchor}: fetch from {repo.remote} failed; compared against "
                        f"the last-known upstream ({update_events.redact(ff.fetch_error)})")
        if ff.updated:
            output.ok(
                f"{anchor}: fast-forwarded {ff.behind} commit(s) to "
                f"{repo.remote}/{repo.default_branch}"
            )
        elif ff.reason in ("up-to-date",):
            output.ok(f"{anchor}: up to date")
        elif ff.reason in ("dirty", "ahead", "diverged"):
            output.info(f"{anchor}: {ff.reason} -- left untouched")
        else:
            output.info(f"{anchor}: not synced ({ff.reason})")


def _self_entry_present(config: cfg.Config) -> bool:
    """Whether this machine has a self-entry in the anchor's machines.yaml."""
    try:
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except Exception:
        return True
    if not entries:
        return True
    return cfg.find_machine_entry(entries, socket.gethostname()) is not None


def _heal_stale_anchor_if_self_missing(config: cfg.Config) -> cfg.Config:
    """Break the launch catch-22 for a stale anchor missing this machine."""
    try:
        if _core_helper("_self_entry_present", _self_entry_present)(config):
            return config
        output.info(
            "This machine is not in machines.yaml -- fast-forwarding the "
            "anchor before the picker (stale-config self-heal)."
        )
        _core_helper("_fast_forward_project_anchors", _fast_forward_project_anchors)()
        return cfg.load_config()
    except Exception as exc:
        output.warn(f"Anchor self-heal skipped: {exc}")
        return config


def _update_modules(
    plugin_dir: Path,
    platform_name: str,
    skip_modules: list[str] | None,
    force: bool = False,
    *,
    targets: dict[str, _RegisteredPluginTarget] | None = None,
    force_runtimes: Collection[str] = (),
    plan: tuple[update_runtime_plan.RuntimeUpdateStep, ...] | None = None,
) -> bool:
    """Update sibling modules registered in modules.json."""
    if plan is None:
        if targets is None:
            # Standalone callers still own payload acquisition. The unified
            # updater supplies its already-acquired target selection and plan.
            for name in sorted(_module_names(plugin_dir)):
                if skip_modules is not None and (not skip_modules or name in skip_modules):
                    continue
                _refresh_module_payload(name)
        plan = update_runtime_plan.plan_modules(
            plugin_dir, platform_name, skip_modules, force=force,
            targets=targets, force_runtimes=force_runtimes,
        )
    results = [(step.name, _execute_runtime_step(step)) for step in plan]

    for step, (name, status) in zip(plan, results, strict=True):
        update_events.record("module_update", f"{name}@copilot-extensions", status,
                             changed=status.startswith("OK"),
                             required=step.required)
    if results:
        output.header("Module Update Summary")
        for name, status in results:
            if status.startswith("OK"):
                output.ok(f"{name} ({status})")
            elif status == "SKIPPED":
                output.info(f"{name} (skipped)")
            elif status.startswith("SKIPPED"):
                output.info(f"{name} ({status})")
            else:
                output.warn(f"{name}: {status}")
    return all(status.startswith("OK") or status.startswith("SKIPPED") for _, status in results)


def _refresh_module_payload(name: str) -> None:
    """Preserve the best-effort refresh contract of standalone module callers."""
    try:
        result = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", "update", f"{name}@copilot-extensions"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                output.ok(line)
        else:
            output.warn(
                f"Plugin update for {name} returned non-zero "
                "(continuing with installed version)"
            )
    except OSError as error:
        output.warn(f"{describe_copilot_spawn_error(error)} -- skipping plugin refresh")
    except subprocess.TimeoutExpired:
        output.warn(f"Plugin update for {name} timed out -- continuing with installed version")


def _find_installed_plugin_dir() -> Path | None:
    """Locate the agent-worktrees plugin in the Copilot CLI install tree."""
    plugins_root = Path.home() / ".copilot" / "installed-plugins"

    candidate = plugins_root / "copilot-extensions" / "agent-worktrees"
    if candidate.is_dir() and (candidate / "plugin.json").exists():
        return candidate

    direct = plugins_root / "_direct"
    if direct.is_dir():
        for d in direct.iterdir():
            if d.is_dir() and "agent-worktrees" in d.name:
                if (d / "plugin.json").exists():
                    return d

    if plugins_root.is_dir():
        for pj in plugins_root.rglob("plugin.json"):
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                if data.get("name") == "agent-worktrees":
                    return pj.parent
            except Exception:
                continue

    return None
