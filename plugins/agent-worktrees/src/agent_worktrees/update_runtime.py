"""Update runtime / payload helpers extracted from ``update_cli``."""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from . import config as cfg
from . import git_ops, output


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
    return _core()._refresh_marketplace(*args, **kwargs)


def _browse_marketplace_plugins(*args, **kwargs):
    return _core()._browse_marketplace_plugins(*args, **kwargs)


def _uninstall_one_plugin_payload(*args, **kwargs):
    return _core()._uninstall_one_plugin_payload(*args, **kwargs)


def _update_one_plugin_payload(*args, **kwargs):
    return _core()._update_one_plugin_payload(*args, **kwargs)


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
    """Refresh every registered copilot-extensions payload (active + inventory)."""
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
    for target in targets.values():
        context = target.context
        if context in refreshed_contexts:
            continue
        refreshed_contexts.add(context)
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
    purge_results: list[tuple[str, str, _RegisteredPluginTarget]] = []
    if available is not None:
        for name in sorted(list(targets)):
            target = targets[name]
            if target.activation is _PluginActivation.INACTIVE and name not in available:
                status = uninstall_one_plugin_payload(
                    name,
                    reconcile.MARKETPLACE,
                    cwd=target.context,
                )
                purge_results.append((name, status, target))
                del targets[name]

    results: list[tuple[str, str, _RegisteredPluginTarget]] = []
    for name in sorted(targets):
        target = targets[name]
        results.append(
            (
                name,
                update_one_plugin_payload(name, reconcile.MARKETPLACE, cwd=target.context),
                target,
            )
        )

    if purge_results:
        output.header("Retired Plugin Purge Summary")
        for name, status, _target in purge_results:
            if status.startswith("OK"):
                output.ok(f"{name} ({status})")
            else:
                output.warn(f"{name}: {status} (inactive retired inventory advisory)")

    output.header("Plugin Payload Update Summary")
    for name, status, target in results:
        if status.startswith("OK"):
            output.ok(name if status == "OK" else f"{name} ({status})")
        elif not target.required:
            output.warn(f"{name}: {status} (inactive installed inventory advisory)")
        else:
            output.warn(f"{name}: {status}")
    payloads_ok = all(
        status.startswith("OK") or not target.required for _, status, target in results
    )
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
        detail = str(error).replace("\r", " ").replace("\n", " ")[:240]
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
) -> bool:
    """Rebuild the runtime venv of every enabled plugin the other steps skip."""
    if skip_modules is not None and len(skip_modules) == 0:
        return True

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
        results.append((name, reconcile_one_runtime(name, platform_name, force=force)))

    acted = [(n, s) for n, s in results if s not in ("SKIPPED (current)", "payload-only")]
    if acted:
        output.header("Registered Runtime Reconcile")
        for name, status in acted:
            if status.startswith("OK"):
                output.ok(f"{name} ({status})")
            else:
                output.warn(f"{name}: {status}")
    return all(
        status.startswith("OK") or status in {"SKIPPED (current)", "payload-only"}
        for _, status in results
    )


