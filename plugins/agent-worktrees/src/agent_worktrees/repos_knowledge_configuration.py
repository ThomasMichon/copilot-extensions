"""Native Configuration adapter to the selected knowledge-binding payload."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import sysconfig

from dropin_registry import ScanAuthority
from plugin_activation import resolve_active_plugins

from . import config as cfg
from .repos_configuration_sources import ConfigurationError


def owner_script(project: str) -> Path:
    try:
        config = cfg.load_project_config(project, include_control_plane_related_pr=False)
        project_root = Path(config.default_repo.anchor)
    except KeyError as exc:
        raise ConfigurationError(
            "binding-owner-unavailable", "The selected project needs an unambiguous default repository.",
        ) from exc
    report = resolve_active_plugins(
        home=cfg._home(), project_root=project_root,
        plugin_names=("harness-knowledge",),
    )
    owners = list(report.active.values())
    if report.authority is ScanAuthority.INDETERMINATE or len(owners) != 1:
        raise ConfigurationError(
            "binding-owner-unavailable", "Enable and prepare one qualified harness-knowledge owner.",
        )
    selected = owners[0].root_for_scope("project:current") or owners[0].root_for_scope("global")
    if selected is None:
        raise ConfigurationError("binding-owner-unavailable", "The binding owner has no selected activation scope.")
    root = selected.resolve(strict=True)
    script = (root / "skills" / "binding-knowledge" / "scripts" / "bind_knowledge.py").resolve(strict=True)
    if not script.is_file() or not script.is_relative_to(root):
        raise ConfigurationError("binding-owner-unavailable", "The selected binding payload is unavailable.")
    return script


def owner_status(project: str) -> str:
    try:
        owner_script(project)
        return "available"
    except (ValueError, OSError, RuntimeError):
        return "unavailable"


def _owner_arguments(script: Path, project: str, name: str, operation: str, token: str = "") -> list[str]:
    entry = Path(sysconfig.get_path("scripts")) / (
        "agent-worktrees.exe" if sys.platform == "win32" else "agent-worktrees"
    )
    if not entry.is_file():
        raise ConfigurationError("binding-native-owner-unavailable", "The selected native engine entry is unavailable.")
    args = [
        str(script), "--harness", project, "--knowledge", name,
        "--agent-worktrees-path", str(entry), "--configuration", operation, "--json",
    ]
    if operation == "apply-locked":
        args += ["--preview-token", token]
    return args


def _apply_worker(project: str, name: str, token: str, expected_script: str) -> None:
    from . import repos
    from .repos_registry_transaction import registry_lock

    with registry_lock(repos._repos_yaml_path()):
        script = owner_script(project)
        if str(script) != expected_script:
            raise ConfigurationError("binding-owner-changed", "The binding owner changed; preview again.")
        previous = sys.argv
        try:
            sys.argv = _owner_arguments(script, project, name, "apply-locked", token)
            runpy.run_path(str(script), run_name="__main__")
        finally:
            sys.argv = previous


def invoke(project: str, name: str, operation: str, token: str = "") -> dict:
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

    script = owner_script(project)
    owner_args = _owner_arguments(script, project, name, "apply-locked" if operation == "apply" else operation, token)
    args = (
        [sys.executable, "-B", "-m", "agent_worktrees.repos_knowledge_configuration",
         project, name, token, str(script)]
        if operation == "apply" else
        [sys.executable, "-B", *owner_args]
    )
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "COPILOT_PLUGIN_ROOT"):
        environment.pop(key, None)
    budget = OwnedExecutionBudget.from_timeout(
        26 if operation == "apply" else 18, cleanup_reserve=2,
    )
    try:
        support = run_caller_owned(
            [sys.executable, "-B", str(script), "--capabilities", "--json"],
            timeout=8, max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
            env=environment, budget=budget,
        )
        capabilities = json.loads(support.stdout)
        if (
            support.returncode or not isinstance(capabilities, dict)
            or type(capabilities.get("version")) is not int or capabilities["version"] != 1
            or type(capabilities.get("existing_binding_configuration")) is not int
            or capabilities["existing_binding_configuration"] != 2
        ):
            raise ConfigurationError(
                "binding-owner-unsupported", "The selected binding owner needs its Configuration capability update.",
            )
        result = run_caller_owned(
            args, timeout=26 if operation == "apply" else 18,
            max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
            env=environment, budget=budget,
        )
        value = json.loads(result.stdout)
    except OwnedCleanupError:
        raise
    except ConfigurationError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ConfigurationError(
            "binding-owner-failed", "Binding observation failed; inspect current readiness before retrying.",
        ) from exc
    if not isinstance(value, dict):
        raise ConfigurationError("binding-owner-failed", "Binding owner returned an invalid result.")
    if result.returncode and value.get("pointer_committed") is not True:
        raise ConfigurationError("binding-owner-refused", "Binding preconditions changed or are unavailable; preview again.")
    if result.returncode:
        return {**value, "status": "incomplete"}
    if operation == "preview":
        from .repos_configuration import target

        if (
            type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("target"), dict)
            or value["target"].get("resource") != f"{project}/{name}"
            or value.get("operation") != "set"
        ):
            raise ConfigurationError("binding-owner-failed", "Binding preview returned a different target.")
        value["target"] = target(project, name)
    return value


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("The binding worker requires its native transaction arguments.")
    _apply_worker(*sys.argv[1:])
