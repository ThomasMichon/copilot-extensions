"""Update / reconcile CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from . import installer as inst, output, services as svc
from .update_runtime import describe_copilot_spawn_error as _describe_copilot_spawn_error


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _resolve_copilot(*args, **kwargs):
    return _core()._resolve_copilot(*args, **kwargs)


def _usable_worktree_manager(*args, **kwargs):
    return _core()._usable_worktree_manager(*args, **kwargs)


def _exec_worktree_manager(*args, **kwargs):
    return _core()._exec_worktree_manager(*args, **kwargs)


def _refresh_terminal_profiles(*args, **kwargs):
    return _core()._refresh_terminal_profiles(*args, **kwargs)


def _resolve_environment(*args, **kwargs):
    return _core()._resolve_environment(*args, **kwargs)


def _find_repo_dir(*args, **kwargs):
    return _core()._find_repo_dir(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("update", help="Re-deploy from repo")
    p.add_argument(
        "--recreate-venv",
        action="store_true",
        help="Force full venv recreation (cannot run from managed venv)",
    )
    p.add_argument(
        "--skip-modules",
        nargs="*",
        default=None,
        metavar="MODULE",
        help="Skip module updates (all if no names given, or named modules)",
    )
    p.add_argument(
        "--no-anchor-sync",
        action="store_true",
        help="Skip fast-forwarding the managed repo anchor(s) after update",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-deploy every runtime installer even when the "
        "deployed version already matches the payload "
        "(default: skip already-current runtimes for speed)",
    )
    p.add_argument(
        "--no-manager",
        action="store_true",
        help="Run the in-plugin update directly, bypassing the "
        "Worktree Manager seam (the escape hatch the Manager "
        "itself re-enters through, and the DQ8 fallback)",
    )

    sub.add_parser("pre-launch", help="Check bootstrap staleness (JSON output)")

    sp = sub.add_parser(
        "reconcile-plugins", help="Reconcile repo enabledPlugins payloads + gated runtimes (JSON)"
    )
    sp.add_argument(
        "--machine", default=None, help="Machine name (auto-detected from hostname if omitted)"
    )
    sp.add_argument(
        "--repo", default=None, help="Repo path to reconcile (defaults to the resolved anchor)"
    )
    sp.add_argument(
        "--status", default=None, help="Write detached provisioning status JSON to this path"
    )
    sp.add_argument(
        "--apply",
        action="store_true",
        help="Execute the plan in-process (2-pass) instead of printing "
        "it. Used by the provision-check sessionStart shim.",
    )
    sp.add_argument(
        "--peek",
        action="store_true",
        help="Print the plan WITHOUT persisting the reconcile cache "
        "(read-only preview; no throttle side effects).",
    )
    sp.add_argument(
        "--with-payload-refresh",
        action="store_true",
        help="Include marketplace payload install/refresh phases "
        "(`copilot plugin install/update`). OFF by default: the "
        "programmatic path is runtime-only + pull-free. Only the "
        "Picker/operator update flow opts in (#1393).",
    )

    sp = sub.add_parser(
        "uninstall-plugins",
        help="Sweep every deployed core copilot-extensions plugin runtime "
        "via each plugin's own uninstall action (JSON; dry-run by default)",
    )
    sp.add_argument(
        "--apply",
        action="store_true",
        help="Actually invoke each plugin's uninstall action. Default is "
        "dry-run: print the plan (and any diagnostics naming what this "
        "sweep cannot remove) without touching anything.",
    )
    sp.add_argument(
        "--verify",
        action="store_true",
        help="Actually EXECUTE each covered plugin's own -DryRun/--dry-run "
        "preview against live system state (safe -- every covered plugin's "
        "uninstall walks the same code paths under that switch without "
        "touching anything) and fold its real captured output into the "
        "JSON, instead of only displaying the argv that would run. Mutually "
        "exclusive with --apply.",
    )


def cmd_update(args: argparse.Namespace) -> int:
    """Update the harness -- via the Worktree Manager when present (the seam).

    ``update`` is the harness's *plugin updater/aligner*: it refreshes every
    registered copilot-extensions plugin payload + runtime, reconciles binstubs,
    and fast-forwards managed anchors. The founding intent hands that role to the
    out-of-plugin **Worktree Manager** (the Configurator's "plugin update +
    cross-plugin alignment"). So, mirroring the bare-launch seam (DQ7/DQ8): when
    a *usable* Manager is on PATH, hand off to ``worktree-manager update`` (which
    self-updates the Manager, then drives the mechanics back through
    ``agent-worktrees update --no-manager``); otherwise run the in-plugin update
    directly. ``--no-manager`` is the seam bypass -- both the Manager's own
    re-entry and an operator escape hatch -- and the DQ8 fallback whenever the
    Manager is absent or fails its health check, so ``update`` never dead-ends.
    """
    if not getattr(args, "no_manager", False):
        mgr = _usable_worktree_manager()
        if mgr:
            update_context_env = getattr(
                _core(), "_UPDATE_CONTEXT_ENV", "AGENT_WORKTREES_UPDATE_CONTEXT"
            )
            previous_context = os.environ.get(update_context_env)
            invocation_context = _core_helper(
                "_invocation_update_context", _invocation_update_context
            )()
            if invocation_context is not None:
                os.environ[update_context_env] = str(invocation_context)
            try:
                return _exec_worktree_manager(
                    mgr,
                    cfg.active_project(),
                    subcommand=["update", *_core_helper("_update_flags", _update_flags)(args)],
                )
            finally:
                if previous_context is None:
                    os.environ.pop(update_context_env, None)
                else:
                    os.environ[update_context_env] = previous_context
    return _core_helper("_cmd_update_in_plugin", _cmd_update_in_plugin)(args)


def _update_flags(args: argparse.Namespace) -> list[str]:
    """Reconstruct the forwardable ``update`` flags to thread through the seam.

    So ``<project> update --force`` (etc.) keeps its meaning when handed to the
    Worktree Manager, which forwards them back to ``agent-worktrees update
    --no-manager``. ``--no-manager`` itself is never forwarded (it is the bypass).
    """
    flags: list[str] = []
    if getattr(args, "force", False):
        flags.append("--force")
    if getattr(args, "no_anchor_sync", False):
        flags.append("--no-anchor-sync")
    if getattr(args, "recreate_venv", False):
        flags.append("--recreate-venv")
    skip = getattr(args, "skip_modules", None)
    if skip is not None:
        flags.append("--skip-modules")
        flags.extend(skip)
    return flags


def _cmd_update_in_plugin(args: argparse.Namespace) -> int:
    """The in-plugin harness update mechanics (the seam's DQ8 fallback + the
    body the Worktree Manager drives via ``agent-worktrees update --no-manager``).

    1. Run ``copilot plugin update`` to fetch the latest plugin version.
    2. Locate the installed plugin directory.
    3. Run the platform-specific installer from the freshly updated plugin.
    """
    output.header("Updating Agent Worktrees")

    if getattr(args, "recreate_venv", False):
        output.warn(
            "--recreate-venv is not supported by the plugin-based "
            "update flow; use 'agent-worktrees install' instead"
        )

    plugin_ref = "agent-worktrees@copilot-extensions"
    output.info(f"Updating plugin: {plugin_ref}")
    payloads_ok = True
    update_context = _core_helper(
        "_invocation_update_context", _invocation_update_context
    )() or _core_helper("_project_update_context", _project_update_context)()
    try:
        r = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", "update", plugin_ref],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=update_context,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                output.ok(line)
        else:
            detail = "\n".join(x for x in [r.stdout.strip(), r.stderr.strip()] if x)
            output.warn(f"Plugin update returned non-zero:\n{detail}")
            payloads_ok = False
    except OSError as exc:
        output.warn(
            f"{_describe_copilot_spawn_error(exc, cwd=update_context)} -- skipping plugin update"
        )
        payloads_ok = False
    except subprocess.TimeoutExpired:
        output.warn("Plugin update timed out -- continuing with installed version")
        payloads_ok = False

    registered_targets = _core_helper(
        "_registered_plugin_targets", _registered_plugin_targets
    )()
    if (
        _core_helper("_update_registered_plugins", _update_registered_plugins)(
            registered_targets
        )
        is False
    ):
        payloads_ok = False

    plugin_dir = _core_helper("_find_installed_plugin_dir", _find_installed_plugin_dir)()
    if not plugin_dir:
        output.err("Cannot find installed plugin directory")
        output.err("Expected at ~/.copilot/installed-plugins/copilot-extensions/agent-worktrees/")
        return 1

    output.info(f"Plugin source: {plugin_dir}")

    from . import reconcile as _reconcile

    plat = cfg.detect_platform()
    force = getattr(args, "force", False)
    try:
        aw_runtime_env, aw_runtime_root = _reconcile.runtime_installer_environment(
            "agent-worktrees",
            plugin_dir,
        )
    except ValueError as error:
        output.err(f"Installation context invalid: {error}")
        return 1
    aw_payload_ver = _reconcile.payload_version(plugin_dir)
    aw_context_selected = "COPILOT_EXTENSIONS_CONTEXT" in aw_runtime_env
    aw_deployed_ver = _reconcile.runtime_deployed_version("agent-worktrees", root=aw_runtime_root)
    versions_equal = bool(aw_payload_ver and aw_payload_ver == aw_deployed_ver)
    version_match = (not force) and versions_equal
    hooks_drifted = (
        bool(version_match)
        and not aw_context_selected
        and _reconcile.hook_shims_drifted(plugin_dir)
    )
    if version_match and not hooks_drifted:
        output.ok(
            f"Runtime already at {aw_deployed_ver} -- skipping installer "
            "(use --force to re-deploy)"
        )
        if plat == "windows":
            _refresh_terminal_profiles()
    else:
        if hooks_drifted:
            output.warn(
                f"Runtime already at {aw_deployed_ver}, but deployed hook shims "
                "have drifted from the payload -- re-deploying (bin/ hook shims "
                "deploy independently of the runtime version; dotfiles #1171)"
            )
        if plat == "windows":
            installer = plugin_dir / "scripts" / "install.ps1"
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                output.err("PowerShell not found")
                return 1
            argv = [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
                "update",
                "-InstallDir",
                str(aw_runtime_root),
            ]
        else:
            installer = plugin_dir / "scripts" / "install.sh"
            argv = [
                "bash",
                str(installer),
                "update",
                "--install-dir",
                str(aw_runtime_root),
            ]

        if not installer.exists():
            output.err(f"Installer not found: {installer}")
            return 1

        result = subprocess.run(
            argv,
            cwd=plugin_dir,
            timeout=600,
            env=aw_runtime_env,
        )
        if result.returncode != 0:
            return result.returncode

    try:
        inst.reconcile_binstubs()
    except Exception as e:
        output.warn(f"Binstub reconcile skipped: {e}")

    skip_modules = getattr(args, "skip_modules", None)
    runtimes_ok = True
    if (
        _core_helper("_update_modules", _update_modules)(
            plugin_dir,
            plat,
            skip_modules,
            force=force,
            targets=registered_targets,
        )
        is False
    ):
        runtimes_ok = False

    if (
        _core_helper("_reconcile_registered_runtimes", _reconcile_registered_runtimes)(
            plugin_dir,
            plat,
            skip_modules,
            force=force,
            targets=registered_targets,
        )
        is False
    ):
        runtimes_ok = False

    if not getattr(args, "no_anchor_sync", False):
        _core_helper("_fast_forward_project_anchors", _fast_forward_project_anchors)()

    return 0 if payloads_ok and runtimes_ok else 1


def _project_update_context() -> Path | None:
    """The active project's anchor, whose settings declare marketplace context."""
    try:
        return Path(cfg.load_config().default_repo.anchor)
    except ValueError as exc:
        output.warn(
            f"Could not resolve project marketplace context; using current directory: {exc}"
        )
        return Path.cwd()


def _invocation_update_context() -> Path | None:
    """Return the invoking checkout when it carries repository Copilot settings."""
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


def _refresh_marketplace(marketplace: str, *, cwd: Path | None = None) -> bool:
    """Refresh the local marketplace catalog (best-effort, non-fatal)."""
    try:
        r = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", "marketplace", "update", marketplace],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
        if r.returncode != 0:
            output.warn("Marketplace refresh returned non-zero -- continuing")
            return False
        return True
    except OSError as exc:
        output.warn(
            f"{_describe_copilot_spawn_error(exc, cwd=cwd)} -- skipping marketplace refresh"
        )
        return False
    except subprocess.TimeoutExpired:
        output.warn("Marketplace refresh timed out -- continuing")
        return False


def _browse_marketplace_plugins(marketplace: str, *, cwd: Path | None = None) -> set[str] | None:
    """Return the refreshed marketplace's plugin names, or ``None`` if unknown."""
    try:
        r = subprocess.run(
            [
                _resolve_copilot() or "copilot",
                "plugin",
                "marketplace",
                "browse",
                marketplace,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
    except OSError as exc:
        output.warn(
            f"{_describe_copilot_spawn_error(exc, cwd=cwd)} -- skipping retired plugin purge"
        )
        return None
    except subprocess.TimeoutExpired:
        output.warn("Marketplace inventory timed out -- skipping retired plugin purge")
        return None

    if r.returncode != 0:
        output.warn("Marketplace inventory returned non-zero -- skipping retired plugin purge")
        return None

    names: set[str] = set()
    for line in r.stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("\u2022 "):
            continue
        name = stripped[2:].split(" - ", 1)[0].strip()
        if name:
            names.add(name)
    if not names:
        output.warn(
            "Marketplace inventory contained no plugin names -- skipping retired plugin purge"
        )
        return None
    return names


def _uninstall_one_plugin_payload(name: str, marketplace: str, *, cwd: Path | None = None) -> str:
    """Uninstall one confirmed-retired, inactive marketplace payload."""
    ref = f"{name}@{marketplace}"
    try:
        r = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", "uninstall", ref],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
    except OSError as exc:
        return _describe_copilot_spawn_error(exc, cwd=cwd)
    except subprocess.TimeoutExpired:
        return "uninstall timed out"
    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            output.ok(line)
        return "OK (purged)"
    return f"uninstall exited {r.returncode}"


def _update_one_plugin_payload(name: str, marketplace: str, *, cwd: Path | None = None) -> str:
    """Update (or install) a single copilot-extensions plugin payload."""
    from . import reconcile
    from .activation_preservation import (
        PluginStateError,
        run_install_preserving_activation,
    )

    ref = f"{name}@{marketplace}"
    installed = reconcile.core_installed_payload_dir(name) is not None
    verb = "update" if installed else "install"
    copilot = _resolve_copilot() or "copilot"

    def _run(selected_verb: str):
        argv = [copilot, "plugin", selected_verb, ref]
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 120,
            "cwd": cwd,
        }
        if selected_verb == "install":
            return run_install_preserving_activation(argv, ref, **kwargs)
        return subprocess.run(argv, **kwargs)

    try:
        r = _run(verb)
    except OSError as exc:
        message = _describe_copilot_spawn_error(exc, cwd=cwd)
        output.warn(f"{message} -- skipping plugin payload update")
        return message
    except PluginStateError as exc:
        output.warn(f"Plugin state for {name} could not be preserved: {exc}")
        return f"plugin state error: {exc}"
    except subprocess.TimeoutExpired:
        output.warn(f"Plugin {verb} for {name} timed out -- continuing")
        return "timed out"

    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            output.ok(line)
        return "OK" if installed else "OK (installed)"

    if installed:
        output.info(f"Plugin update for {name} returned non-zero -- retrying with install")
    else:
        output.info(f"Plugin install for {name} returned non-zero -- retrying")
    try:
        r2 = _run("install")
    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        PluginStateError,
    ) as exc:
        output.warn(f"Plugin install retry for {name} failed: {exc}")
        return "install retry failed"
    if r2.returncode == 0:
        for line in r2.stdout.strip().splitlines():
            output.ok(line)
        return "OK (installed)"
    if installed:
        output.warn(
            f"Plugin update for {name} returned non-zero (continuing with installed version)"
        )
        return f"update exited {r.returncode}"
    else:
        output.warn(f"Plugin install for {name} returned non-zero")
    detail = "\n".join(x for x in [r2.stdout.strip(), r2.stderr.strip()] if x)
    return detail or f"install exited {r2.returncode}"


from . import update_runtime

_PluginActivation = update_runtime._PluginActivation
_RegisteredPluginTarget = update_runtime._RegisteredPluginTarget
_update_registered_plugins = update_runtime._update_registered_plugins
_registered_plugin_targets = update_runtime._registered_plugin_targets
_module_names = update_runtime._module_names
_reconcile_registered_runtimes = update_runtime._reconcile_registered_runtimes
_reconcile_one_runtime = update_runtime._reconcile_one_runtime
_fast_forward_project_anchors = update_runtime._fast_forward_project_anchors
_self_entry_present = update_runtime._self_entry_present
_heal_stale_anchor_if_self_missing = update_runtime._heal_stale_anchor_if_self_missing
_update_modules = update_runtime._update_modules
_find_installed_plugin_dir = update_runtime._find_installed_plugin_dir

_BOOTSTRAP_SERVICES = ("agent-worktrees",)


_BOOTSTRAP_SERVICES = ("agent-worktrees",)


def plan_pre_launch() -> dict:
    """Check bootstrap service staleness and return an action plan dict."""
    from . import reconcile as _reconcile

    repo_dir = _find_repo_dir()
    if not repo_dir:
        return {"action": "continue", "reason": "no-repo"}

    try:
        config = cfg.load_config()
    except Exception:
        return {"action": "continue", "reason": "no-config"}

    env = _resolve_environment(config)
    all_services = svc.discover_services(
        repo_dir,
        env,
        service_paths=config.default_repo.service_paths or None,
    )

    bootstrap_names = _BOOTSTRAP_SERVICES + tuple(
        getattr(config.default_repo, "bootstrap_services", None) or ()
    )
    bootstrap = {s.name: s for s in all_services if s.name in bootstrap_names}
    diagnostics: list[dict[str, str]] = []
    bootstrap.pop("agent-worktrees", None)
    aw_plugin_dir = _reconcile.core_installed_payload_dir("agent-worktrees")
    aw_runtime_root: Path | None = None
    aw_environment: dict[str, str] = {}
    aw_resolution_failed = False
    if aw_plugin_dir is None:
        aw_resolution_failed = True
        try:
            explicit_context = _reconcile._explicit_context_target()
        except ValueError as error:
            diagnostics.append(
                {
                    "service": "agent-worktrees",
                    "reason": "installation-context-invalid",
                    "message": str(error),
                }
            )
        else:
            if explicit_context is not None:
                diagnostics.append(
                    {
                        "service": "agent-worktrees",
                        "reason": "installation-context-payload-missing",
                        "message": (
                            "selected context cannot be validated because the "
                            "agent-worktrees payload is not installed"
                        ),
                    }
                )
    else:
        try:
            aw_environment, aw_runtime_root = _reconcile.runtime_installer_environment(
                "agent-worktrees",
                aw_plugin_dir,
                base={},
            )
        except ValueError as error:
            aw_resolution_failed = True
            diagnostics.append(
                {
                    "service": "agent-worktrees",
                    "reason": "installation-context-invalid",
                    "message": str(error),
                }
            )

    if not aw_resolution_failed and aw_runtime_root is not None:
        wm_dir = aw_runtime_root
        wm_manifest = wm_dir / "deploy-manifest.json"
        if not wm_manifest.exists():
            staleness = "missing"
        else:
            _manifest_data = svc._read_manifest(wm_manifest)
            _source_kind = ((_manifest_data or {}).get("source") or {}).get("kind")
            if _source_kind == "marketplace" and aw_plugin_dir is not None:
                staleness = svc.check_marketplace_staleness(wm_manifest, aw_plugin_dir)
            else:
                staleness = svc.check_staleness(wm_manifest, repo_dir)
        if staleness != "current":
            installer = next(
                (
                    aw_plugin_dir / "scripts" / name
                    for name in svc._preferred_installer_order()
                    if (aw_plugin_dir / "scripts" / name).exists()
                ),
                None,
            )
            result = (
                _core_helper("_build_installer_argv", _build_installer_argv)(
                    installer,
                    install_dir=aw_runtime_root,
                )
                if installer is not None
                else None
            )
            if result is not None:
                cmd, cmd_argv = result
                updates: list[dict] = [
                    {
                        "service": "agent-worktrees",
                        "staleness": staleness,
                        "command": cmd,
                        "argv": cmd_argv,
                        "environment": aw_environment,
                        "unset_environment": list(_reconcile._RUNTIME_ENV_UNSET),
                        "runtime_root": str(aw_runtime_root),
                    }
                ]
                append_update_if_stale = _core_helper(
                    "_append_update_if_stale", _append_update_if_stale
                )
                for s in bootstrap.values():
                    append_update_if_stale(s, repo_dir, updates)
                result = {"action": "self-update", "updates": updates}
                if diagnostics:
                    result["diagnostics"] = diagnostics
                return result

    updates: list[dict] = []
    append_update_if_stale = _core_helper("_append_update_if_stale", _append_update_if_stale)
    for s in bootstrap.values():
        append_update_if_stale(s, repo_dir, updates)

    if updates:
        result = {"action": "self-update", "updates": updates}
        if diagnostics:
            result["diagnostics"] = diagnostics
        return result
    result = {"action": "continue"}
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


def cmd_pre_launch(args: argparse.Namespace) -> int:
    """Emit the pre-launch staleness plan as JSON (see ``plan_pre_launch``)."""
    print(json.dumps(_core_helper("plan_pre_launch", plan_pre_launch)()))
    return 0


def _build_installer_argv(
    installer: Path,
    *,
    install_dir: Path | None = None,
) -> tuple[str, list[str]] | None:
    """Build a (display_cmd, argv) pair for running an installer."""
    if installer.suffix == ".sh":
        if platform.system() == "Windows":
            ps1_sibling = installer.with_name("install.ps1")
            if ps1_sibling.exists():
                installer = ps1_sibling
            else:
                return None
        else:
            cmd = f"bash {installer} update"
            argv = ["bash", str(installer), "update"]
            if install_dir is not None:
                argv.extend(["--install-dir", str(install_dir)])
            return cmd, argv
    if installer.suffix == ".ps1":
        cmd = f"pwsh -File {installer} update"
        argv = ["pwsh", "-File", str(installer), "update"]
        if install_dir is not None:
            argv.extend(["-InstallDir", str(install_dir)])
        return cmd, argv
    return None


def _append_update_if_stale(
    service: svc.ServiceInfo,
    repo_dir: Path,
    updates: list[dict[str, str]],
) -> None:
    """Check staleness and append an update entry if needed."""
    st = svc.get_service_status(service, repo_dir)
    if st.staleness == "current":
        return
    if not service.installer_path:
        return
    installer = repo_dir / service.installer_path
    if not installer.exists():
        return
    result = _core_helper("_build_installer_argv", _build_installer_argv)(installer)
    if not result:
        return
    cmd, argv = result
    updates.append(
        {
            "service": service.name,
            "staleness": st.staleness,
            "command": cmd,
            "argv": argv,
        }
    )


def cmd_reconcile_plugins(args: argparse.Namespace) -> int:
    """Reconcile repo-configured copilot-extensions plugins (JSON action plan)."""
    from . import reconcile

    status_arg = getattr(args, "status", None)

    def _write_status(payload: dict[str, object]) -> None:
        if not status_arg:
            return
        status_path = Path(status_arg).expanduser()
        status_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        temp = status_path.with_name(f".{status_path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, status_path)

    repo_override = getattr(args, "repo", None)
    repo_dir = repo_override or _find_repo_dir()
    if not repo_dir:
        if getattr(args, "apply", False):
            _write_status({"ok": False, "reason": "no-repo", "failed": []})
        print(json.dumps({"action": "continue", "reason": "no-repo"}))
        return 1 if getattr(args, "apply", False) else 0

    machine = getattr(args, "machine", None)
    with_payload = getattr(args, "with_payload_refresh", False)

    if getattr(args, "apply", False):

        def _log(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)

        try:
            summary = reconcile.apply_plan(
                Path(repo_dir),
                machine=machine,
                include_payload_refresh=with_payload,
                log=_log,
            )
        except Exception as e:
            print(f"provision: error: {e}", file=sys.stderr)
            _write_status(
                {
                    "ok": False,
                    "repo": str(Path(repo_dir).resolve()),
                    "reason": str(e),
                    "failed": [],
                }
            )
            return 1
        failed = [item for item in summary.get("executed", []) if not item.get("ok", False)]
        _write_status(
            {
                "ok": not failed,
                "repo": str(Path(repo_dir).resolve()),
                "failed": failed,
            }
        )
        print(json.dumps(summary))
        return 1 if failed else 0

    try:
        plan = reconcile.build_plan(
            Path(repo_dir),
            machine=machine,
            include_payload_refresh=with_payload,
            save=not getattr(args, "peek", False),
        )
    except Exception as e:
        print(json.dumps({"action": "continue", "reason": f"error: {e}"}))
        return 0

    print(json.dumps(plan))
    return 0


def cmd_uninstall_plugins(args: argparse.Namespace) -> int:
    """Sweep every deployed core plugin runtime via its own uninstall action."""
    from . import reconcile

    apply_ = getattr(args, "apply", False)
    verify = getattr(args, "verify", False)
    if apply_ and verify:
        print(json.dumps({"error": "--apply and --verify are mutually exclusive"}))
        return 2

    if apply_ or verify:

        def _log(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)

        summary = reconcile.apply_uninstall_plan(dry_run=verify, log=_log)
        failed = [item for item in summary.get("executed", []) if not item.get("ok", False)]
        print(json.dumps(summary))
        return 1 if failed else 0

    plan = reconcile.build_uninstall_plan()
    print(json.dumps(plan, indent=2))
    return 0