def _reconcile_one_runtime(name: str, platform_name: str, *, force: bool) -> str:
    """Reconcile a single registered plugin's runtime; returns a status string."""
    from . import reconcile

    pdir = reconcile.core_installed_payload_dir(name)
    if pdir is None:
        return "payload not installed"
    scope = reconcile.manifest_runtime_scope(pdir) or "none"
    if scope == "none":
        return "payload-only"

    pver = reconcile.payload_version(pdir)
    try:
        child_environment, runtime_root = reconcile.runtime_installer_environment(name, pdir)
    except ValueError as error:
        return f"installation context invalid: {error}"
    dver = reconcile.runtime_deployed_version(name, root=runtime_root)
    if not force and pver and dver and reconcile._versions_equal(dver, pver):
        return "SKIPPED (current)"

    context = (
        Path(child_environment["COPILOT_EXTENSIONS_CONTEXT"])
        if "COPILOT_EXTENSIONS_CONTEXT" in child_environment
        else None
    )
    if context is not None:
        if force:
            return "forced namespaced runtime reconciliation is unsupported"
        try:
            built = reconcile.runtime_installer_argv(
                pdir,
                context=context,
            )
        except ValueError as error:
            return str(error)
        if built is None:
            return "installer not found"
        _display, argv = built
        installer = None
    elif platform_name == "windows":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return "powershell not found"
        installer = pdir / "scripts" / "install.ps1"
        if installer.exists():
            argv = [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
                "update",
            ]
        else:
            installer = pdir / "scripts" / "init.ps1"
            argv = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)] + (
                ["-Force"] if force else []
            )
    else:
        installer = pdir / "scripts" / "install.sh"
        if installer.exists():
            argv = ["bash", installer.as_posix(), "update"]
        else:
            installer = pdir / "scripts" / "init.sh"
            argv = ["bash", installer.as_posix()] + (["--force"] if force else [])

    if context is None and installer is not None and not installer.exists():
        return "installer not found"

    output.header(f"Reconciling Runtime: {name}" + (" (forced)" if force else ""))
    try:
        r = subprocess.run(
            argv,
            cwd=pdir,
            timeout=300,
            env=child_environment,
        )
    except subprocess.TimeoutExpired:
        return "timed out"
    except Exception as exc:
        return str(exc)[:120]
    return "OK" if r.returncode == 0 else f"installer exited {r.returncode}"


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
        if not (anchor_path / ".git").exists():
            output.warn(f"Anchor not checked out, skipping: {anchor}")
            continue

        current = git_ops.current_branch(anchor_path)
        if current is None:
            output.info(f"{anchor}: detached HEAD -- skipped")
            continue
        if current != repo.default_branch:
            output.info(f"{anchor}: on '{current}', not '{repo.default_branch}' -- skipped")
            continue

        ff = git_ops.fast_forward_worktree(
            anchor_path,
            remote=repo.remote,
            default_branch=repo.default_branch,
            do_fetch=True,
        )
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
) -> bool:
    """Update sibling modules registered in modules.json."""
    manifest = plugin_dir / "modules.json"
    if not manifest.exists():
        return True

    try:
        data = json.loads(manifest.read_text())
    except Exception as exc:
        output.warn(f"Failed to parse modules.json: {exc}")
        return False

    modules = data.get("modules", [])
    if not modules:
        return True

    if skip_modules is not None and len(skip_modules) == 0:
        output.info("Skipping all module updates (--skip-modules)")
        return True

    extensions_root = plugin_dir.parent
    results: list[tuple[str, str]] = []

    for mod in modules:
        name = mod.get("name", "unknown")

        if skip_modules and name in skip_modules:
            output.info(f"Skipping module: {name}")
            results.append((name, "SKIPPED"))
            continue

        target = targets.get(name) if targets is not None else None
        if targets is not None and (target is None or not target.required):
            output.info(
                f"Skipping inactive module runtime: {name} (payload inventory remains advisory)"
            )
            results.append((name, "SKIPPED (inactive inventory)"))
            continue

        source = mod.get("source", name)
        module_dir = extensions_root / source

        if targets is None:
            plugin_ref = f"{name}@copilot-extensions"
            try:
                r = subprocess.run(
                    [_resolve_copilot() or "copilot", "plugin", "update", plugin_ref],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().splitlines():
                        output.ok(line)
                else:
                    output.warn(
                        f"Plugin update for {name} returned non-zero "
                        f"(continuing with installed version)"
                    )
            except OSError as exc:
                output.warn(
                    f"{describe_copilot_spawn_error(exc)} -- skipping plugin refresh"
                )
            except subprocess.TimeoutExpired:
                output.warn(
                    f"Plugin update for {name} timed out -- continuing with installed version"
                )

        if not module_dir.is_dir():
            output.warn(f"Module '{name}' source not found: {module_dir}")
            results.append((name, "source dir not found"))
            continue

        from . import reconcile as _reconcile

        try:
            runtime_env, runtime_root = _reconcile.runtime_installer_environment(name, module_dir)
        except ValueError as error:
            output.warn(f"{name}: installation context invalid: {error}")
            results.append((name, "installation context invalid"))
            continue
        mod_payload_ver = _reconcile.payload_version(module_dir)
        mod_deployed_ver = _reconcile.runtime_deployed_version(name, root=runtime_root)
        if (
            not force
            and mod_payload_ver
            and mod_deployed_ver
            and mod_payload_ver == mod_deployed_ver
        ):
            output.ok(f"{name} already at {mod_deployed_ver} -- skipping installer")
            results.append((name, "SKIPPED (current)"))
            continue

        if platform_name == "windows":
            installer = module_dir / "scripts" / "install.ps1"
        else:
            installer = module_dir / "scripts" / "install.sh"

        if not installer.exists():
            output.warn(f"Module '{name}' installer not found: {installer}")
            results.append((name, "installer not found"))
            continue

        if platform_name == "windows":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                output.warn(f"Module '{name}': PowerShell not found")
                results.append((name, "powershell not found"))
                continue
            shell_prefix = [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
            ]
        else:
            shell_prefix = ["bash", str(installer)]

        update_args = ["update"]
        if platform_name == "windows":
            from . import reconcile as _reconcile

            if _reconcile._zero_downtime_update(module_dir):
                update_args.append("-ZeroDowntime")
        output.header(f"Updating Module: {name}")
        try:
            r = subprocess.run(
                [*shell_prefix, *update_args],
                cwd=module_dir,
                timeout=300,
                env=runtime_env,
            )
            if r.returncode == 0:
                results.append((name, "OK"))
                continue
        except subprocess.TimeoutExpired:
            output.warn(f"Module '{name}' update timed out")
            results.append((name, "timed out"))
            continue
        except Exception as exc:
            output.warn(f"Module '{name}' update failed: {exc}")
            results.append((name, str(exc)))
            continue

        output.info(f"Module '{name}' update failed (not installed?), trying install...")
        try:
            r = subprocess.run(
                [*shell_prefix, "install"],
                cwd=module_dir,
                timeout=300,
                env=runtime_env,
            )
            if r.returncode == 0:
                results.append((name, "OK (installed)"))
            else:
                output.warn(f"Module '{name}' install exited with code {r.returncode}")
                results.append((name, f"install exited {r.returncode}"))
        except subprocess.TimeoutExpired:
            output.warn(f"Module '{name}' install timed out")
            results.append((name, "timed out"))
        except Exception as exc:
            output.warn(f"Module '{name}' install failed: {exc}")
            results.append((name, str(exc)))

    if results:
        output.header("Module Update Summary")
        for name, status in results:
            if status == "OK":
                output.ok(f"{name}")
            elif status == "SKIPPED":
                output.info(f"{name} (skipped)")
            else:
                output.warn(f"{name}: {status}")
    return all(status.startswith("OK") or status.startswith("SKIPPED") for _, status in results)


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
