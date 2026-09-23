"""External-state-root resolution for knowledge-repo config overlays.

Kept separate from ``config.py`` so the runtime config surface stays under the
module-size cap while mirroring the standalone bootstrap resolver's contract.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable

WORKTREES_CONFIG_RELATIVE = Path(".agent-worktrees") / "config.yaml"
INSTALLATION_CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"
WORKTREES_COMMAND_ENV = "AGENT_WORKTREES_COMMAND"
MAX_CONFIG_BYTES = 256 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key: {key}")
        value[key] = item
    return value


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _safe_file(path: Path, root: Path) -> tuple[str, Path | None]:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return "invalid", None
    candidate = resolved_root / path.relative_to(root)
    current = resolved_root
    for part in candidate.relative_to(resolved_root).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return "absent", None
        except OSError:
            return "invalid", None
        if _is_link_or_reparse(info):
            return "invalid", None
        if current != candidate and not stat.S_ISDIR(info.st_mode):
            return "invalid", None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        size = resolved.stat().st_size
    except (OSError, ValueError):
        return "invalid", None
    if not stat.S_ISREG(resolved.stat().st_mode):
        return "invalid", None
    if size > MAX_CONFIG_BYTES:
        return "invalid", None
    return "ready", resolved


def _load_yaml_mapping(path: Path) -> tuple[str, dict[str, Any] | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "invalid", None
    try:
        import yaml

        value = yaml.safe_load(text)
    except Exception:
        return "invalid", None
    return ("ready", value) if isinstance(value, dict) else ("invalid", None)


def requires_external(root: Path) -> tuple[str, bool]:
    path = root / WORKTREES_CONFIG_RELATIVE
    state, safe_path = _safe_file(path, root)
    if state == "absent":
        return "ready", False
    if state != "ready" or safe_path is None:
        return "invalid", False
    state, data = _load_yaml_mapping(safe_path)
    if state != "ready" or data is None:
        return state, False
    for key in ("stateless", "requires_external_state_root"):
        if key in data and not isinstance(data[key], bool):
            return "invalid", False
    return "ready", bool(
        data.get("stateless") or data.get("requires_external_state_root")
    )


def _worktrees_command() -> str | None:
    explicit = os.environ.get(WORKTREES_COMMAND_ENV)
    if explicit is not None:
        value = explicit.strip()
        return value or None
    return shutil.which("agent-worktrees")


def _load_peer_launch() -> Any:
    from . import _peer_launch as peer_launch

    return peer_launch


def _validate_index_owner_or_refuse(
    raw_context: str, load_installation_context: Callable[[], dict | None]
) -> dict[str, Any]:
    peer_launch = _load_peer_launch()
    context = load_installation_context()
    plugin_root_text = context.get("pluginRoot") if isinstance(context, dict) else None
    if not isinstance(plugin_root_text, str) or not plugin_root_text.strip():
        raise peer_launch.ContextRefused(
            "agent-index installation context is missing pluginRoot"
        )
    plugin_root = Path(plugin_root_text).expanduser().resolve()
    try:
        return peer_launch.validate_owner("agent-index", plugin_root, raw_context)
    except (OSError, ValueError, ImportError) as error:
        raise peer_launch.ContextRefused(
            f"agent-index installation context refused: {error}"
        ) from error


def _run_state_root_same_cell(
    own: dict[str, Any], raw_context: str, *, cwd: Path | None, project: str | None
) -> subprocess.CompletedProcess[str] | None:
    peer_launch = _load_peer_launch()
    arguments = ("state-root", "--json") if project is None else (
        "--project", project, "state-root", "--json",
    )
    prefix = peer_launch.launch_prefix(
        "agent-index", Path(own["pluginRoot"]), raw_context, "agent-worktrees"
    )
    try:
        result = subprocess.run(
            [*prefix, *arguments],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            **peer_launch.no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise peer_launch.ContextRefused(
            f"Same-cell worktrees state-root invocation failed: {error}"
        ) from error
    if result.returncode == 126:
        raise peer_launch.ContextRefused(
            result.stderr.strip() or "Same-cell worktrees context refused"
        )
    return result


def _worktrees_argv(command: str, *arguments: str) -> list[str]:
    if os.name == "nt" and Path(command).suffix.casefold() == ".ps1":
        shell = shutil.which("pwsh") or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        return [shell, "-NoProfile", "-File", command, *arguments]
    return [command, *arguments]


def _platform_key() -> str:
    if platform.system() == "Windows":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _project_name_for_root(root: Path, agent_worktrees_home: Callable[[], Path]) -> str | None:
    state, registry = _load_yaml_mapping(agent_worktrees_home() / "repos.yaml")
    if state != "ready" or registry is None:
        return None
    repos = registry.get("repos")
    if not isinstance(repos, dict):
        return None
    key = _platform_key()
    try:
        target = root.resolve(strict=True)
    except OSError:
        target = root
    for name, entry in repos.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        candidate = entry.get(key)
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            candidate_path = Path(candidate).expanduser().resolve(strict=True)
        except OSError:
            continue
        if candidate_path == target:
            return name
    return None


def _run_state_root(
    command: str, environment: dict[str, str], *, cwd: Path | None, project: str | None
) -> subprocess.CompletedProcess[str] | None:
    arguments = ("state-root", "--json") if project is None else (
        "--project", project, "state-root", "--json",
    )
    try:
        return subprocess.run(
            _worktrees_argv(command, *arguments),
            cwd=str(cwd) if cwd is not None else None,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _external_state_root_runner(
    root: Path,
    *,
    load_installation_context: Callable[[], dict | None],
) -> tuple[Any, None] | tuple[None, tuple[str, Path | None]]:
    raw_context = os.environ.get(INSTALLATION_CONTEXT_ENV, "").strip()
    if raw_context and os.environ.get(WORKTREES_COMMAND_ENV) is None:
        own = _validate_index_owner_or_refuse(raw_context, load_installation_context)
        peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
        if not peer_root.exists() and not peer_root.is_symlink():
            return None, ("unavailable", None)

        def run(cwd: Path | None, project: str | None) -> subprocess.CompletedProcess[str] | None:
            return _run_state_root_same_cell(own, raw_context, cwd=cwd, project=project)

        return run, None
    command = _worktrees_command()
    if not command:
        return None, ("unavailable", None)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "COPILOT_PLUGIN_ROOT",
            "PLUGIN_ROOT",
            "CLAUDE_PLUGIN_ROOT",
            "AGENT_INDEX_PAYLOAD_ROOT",
            "PYTHONHOME",
            "PYTHONPATH",
        }
    }

    def run(cwd: Path | None, project: str | None) -> subprocess.CompletedProcess[str] | None:
        return _run_state_root(command, environment, cwd=cwd, project=project)

    return run, None


def external_state_root(
    root: Path,
    *,
    load_installation_context: Callable[[], dict | None],
    agent_worktrees_home: Callable[[], Path],
) -> tuple[str, Path | None]:
    run, terminal = _external_state_root_runner(
        root, load_installation_context=load_installation_context
    )
    if run is None:
        assert terminal is not None
        return terminal
    result = run(root, None)
    if result is None:
        return "unavailable", None
    if result.returncode != 0:
        project = _project_name_for_root(root, agent_worktrees_home)
        if project is None:
            return "unavailable", None
        result = run(None, project)
        if result is None or result.returncode != 0:
            return "unavailable", None
    try:
        payload = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except (TypeError, ValueError):
        return "invalid", None
    if (
        not isinstance(payload, dict)
        or payload.get("requires_external") is not True
        or payload.get("bound") is not True
        or payload.get("source") != "knowledge_repo"
        or not isinstance(payload.get("repo"), str)
        or not payload["repo"].strip()
        or not isinstance(payload.get("state_root"), str)
        or not payload["state_root"].strip()
        or payload.get("error") not in (None, "")
    ):
        return "invalid", None
    try:
        state_root = Path(payload["state_root"]).expanduser().resolve(strict=True)
        state_root.relative_to(state_root.anchor)
        if state_root == root.resolve(strict=True) or not state_root.is_dir():
            return "invalid", None
    except (OSError, ValueError):
        return "unavailable", None
    return "ready", state_root
