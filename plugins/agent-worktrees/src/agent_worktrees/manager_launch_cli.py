"""Worktree Manager resolution + bare-invocation dispatch, extracted from
``front_door_cli`` to stay under its own zero-headroom 1000-line cap (same
move already made once before for ``control_plane_providers.py`` -- see that
module's own docstring). ``front_door_cli`` re-exports every public name
here at module level, so every existing ``front_door_cli.<name>`` caller
(``__main__``'s flat re-export block, ``managed_mux_registry.py``, tests)
keeps working unchanged; this is a pure file-boundary move, not an API
change.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from . import config as cfg, output
from . import control_plane_providers as _cpp

_WORKTREE_MANAGER_BIN = "worktree-manager"
_WORKTREE_MANAGER_MIN_PICKER_VERSION = (0, 1, 0, 21)
_WORKTREE_MANAGER_ENGINE_ARGV_ENV = "WORKTREE_MANAGER_ENGINE_ARGV"
_WORKTREE_MANAGER_ROOT_ENV = "WORKTREE_MANAGER_ROOT"
_CONTROL_PLANE_PROVIDER_ENV = "AGENT_WORKTREES_CONTROL_PLANE_PROVIDER"
_WORKTREE_MANAGER_REPO_URL = "https://github.com/ThomasMichon/copilot-extensions"
_WORKTREE_MANAGER_INSTALL_SH = (
    "curl -fsSL https://raw.githubusercontent.com/ThomasMichon/"
    "copilot-extensions/main/worktree-manager/bootstrap.sh | bash"
)
_WORKTREE_MANAGER_INSTALL_PS1 = (
    "iex (irm https://raw.githubusercontent.com/ThomasMichon/"
    "copilot-extensions/main/worktree-manager/bootstrap.ps1)"
)

# See control_plane_providers.py: manifest parsing/discovery and the
# leaked-pytest-tmp_path guard (copilot-extensions#5122).
_ControlPlaneProviderManifest = _cpp._ControlPlaneProviderManifest
_CONTROL_PLANE_PROVIDERS_DIR_ENV = _cpp._CONTROL_PLANE_PROVIDERS_DIR_ENV
_parse_comparable_version = _cpp._parse_comparable_version
_control_plane_providers_dir = _cpp._control_plane_providers_dir
_LeakedTestTmpPathManifestError = _cpp._LeakedTestTmpPathManifestError
_parse_control_plane_provider_manifest = _cpp._parse_control_plane_provider_manifest
_discover_control_plane_provider_manifests = _cpp._discover_control_plane_provider_manifests


def _core():
    # Local import: avoids a module-load-time cycle with front_door_cli
    # (which imports this module to re-export its names) -- same seam
    # control_plane_providers.py's own lazy imports already preserve.
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _select_control_plane_provider_manifest() -> _ControlPlaneProviderManifest | None:
    manifests = _core_helper(
        "_discover_control_plane_provider_manifests",
        _discover_control_plane_provider_manifests,
    )()
    selected = os.environ.get(_CONTROL_PLANE_PROVIDER_ENV, "").strip()
    if selected:
        return manifests.get(selected)
    if len(manifests) == 1:
        return next(iter(manifests.values()))
    return None


def _provider_command_display_name(
    manifest: _ControlPlaneProviderManifest,
) -> tuple[str, str]:
    if manifest.provider == _WORKTREE_MANAGER_BIN:
        return f"'{_WORKTREE_MANAGER_BIN}'", "on PATH"
    return f"registered control-plane provider '{manifest.provider}'", f"via {manifest.source_path}"


def _worktree_manager_path() -> str | None:
    """Compatibility wrapper: the selected provider command's argv[0], if any."""
    manifest = _core_helper(
        "_select_control_plane_provider_manifest", _select_control_plane_provider_manifest
    )()
    return manifest.command[0] if manifest is not None else None


def _launch_probe_env() -> dict[str, str]:
    """Subprocess env for Manager/launch probes."""
    env = {**os.environ, "PYTHONUTF8": "1"}
    env.pop("PYTHONHOME", None)
    env.pop("UV_INTERNAL__PYTHONHOME", None)
    return env


def _probe_worktree_manager_version(
    command: list[str],
) -> tuple[tuple[int, int, int, int] | None, subprocess.CompletedProcess[str] | None]:
    """Run a fast ``--version`` probe and parse a comparable version tuple."""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            env=_launch_probe_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if proc.returncode != 0:
        return None, proc
    version = _parse_comparable_version(proc.stdout or "")
    if version is None:
        return None, proc
    return version, proc


