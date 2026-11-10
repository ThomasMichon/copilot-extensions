"""Native Configuration adapter to the selected knowledge-binding payload."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import sysconfig

from agent_procutil.owned_budget import OwnedExecutionBudget
from dropin_registry import ScanAuthority
from plugin_activation import resolve_active_plugins

from . import config as cfg
from .repos_configuration_sources import ConfigurationError, resource_name


def binding_names_supported(project: str, name: str) -> bool:
    """Binding v2 identifiers narrow the generic Configuration name grammar."""
    for value in (project, name):
        try:
            canonical = resource_name(value)
        except ConfigurationError:
            return False
        if canonical != value or not canonical[0].isalnum():
            return False
    return True


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


def _native_read_runner(project: str):
    """Keep fresh owner reads in the native worker; never dispatch write/auth verbs."""
    entry = Path(sysconfig.get_path("scripts")) / (
        "agent-worktrees.exe" if sys.platform == "win32" else "agent-worktrees"
    )

    def run(command: str, args: list[str], *, cwd="", noninteractive=False):
        if Path(command).resolve() != entry.resolve():
            return None
        if args == ["repos", "list", "--json"]:
            from .repos_cli import cmd_repos_dispatch

            dispatch, arguments = cmd_repos_dispatch, ["list", "--json"]
        elif args in (
            ["-p", project, "config-root", "--json"],
            ["-p", project, "state-root", "--json"],
        ):
            from . import context_cli

            dispatch = (
                context_cli.cmd_config_root_dispatch if args[2] == "config-root"
                else context_cli.cmd_state_root_dispatch
            )
            arguments = ["--json"]
        else:
            return None
        from .output import capture_json_output

        previous, directory = cfg.active_project(), Path.cwd()
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            cfg.set_active_project(project)
            if cwd:
                os.chdir(cwd)
            with capture_json_output() as native_json, redirect_stdout(stdout), redirect_stderr(stderr):
                result = dispatch(arguments)
        finally:
            os.chdir(directory)
            cfg.set_active_project(previous)
        return subprocess.CompletedProcess(
            [command, *args], result, native_json.getvalue() or stdout.getvalue(),
            stderr.getvalue() + (stdout.getvalue() if native_json.getvalue() else ""),
        )

    return run


def _run_selected_payload(
    project: str, name: str, operation: str, token: str, expected_script: str,
    portable_resolution: dict | None = None, preference_selection: dict | None = None,
) -> None:
    script = owner_script(project)
    if str(script) != expected_script:
        raise ConfigurationError("binding-owner-changed", "The binding owner changed; preview again.")

    def revalidate() -> None:
        if preference_selection is not None:
            from .repos_knowledge_default import revalidate

            revalidate(project, preference_selection)
        if portable_resolution is not None:
            from .repos_knowledge_preferences import resolve

            current = resolve(
                project, portable_resolution["harness"]["repository_id"],
                portable_resolution["knowledge"]["repository_id"],
            )
            if current != portable_resolution:
                raise ConfigurationError("preview-conflict", "Portable binding scope changed; preview again.")
    previous = sys.argv
    try:
        sys.argv = _owner_arguments(script, project, name, operation, token)
        runpy.run_path(
            str(script), run_name="__main__",
            init_globals={
                "_NATIVE_BINDING_READ_RUNNER": _native_read_runner(project),
                "_NATIVE_BINDING_PRECONDITION": revalidate,
            },
        )
    finally:
        sys.argv = previous


def _apply_worker(
    project: str, name: str, token: str, expected_script: str,
    portable_resolution: dict | None = None, preference_selection: dict | None = None,
) -> None:
    from . import repos
    from .repos_registry_transaction import registry_lock

    with registry_lock(repos._repos_yaml_path()):
        _run_selected_payload(
            project, name, "apply-locked", token, expected_script, portable_resolution, preference_selection,
        )


def _preview_worker(project: str, name: str, expected_script: str) -> None:
    _run_selected_payload(project, name, "preview", "", expected_script)


def invoke(
    project: str, name: str, operation: str, token: str = "", *, portable_resolution: dict | None = None,
    preference_selection: dict | None = None,
    require_preference_freshness: bool = False,
    budget: OwnedExecutionBudget | None = None,
) -> dict:
    if not binding_names_supported(project, name):
        raise ConfigurationError(
            "binding-name-unsupported",
            "Binding requires exact Configuration names beginning with an ASCII letter or digit.",
        )
    from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

    script = owner_script(project)
    _owner_arguments(script, project, name, "apply-locked" if operation == "apply" else operation, token)
    args = [
        sys.executable, "-B", "-m", "agent_worktrees.repos_knowledge_configuration",
        project, name, operation, token, str(script),
    ]
    if portable_resolution is not None:
        args.append(json.dumps(portable_resolution, sort_keys=True))
    if preference_selection is not None:
        if portable_resolution is None:
            raise ConfigurationError("invalid-request", "Accepted defaults require native portable resolution.")
        args.append(json.dumps(preference_selection, sort_keys=True))
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "COPILOT_PLUGIN_ROOT"):
        environment.pop(key, None)
    budget = budget or OwnedExecutionBudget.from_timeout(
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
            or portable_resolution is not None and (
                type(capabilities.get("portable_binding_precondition")) is not int
                or capabilities["portable_binding_precondition"] != 1
            )
            or (require_preference_freshness or preference_selection is not None) and (
                type(capabilities.get("binding_precondition_freshness")) is not int
                or capabilities["binding_precondition_freshness"] != 1
            )
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


def _worker_main() -> None:
    if len(sys.argv) == 5:
        _apply_worker(*sys.argv[1:])
    elif len(sys.argv) == 6 and sys.argv[3] == "apply":
        _apply_worker(sys.argv[1], sys.argv[2], sys.argv[4], sys.argv[5])
    elif len(sys.argv) == 7 and sys.argv[3] == "apply":
        _apply_worker(sys.argv[1], sys.argv[2], sys.argv[4], sys.argv[5], json.loads(sys.argv[6]))
    elif len(sys.argv) == 8 and sys.argv[3] == "apply":
        _apply_worker(
            sys.argv[1], sys.argv[2], sys.argv[4], sys.argv[5], json.loads(sys.argv[6]), json.loads(sys.argv[7]),
        )
    elif len(sys.argv) == 6 and sys.argv[3] == "preview" and not sys.argv[4]:
        _preview_worker(sys.argv[1], sys.argv[2], sys.argv[5])
    else:
        raise SystemExit("The binding worker requires its native transaction arguments.")


if __name__ == "__main__":
    try:
        _worker_main()
    except ConfigurationError as error:
        print(json.dumps({"status": "refused", "code": error.code, "pointer_committed": False}))
        raise SystemExit(1)
