"""Private suite/preparation state and pure registry comparison.

Inherited service roots and installation participants are detached in ordinary
runs; supervisor custody stays with its existing owner. Native fixtures must
also pass explicit private installation/data paths: environment redirection
does not redefine Windows special-folder APIs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

CONTAINED_ENV = "COPILOT_EXTENSIONS_TEST_CONTAINED"
SANDBOX_ENV = "COPILOT_EXTENSIONS_TEST_SANDBOX"
ALLOW_HOST_STATE_ENV = "COPILOT_EXTENSIONS_ALLOW_HOST_STATE"

_ROOT_ENV = {
    "HOME": "home",
    "USERPROFILE": "home",
    "APPDATA": "home/AppData/Roaming",
    "LOCALAPPDATA": "home/AppData/Local",
    "PROGRAMDATA": "program-data",
    "PSModulePath": "powershell-modules",
    "XDG_CONFIG_HOME": "home/.config",
    "XDG_CACHE_HOME": "home/.cache",
    "XDG_DATA_HOME": "home/.local/share",
    "XDG_STATE_HOME": "home/.local/state",
    "XDG_RUNTIME_DIR": "run",
    "COPILOT_HOME": "home/.copilot",
    "AGENT_HOME": "home",
    "AGENT_SCRATCH_ROOT": "scratch",
    "PYTHONPYCACHEPREFIX": "pycache",
    "UV_CREDENTIALS_DIR": "uv/credentials",
    "AGENT_WORKTREES_HOME": "agent-worktrees",
    "AGENT_WORKTREES_PIVOTS_DIR": "agent-worktrees/pivots",
    "AGENT_WORKTREES_PLUGINS_DIR": "agent-worktrees/plugins",
    "AGENT_BRIDGE_CONFIG_DIR": "agent-bridge/config",
    "AGENT_BRIDGE_PROVIDERS_DIR": "agent-bridge/providers",
    "AGENT_DISPATCH_ROUTING_DIR": "agent-dispatch/routing",
    "AGENT_DISPATCH_RUN_DIR": "agent-dispatch/run",
    "AGENT_INDEX_DATA_DIR": "agent-index/data",
    "AGENT_INDEX_BACKUP_DIR": "agent-index/backups",
    "AGENT_INDEX_ENGINE_HOME": "agent-index/engine",
    "AGENT_LOGGER_HOME": "agent-logger",
    "AGENT_MCP_HOME": "agent-mcp",
    "AGENT_MCP_INSTALL_DIR": "agent-mcp-runtime",
    "WORKTREE_MANAGER_ROOT": "worktree-manager",
    "AGENT_VAULT_CORE_RUN_DIR": "agent-vault/run",
    "AGENT_VAULT_KEK_DIR": "agent-vault/kek",
    "TEMP": "tmp",
    "TMP": "tmp",
    "TMPDIR": "tmp",
}

_FILE_ENV = {
    "AGENT_CONTAINERS_CONFIG": "agent-containers/config.yaml",
    "AGENT_DISPATCH_DB": "agent-dispatch/state/dispatch.db",
    "AGENT_DISPATCH_ENDPOINT": "agent-dispatch/run/endpoint.json",
    "AGENT_WORKTREES_PROJECTS_YAML": "agent-worktrees/projects.yaml",
    "AGENT_WORKTREES_REPOS_YAML": "agent-worktrees/repos.yaml",
}

ROOT_ENV_NAMES = tuple(_ROOT_ENV)
OPTIONAL_FILE_ENV_NAMES = tuple(_FILE_ENV)
ALWAYS_SANDBOX_ENV_NAMES = (
    "TEMP", "TMP", "TMPDIR", "XDG_RUNTIME_DIR", "AGENT_SCRATCH_ROOT", "PYTHONPYCACHEPREFIX",
    "UV_CREDENTIALS_DIR",
)

# Shared with coverage collection so both runners detach from the same host state.
ALWAYS_SCRUB_NAMES = {
    "AGENT_RT_ROOT",
    "AGENT_WORKTREES_CONFIG_ROOT",
    "AGENT_WORKTREES_OWNER_REF",
    "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_AGENT_SESSION_ID",
    "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
    "COPILOT_PLUGIN_ROOT",
    "COPILOT_EXTENSIONS_TEST_VENV_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT",
    "COPILOT_EXTENSIONS_NATIVE_INSTALLER",
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
    "COPILOT_PLUGIN_INSTALL_STAGED",
    "COPILOT_PLUGIN_STAGED_FROM",
    "COPILOT_PROJECT_DIR",
    "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN",
    "UPDATE_DRIVER_WITNESS",
}
_ALWAYS_SCRUB_NAMES = ALWAYS_SCRUB_NAMES
_CREDENTIAL_NAMES = {"GH_TOKEN", "GITHUB_TOKEN"}


def capture_preparation_auth(uv, environment, cwd, *, run_metadata):
    """Capture only UV's declared auth-directory location, never its contents."""
    import installer_dependency_policy as dependencies

    error_type = dependencies.DependencyPolicyError
    scope = {
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME",
        "XDG_STATE_HOME", "UV_CREDENTIALS_DIR", "UV_PREVIEW_FEATURES",
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
    }
    selected = dependencies._captured_environment({
        key: environment[key] for key in environment
        if (key.upper() if os.name == "nt" else key) in scope
    })
    explicit = selected.get("UV_CREDENTIALS_DIR")
    requirement = (
        "Test preparation requires selected UV supporting 'uv auth dir' metadata "
        "and one absolute authentication-directory path; select compatible UV "
        "or correct UV_CREDENTIALS_DIR. No installation was attempted."
    )
    if explicit is not None and (
        not isinstance(explicit, str) or not explicit or not Path(explicit).is_absolute()
        or any(ord(char) < 32 or ord(char) == 127 for char in explicit)
    ):
        raise error_type(requirement)
    try:
        result = run_metadata(
            [dependencies._select_uv_executable(uv), "auth", "dir"], env=selected, cwd=cwd,
            timeout=15, capture=True, binary=True, input_text=None,
            max_stdout_bytes=32768, max_stderr_bytes=16384,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        raise error_type(requirement) from None
    if not isinstance(result, subprocess.CompletedProcess) or (
        type(result.returncode) is not int or result.returncode != 0
        or not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
    ):
        raise error_type(requirement)
    if len(result.stdout) > 32768 or len(result.stderr) > 16384:
        raise error_type(requirement)
    try:
        value = result.stdout.decode("utf-8")
        value = value[:-2] if value.endswith("\r\n") else value[:-1] if value.endswith("\n") else value
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid directory metadata")
        directory = Path(value)
        if not directory.is_absolute():
            raise ValueError("relative directory metadata")
        directory = directory.resolve()
        if directory.exists() and not directory.is_dir():
            raise ValueError("directory metadata names a file")
        if explicit is not None and Path(explicit).resolve() != directory:
            raise ValueError("directory metadata differs from the explicit input")
    except (OSError, ValueError, RuntimeError):
        raise error_type(requirement) from None
    return {"UV_CREDENTIALS_DIR": str(directory)}


def preparation_environment(
    base: Mapping[str, str], sandbox: Path, project: Path, *, uv: str,
) -> dict[str, str]:
    """Retain the dependency owner's resolver inputs before detaching host state."""
    import installer_dependency_policy as dependencies
    from agent_procutil.owned_process import OwnedCleanupError, OwnedExecutionBudget, run_caller_owned

    def metadata(argv, **options):
        try:
            return run_caller_owned(
                argv, **options, budget=OwnedExecutionBudget.from_timeout(20, cleanup_reserve=5),
            )
        except OwnedCleanupError as exc:
            if __package__:
                from .plugin_test_containment import UnreapedProcessError
            else:
                from plugin_test_containment import UnreapedProcessError

            raise UnreapedProcessError("Authentication metadata cleanup unconfirmed", job=exc.job) from exc

    authentication = capture_preparation_auth(uv, base, project, run_metadata=metadata)

    inputs = dependencies._config_inputs([], base, project)
    configuration = {}
    for path, is_project in reversed(inputs):
        data = dependencies._toml(path)
        if is_project:
            data = {
                key: value for key, value in data["tool"]["uv"].items()
                if key not in ("sources", "workspace")
            }
        configuration = dependencies._merge_config(data, configuration)
    environment = isolated_environment(base, sandbox)
    environment.update(authentication)
    if inputs:
        retained = sandbox.resolve() / "uv.toml"
        dependencies._write_toml(retained, configuration)
        environment["UV_CONFIG_FILE"] = str(retained)
    return environment


def _detach_suite_overrides(env: dict[str, str], root: Path) -> None:
    """Route inherited service state privately, without moving supervisor custody."""
    for name in tuple(env):
        key = name.upper()
        if "TEST_SUPERVISOR" in key:
            continue
        if not key.startswith(("AGENT_", "WORKTREE_MANAGER_", "COPILOT_PLUGIN_")):
            continue
        if not env[name].strip():
            env.pop(name)
            continue
        if key.endswith(("_CONTEXT", "_INSTALLATION_ID")):
            env.pop(name)
        elif key.endswith(("_ROOT", "_ROOTS", "_HOME", "_DIR")):
            target = root / "overrides" / key.lower()
            target.mkdir(parents=True, exist_ok=True)
            env[name] = str(target)
        elif key.endswith(("_CONFIG", "_CONFIG_PATH", "_DB", "_ENDPOINT", "_SOCKET", "_FILE")):
            target = root / "overrides" / key.lower() / "state"
            target.parent.mkdir(parents=True, exist_ok=True)
            env[name] = str(target)


def isolated_environment(
    base: Mapping[str, str],
    sandbox: Path,
    *,
    allow_explicit_tiers: bool = False,
    allow_host_state: bool = False,
) -> dict[str, str]:
    """Return a contained environment, host-detached unless explicitly allowed."""
    if allow_host_state and not allow_explicit_tiers:
        raise ValueError("allow_host_state requires allow_explicit_tiers")
    root = sandbox.resolve()
    env = dict(base)
    for name in tuple(env):
        if name.upper() in _ALWAYS_SCRUB_NAMES:
            env.pop(name)
    if allow_host_state:
        env[ALLOW_HOST_STATE_ENV] = "1"
        for name in ALWAYS_SANDBOX_ENV_NAMES:
            if os.name == "nt":
                for inherited_name in tuple(env):
                    if inherited_name.casefold() == name.casefold():
                        env.pop(inherited_name)
            value = root.joinpath(*_ROOT_ENV[name].split("/"))
            value.mkdir(parents=True, exist_ok=True)
            env[name] = str(value)
    else:
        _detach_suite_overrides(env, root)
        env.pop(ALLOW_HOST_STATE_ENV, None)
        for name in _CREDENTIAL_NAMES:
            env.pop(name, None)
        for name, relative in _ROOT_ENV.items():
            if os.name == "nt":
                for inherited_name in tuple(env):
                    if inherited_name.casefold() == name.casefold():
                        env.pop(inherited_name)
            value = root.joinpath(*relative.split("/"))
            value.mkdir(parents=True, exist_ok=True)
            env[name] = str(value)
        for name, relative in _FILE_ENV.items():
            if name not in base:
                env.pop(name, None)
                continue
            value = root.joinpath(*relative.split("/"))
            value.parent.mkdir(parents=True, exist_ok=True)
            env[name] = str(value)
    env[CONTAINED_ENV] = "1"
    env[SANDBOX_ENV] = str(root)
    if allow_explicit_tiers:
        env["COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS"] = "1"
    else:
        env.pop("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS", None)
    return env


def registry_environment_changes(
    label: str,
    expected: dict[str, tuple[Any, int]],
    current: dict[str, tuple[Any, int]],
) -> list[str]:
    """Compare registry values case-insensitively, retaining display names."""
    expected_folded = {
        name.casefold(): (name, data) for name, data in expected.items()
    }
    current_folded = {
        name.casefold(): (name, data) for name, data in current.items()
    }
    names = sorted(set(expected_folded) | set(current_folded))
    drifted = [
        name
        for name in names
        if expected_folded.get(name, (None, None))[1]
        != current_folded.get(name, (None, None))[1]
    ]
    return [
        f"{label}:{current_folded.get(name, expected_folded.get(name))[0]}"
        for name in drifted
    ]