def _usable_worktree_manager() -> tuple[str, ...] | None:
    """The selected control-plane provider command, when invocable (DQ8 guard)."""
    override = _core_helper("_usable_worktree_manager", _usable_worktree_manager)
    if override is not _usable_worktree_manager:
        return override()
    manifest = _core_helper(
        "_select_control_plane_provider_manifest", _select_control_plane_provider_manifest
    )()
    if manifest is None:
        return None
    version, proc = _core_helper(
        "_probe_worktree_manager_version", _probe_worktree_manager_version
    )([*manifest.command, "--version"])
    display_name, display_source = _provider_command_display_name(manifest)
    command0 = manifest.command[0]
    if proc is None:
        output.err(
            f"Ignoring an unusable {display_name} {display_source} ({command0}): "
            "it could not be run. Falling back to the bundled picker."
        )
        return None
    if version is None and proc.returncode != 0:
        output.err(
            f"Ignoring a broken {display_name} {display_source} ({command0}): it "
            f"failed a --version health check (exit {proc.returncode}). This is "
            "usually a stale binstub from an old install; reinstall or remove "
            "it. Falling back to the bundled picker."
        )
        return None
    if version is None:
        output.err(
            f"Ignoring an incompatible {display_name} {display_source} ({command0}): "
            "its --version output did not include a supported version. Falling "
            "back to the bundled picker."
        )
        return None
    if version < manifest.minimum_version:
        output.err(
            f"Ignoring an older {display_name} {display_source} ({command0}): "
            f"production Picker handoff requires {manifest.minimum_version_text} or newer. "
            "Falling back to the bundled picker."
        )
        return None
    return manifest.command


