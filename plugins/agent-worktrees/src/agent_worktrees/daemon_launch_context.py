"""Small resident launch-context composition using the runtime's selected readers."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
import os
from pathlib import Path
import shutil
from typing import Protocol


class FreshReader(Protocol):
    def __call__(self, *, overrides: Mapping[str, str]) -> dict[str, str]: ...


class GitReader(Protocol):
    def __call__(self, *, env: Mapping[str, str]) -> str: ...


def _anchor(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if os.name == "nt":
        return os.path.abspath(value)
    return value if os.path.isabs(value) else os.path.join(os.getcwd(), value)


def monitor_mux(env: Mapping[str, str] | None = None) -> tuple[str | None, str | None]:
    environment = os.environ if env is None else env
    values = {key.upper(): value for key, value in environment.items()}
    hint = values.get("AGENT_WORKTREES_MONITOR_MUX", "")
    if os.path.isabs(hint) and Path(hint).stem in {"psmux", "tmux"} and os.path.isfile(hint):
        return Path(hint).stem, hint
    for name in ("psmux", "tmux"):
        binary = shutil.which(name, path=values.get("PATH", os.defpath))
        if binary:
            return name, _anchor(binary)
    return None, None


def cwd(resolve_root: Callable[[], Path]) -> str:
    """Use the daemon's own cell/legacy install root, not the caller's cwd.

    An unresolved or not-yet-materialized root degrades to HOME. Returning an
    absolute existing directory preserves the native spawn owner's contract.
    """
    try:
        root = resolve_root()
        if root.is_dir():
            return _anchor(root)
    except Exception:
        pass
    return os.path.expanduser("~")


def windowless(python: str, select: Callable[[str], str]) -> str:
    """Select the interpreter without changing the existing launch-kind policy.

    Windows venv console launchers can allocate a console through their base
    interpreter even when detached. The owning selector chooses a GUI sibling
    where appropriate, retaining its original fallback for nonstandard layouts.
    Residents with recurring console descendants instead retain console Python
    under the existing hidden-console-tree launch owner.
    """
    return select(python)


def build(
    inherited: Mapping[str, str], *, refresh: bool, fresh: FreshReader, git: GitReader,
    auth_excluded: Collection[str], scoped: Sequence[str], paths: Sequence[str],
) -> dict[str, str]:
    """Apply the caller's current case-insensitive selectors and exclusions.

    The resident retains installation context and plugin-root selectors:
    dropping them can redirect a cell to the legacy runtime root. Session IDs
    and credential exclusions apply after fresh OS data and overrides merge.
    One-operation configuration reports never become resident context.
    Path-valued selectors are anchored before changing the child's cwd.
    """
    env = dict(inherited)
    if refresh:
        prefixes = ("AGENT_", "COPILOT_", "PSMUX_", "TMUX_", "UV_", "PYTHON")
        overrides = {
            key: value for key, value in env.items()
            if key.upper().startswith(prefixes)
            or key.upper() in {
                "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "TMUX", "PSMUX", "WORKTREE_MANAGER_ROOT",
                "GH_CONFIG_DIR", "GH_HOST",
            }
        }
        _, mux = monitor_mux(env)
        if mux:
            overrides["AGENT_WORKTREES_MONITOR_MUX"] = mux
        explicit_git = any(
            key.upper() == "AGENT_GIT_EXECUTABLE" and value for key, value in env.items()
        )
        try:
            selected = git(env=env)
            overrides[
                "AGENT_GIT_EXECUTABLE" if explicit_git else "AGENT_GIT_DISCOVERED_EXECUTABLE"
            ] = selected
        except FileNotFoundError:
            if explicit_git:
                raise
        gh = shutil.which("gh", path=next(
            (value for key, value in env.items() if key.upper() == "PATH"), os.defpath,
        ))
        gh_directory = os.path.dirname(_anchor(gh)) if gh else None
        env = fresh(overrides=overrides)
        if gh_directory:
            path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
            fresh_path = env.get(path_key, "")
            env[path_key] = os.pathsep.join(filter(None, (gh_directory, fresh_path)))
    excluded = {"AGENT_CONFIGURATION_CONTEXT"} | {
        key.upper() for key in (*scoped, *auth_excluded)
    }
    anchored = {key.upper() for key in paths}
    for key, value in tuple(env.items()):
        if (
            key.upper() == "AGENT_GIT_EXECUTABLE" and value.strip()
            and (os.path.dirname(value) or "/" in value or "\\" in value)
        ):
            env[key] = _anchor(os.path.expanduser(value))
    return {
        key: _anchor(os.path.expanduser(value))
        if key.upper() in anchored and value.strip() and not Path(value.strip()).anchor
        else value
        for key, value in env.items() if key.upper() not in excluded
    }