def _worktree_manager_root() -> Path:
    configured = os.environ.get(_WORKTREE_MANAGER_ROOT_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".worktree-manager"


def _current_version_slot(root: Path) -> Path | None:
    try:
        version = (root / "current-version").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not version:
        return None
    versions_dir = (root / "versions").resolve()
    slot = (versions_dir / version).resolve()
    try:
        slot.relative_to(versions_dir)
    except ValueError:
        return None
    return slot


def _usable_worktree_manager_launcher_dir() -> Path | None:
    """Return the relocated Worktree Manager launcher directory when usable."""
    override = _core_helper(
        "_usable_worktree_manager_launcher_dir", _usable_worktree_manager_launcher_dir
    )
    if override is not _usable_worktree_manager_launcher_dir:
        return override()
    slot = _core_helper("_current_version_slot", _current_version_slot)(
        _core_helper("_worktree_manager_root", _worktree_manager_root)()
    )
    if slot is None:
        return None
    version, proc = _core_helper(
        "_probe_worktree_manager_version", _probe_worktree_manager_version
    )(
        ["uv", "run", "--quiet", "--project", str(slot), "python", "-m", "worktree_manager", "--version"]
    )
    if proc is None:
        output.warn(
            "Ignoring an unusable Worktree Manager install at "
            f"{slot}: its versioned runtime could not be started."
        )
        return None
    if version is None and proc.returncode != 0:
        output.warn(
            "Ignoring a broken Worktree Manager install at "
            f"{slot}: its versioned runtime failed a --version health check "
            f"(exit {proc.returncode})."
        )
        return None
    if version is None:
        output.warn(
            "Ignoring an incompatible Worktree Manager install at "
            f"{slot}: its --version output did not include a supported version."
        )
        return None
    return slot / "bin"


def _agent_worktrees_launch_command(project: str | None) -> list[str]:
    argv = [sys.executable, "-m", "agent_worktrees"]
    if project:
        argv += ["--project", project]
    return argv


def _resolve_direct_launch_plan(
    project: str | None, passthrough: list[str]
) -> tuple[dict[str, object], str | None]:
    """Resolve the same launch plan the mux launchers consume, but in Python."""
    resolve_args = _agent_worktrees_launch_command(project)
    resolve_args += ["resolve", "--no-mux", *passthrough]
    proc = subprocess.run(
        resolve_args,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        env=_launch_probe_env(),
    )
    if proc.returncode != 0:
        return {"action": "none", "exit_code": proc.returncode}, project
    if not proc.stdout.strip():
        output.err("resolve produced no output on stdout")
        return {"action": "none", "exit_code": 1}, project
    plan = json.loads(proc.stdout)
    if isinstance(plan, dict) and isinstance(plan.get("launch"), dict):
        plan = plan["launch"]
    if not isinstance(plan, dict):
        raise RuntimeError("resolve did not return a launch plan object")
    plan_project = plan.get("project")
    if isinstance(plan_project, str) and plan_project:
        project = plan_project
    return plan, project


def _wait_for_launch_child(proc: subprocess.Popen) -> int:
    try:
        return proc.wait()
    except KeyboardInterrupt:
        try:
            return proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 130


def _run_post_exit_for_direct_launch(project: str | None, worktree_id: str) -> None:
    post_args = _agent_worktrees_launch_command(project)
    post_args += ["post-exit", worktree_id]
    result = subprocess.run(post_args, env=_launch_probe_env())
    if result.returncode != 0:
        output.warn(
            f"Post-exit finalization failed (exit code {result.returncode}). "
            "Run 'agent-worktrees finalize' to retry."
        )


def _run_direct_launch_fallback(project: str | None, passthrough: list[str]) -> int:
    """Launch Copilot directly from the resolved plan when mux support is absent."""
    while True:
        plan, project = _resolve_direct_launch_plan(project, passthrough)
        action = str(plan.get("action", "none"))
        if action == "none":
            return int(plan.get("exit_code", 0) or 0)
        if action == "refresh":
            if _core()._env_get("WORKTREE_NO_UPDATE") != "1":
                update_args = _agent_worktrees_launch_command(project)
                update_args.append("update")
                result = subprocess.run(update_args, env=_launch_probe_env())
                if result.returncode != 0:
                    output.warn("Full update returned non-zero -- continuing to relaunch")
            continue
        if action == "remote":
            ssh_alias = plan.get("ssh_alias")
            remote_cmd = plan.get("remote_command")
            if not isinstance(ssh_alias, str) or not isinstance(remote_cmd, str):
                output.err("Remote launch plan is missing ssh handoff details.")
                return 1
            proc = subprocess.Popen(["ssh", "-t", ssh_alias, remote_cmd])
            return _wait_for_launch_child(proc)
        if action != "exec":
            output.err(f"Unknown launch action: {action}")
            return 1
        work_dir = plan.get("work_dir")
        cmd = plan.get("cmd")
        if not isinstance(work_dir, str) or not work_dir:
            output.err("Resolved launch plan is missing work_dir.")
            return 1
        if not isinstance(cmd, list) or not all(isinstance(item, str) for item in cmd):
            output.err("Resolved launch plan is missing an executable command.")
            return 1
        child_env = _launch_probe_env()
        plan_env = plan.get("env")
        if isinstance(plan_env, dict):
            for key, value in plan_env.items():
                if isinstance(key, str):
                    child_env[key] = str(value)
        child_env.pop("WORKTREE_ID", None)
        child_env.pop("WORKTREE_PROJECT", None)
        proc = subprocess.Popen(cmd, cwd=work_dir, env=child_env)
        rc = _wait_for_launch_child(proc)
        worktree_id = plan.get("worktree_id")
        if isinstance(worktree_id, str) and worktree_id and plan.get("post_exit"):
            _run_post_exit_for_direct_launch(project, worktree_id)
        return rc


def _exec_worktree_manager(
    mgr: str | tuple[str, ...] | list[str], project: str | None, *, subcommand: list[str] | None = None
) -> int:
    """Hand an invocation off to the Worktree Manager (the seam)."""
    argv = [mgr] if isinstance(mgr, str) else list(mgr)
    if subcommand:
        argv += list(subcommand)
    if project:
        argv += ["--project", project]
    env = {
        **os.environ,
        _WORKTREE_MANAGER_ENGINE_ARGV_ENV: json.dumps([sys.executable, "-m", "agent_worktrees"]),
    }
    if platform.system() == "Windows":
        proc = subprocess.Popen(argv, env=env)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            try:
                rc = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = 130
        sys.exit(rc)
    os.execvpe(argv[0], argv, env)
    return 1


def _bundled_picker_available() -> bool:
    """Return True while the interactive Picker still ships inside the plugin."""
    try:
        return (Path(__file__).resolve().parent / "picker_tui" / "__init__.py").exists()
    except Exception:
        return False


def cmd_manager_install_trigger(project: str | None) -> int:
    """The install trigger: guide the user to install the Worktree Manager."""
    out = sys.stderr
    name = project or "agent-worktrees"
    is_windows = platform.system() == "Windows"
    install_cmd = _WORKTREE_MANAGER_INSTALL_PS1 if is_windows else _WORKTREE_MANAGER_INSTALL_SH

    with output.stdout_to_stderr():
        output.header(f"{name} -- interactive mode needs Worktree Manager")
    print(
        "No usable Worktree Manager is available right now. On a first run "
        "that's expected; if this machine already had one, update or repair "
        "it with the bootstrap below.",
        file=out,
    )
    print(
        "The Picker / session launcher now ships as the standalone Worktree "
        "Manager, installed and updated out-of-band from the plugin. "
        "Bootstrap it from this repo to get the interactive front door.",
        file=out,
    )
    print(file=out)
    print(f"  Source (verify this is ours): {_WORKTREE_MANAGER_REPO_URL}", file=out)
    print(file=out)
    print("  Bootstrap / update Worktree Manager:", file=out)
    print(f"    {install_cmd}", file=out)
    print(file=out)
    print(
        f"Once installed, run this binstub again -- bare `{name}` will open the "
        f"Manager. Headless agent commands are unaffected: `{name} <verb>` "
        f"(e.g. list, create, "
        "finalize) works headless without the Manager.",
        file=out,
    )
    return 0


def _is_headless_project() -> bool:
    """Return True if the active project is configured headless (CLI-only)."""
    try:
        return cfg.load_config().headless
    except Exception:
        return False


def _is_noninteractive_invocation() -> bool:
    """True when stdin is not a real, attached terminal."""
    try:
        return not sys.stdin.isatty()
    except Exception:
        return False


def cmd_noninteractive_bare() -> int:
    """Bare invocation of a binstub with no attached interactive terminal."""
    try:
        project = cfg.project_name()
    except Exception:
        project = "<project>"
    print(
        f"'{project}' was invoked without an attached interactive terminal -- "
        f"refusing to open the Manager/Picker (nothing would be able to drive "
        f"or close it).",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    rc = _core().cmd_worktree_dispatch(["list"])
    print(file=sys.stderr)
    print(
        f"Manage it with: {project} worktree <create|status|push|finalize|cleanup>",
        file=sys.stderr,
    )
    return rc


def cmd_headless_bare() -> int:
    """Bare invocation of a headless project's binstub."""
    try:
        project = cfg.project_name()
    except Exception:
        project = "<project>"
    print(
        f"'{project}' is a headless (CLI-only) project -- it is driven via "
        f"worktree commands, not an interactive session.",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    rc = _core().cmd_worktree_dispatch(["list"])
    print(file=sys.stderr)
    print(
        f"Manage it with: {project} worktree <create|status|push|finalize|cleanup>",
        file=sys.stderr,
    )
    return rc


def dispatch_bare_invocation(has_project: bool) -> int:
    """Handle the bare no-args front door."""
    # Local import: cmd_help_unrouted stays in front_door_cli (part A); this
    # module is part B. See this module's own docstring for the split.
    from . import front_door_cli as _fdc

    is_noninteractive = _core_helper("_is_noninteractive_invocation", _is_noninteractive_invocation)
    noninteractive_bare = _core_helper("cmd_noninteractive_bare", cmd_noninteractive_bare)
    help_unrouted = _core_helper("cmd_help_unrouted", _fdc.cmd_help_unrouted)
    is_headless = _core_helper("_is_headless_project", _is_headless_project)
    headless_bare = _core_helper("cmd_headless_bare", cmd_headless_bare)
    usable_manager = _core_helper("_usable_worktree_manager", _usable_worktree_manager)
    exec_worktree_manager = _core_helper("_exec_worktree_manager", _exec_worktree_manager)
    bundled_picker_available = _core_helper("_bundled_picker_available", _bundled_picker_available)
    manager_install_trigger = _core_helper(
        "cmd_manager_install_trigger", cmd_manager_install_trigger
    )
    if is_noninteractive():
        if has_project:
            return noninteractive_bare()
        return help_unrouted()
    if has_project:
        if is_headless():
            return headless_bare()
        mgr = usable_manager()
        if mgr:
            return exec_worktree_manager(mgr, cfg.active_project())
        if bundled_picker_available():
            return _core().cmd_launch([])
        return manager_install_trigger(cfg.active_project())
    mgr = usable_manager()
    if mgr:
        return exec_worktree_manager(mgr, None)
    if bundled_picker_available():
        return help_unrouted()
    return manager_install_trigger(None)
