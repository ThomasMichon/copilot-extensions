"""Run an opted-in project's update controller under the suite update driver."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from copy import copy
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import config as cfg
from . import output, update_options

PROBE_TIMEOUT = 30
DELEGATION_SCHEMA = "copilot-extensions.project-update-delegation"
DELEGATION_VERSION = 1
CONTROLLER_CAPABILITIES_SCHEMA = "copilot-extensions.project-update-controller-capabilities"
_CONTROLLER_OWNS_MANAGER = object()


def _project_integration_capability(prefix: Path) -> bool:
    try:
        from . import manager_launch_cli, manager_recovery, picker_profiles_cli, update_cli, update_integration

        return (
            all(Path(module.__file__).resolve().is_relative_to(prefix)
                for module in (
                    update_cli, update_integration, manager_launch_cli, manager_recovery, picker_profiles_cli,
                ))
            and getattr(update_cli, "PROJECT_INTEGRATION_CAPABILITY", None)
            == getattr(update_integration, "PROJECT_INTEGRATION_CAPABILITY", None)
            == "project-controller-integration/v1"
            and getattr(picker_profiles_cli, "CONTROLLER_PROFILE_REFRESH_CAPABILITY", None)
            == "controller-profile-refresh/v1"
            and getattr(update_cli, "run_selected", None) is getattr(update_integration, "run_selected", None)
            and callable(getattr(update_integration, "run_selected", None))
            and callable(getattr(manager_recovery, "adopted_manager", None))
            and callable(getattr(manager_recovery.Adoption, "verify", None))
            and callable(getattr(manager_recovery, "controller_selection", None))
            and callable(getattr(manager_recovery.ControllerSelection, "verify", None))
            and callable(getattr(manager_launch_cli, "_worktree_manager_root", None))
            and callable(getattr(picker_profiles_cli, "_refresh_terminal_profiles", None))
        )
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return False


def _targeted_payload_install_capability(prefix: Path) -> bool:
    try:
        import plugin_activation
        from . import update_acquisition, update_cli, update_driver_cli, update_options, update_runtime

        if any(
            not Path(module.__file__).resolve().is_relative_to(prefix)
            for module in (
                plugin_activation, update_acquisition, update_cli, update_driver_cli,
                update_options, update_runtime,
            )
        ):
            return False
        return (
            callable(getattr(update_acquisition, "acquire", None))
            and callable(getattr(update_acquisition, "_run_install", None))
            and callable(getattr(update_cli, "_update_in_plugin_steps", None))
            and callable(getattr(update_driver_cli, "cmd_update_request", None))
            and callable(getattr(update_options, "select_plugin_targets", None))
            and callable(getattr(update_runtime, "_update_registered_plugins", None))
            and callable(getattr(plugin_activation, "activation_custody", None))
            and callable(getattr(plugin_activation.ActivationCustody, "preserve", None))
            and all(callable(getattr(plugin_activation.ActivationPreservation, name, None)) for name in (
                "confirm_quiescent", "confirm_posix_quiescent", "confirm_not_launched",
            ))
        )
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return False


def _runtime_backend_intent_capability(prefix: Path) -> bool:
    try:
        from . import update_driver_cli

        return (
            all(Path(module.__file__).resolve().is_relative_to(prefix)
                for module in (update_options, update_driver_cli))
            and update_driver_cli.RUNTIME_BACKEND_INTENT_CAPABILITY
            == update_options.RUNTIME_BACKEND_INTENT_CAPABILITY
            == "project-runtime-backend-intent/v1"
        )
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return False


def delegation_capability():
    """Advertise only the selected runtime's usable driver-backed seam."""
    try:
        import update_driver
        from agent_procutil._daemon_instance_identity import runtime_slot_paths

        prefix = Path(sys.prefix).resolve()
        slot = runtime_slot_paths(prefix)
        if (
            slot is None or Path(slot[2]).resolve() != cfg.install_dir().resolve()
            or not Path(__file__).resolve().is_relative_to(prefix)
            or not Path(update_driver.__file__).resolve().is_relative_to(prefix)
        ):
            return None
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None
    if (
        getattr(update_driver, "UPDATE_AUTHORITY_CAPABILITY", None) != "suite-update-authority/v1"
        or any(not callable(getattr(update_driver, name, None)) for name in (
            "current_authority", "borrowed_custody", "participant_environment", "status", "runtime_command",
        ))
    ):
        return None
    return {
        "schema": DELEGATION_SCHEMA, "version": DELEGATION_VERSION,
        "engine_fallback": True, "isolated_runtime_phase": True,
        "targeted_payload_install": _targeted_payload_install_capability(prefix),
        "project_integration": _project_integration_capability(prefix),
        "runtime_backend_intent": _runtime_backend_intent_capability(prefix),
    }


def dispatch_update(args, auth, manager, standalone) -> int:
    if getattr(args, "auth_report", False):
        return auth(args)
    request = getattr(args, "_controller_request", None)
    selected = getattr(args, "_controller_authority", None)
    requested = getattr(request, "authority", "standalone")
    if selected is not None and selected.configured and selected.mode == "standalone":
        if requested != "standalone":
            output.err("Project update authority changed before execution.")
            return 1
        args = copy(args)
        args.no_project_controller = True
        controlled = None
    elif requested == "project":
        if (
            selected is None or not selected.configured or selected.mode != "project"
            or selected.project != request.project or cfg.active_project() != request.project
        ):
            output.err("The selected project update authority is unavailable.")
            return 1
        if (
            getattr(args, "phase", None) or getattr(args, "target", None) is not None
            or getattr(args, "no_project_controller", False)
        ):
            if any(
                flag in {"--phase", "--target", "--no-project-controller"}
                or flag.startswith(("--phase=", "--target="))
                for flag in request.argv
            ):
                output.err("A partial request cannot own the project update.")
                return 1
            if getattr(args, "phase", None) or getattr(args, "target", None) is not None:
                if getattr(args, "phase", None) == "integration":
                    missing = object()
                    previous = getattr(args, "_controller_owns_manager", missing)
                    args._controller_owns_manager = _CONTROLLER_OWNS_MANAGER
                    try:
                        return standalone()
                    finally:
                        if previous is missing:
                            del args._controller_owns_manager
                        else:
                            args._controller_owns_manager = previous
                return standalone()
            controlled = None
        else:
            controlled = run_project_update(args)
            if controlled is None:
                output.err("The selected project update controller is unavailable; no update ran.")
                return 1
    elif selected is not None and selected.configured and selected.mode == "project":
        output.err("Standalone update is disabled by the selected project authority.")
        return 1
    else:
        controlled = run_project_update(args)
    if controlled is not None:
        return controlled
    if getattr(args, "runtime_backend", None):
        output.err("--runtime-backend requires a selected project update controller.")
        return 1
    handed_off = manager(args)
    return handed_off if handed_off is not None else standalone()


def configured_script(repo) -> str:
    controllers = getattr(repo, "update_controller", {})
    platform = cfg.detect_platform()
    return controllers.get(platform) or (controllers.get("linux", "") if platform == "wsl" else "")


def _controller_path(repo, script: str) -> tuple[Path, Path]:
    root = Path(repo.anchor).resolve(strict=True)
    relative = Path(script)
    if relative.is_absolute() or relative.suffix.lower() != ".py":
        raise ValueError("the controller must be a repo-relative Python script")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("the controller is outside the project or is not a file")
    return root, path


def _controller_supports_runtime_backend(path: Path) -> bool:
    from .installation_probe import strict_json_object

    descriptor = path.with_name(path.name + ".capabilities.json")
    try:
        info = descriptor.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or info.st_size > 65536
        ):
            return False
        with descriptor.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return False
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_json_object)
        capabilities = record.get("capabilities") if isinstance(record, dict) else None
        return (
            isinstance(record, dict) and record.get("schema") == CONTROLLER_CAPABILITIES_SCHEMA
            and type(record.get("version")) is int and record["version"] == 1
            and isinstance(capabilities, list) and len(capabilities) <= 128
            and all(isinstance(value, str) and value for value in capabilities)
            and len(set(capabilities)) == len(capabilities)
            and update_options.RUNTIME_BACKEND_INTENT_CAPABILITY in capabilities
        )
    except (OSError, TypeError, ValueError):
        return False


def supports_runtime_backend(project: str | None) -> bool:
    if not project:
        return False
    try:
        repo = cfg.load_config(project=project).default_repo
        script = configured_script(repo)
        return bool(script) and _controller_supports_runtime_backend(_controller_path(repo, script)[1])
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _selected_project(args, *, project=None):
    if (
        getattr(args, "no_project_controller", False)
        or getattr(args, "phase", None)
        or getattr(args, "target", None) is not None
    ):
        return None
    return cfg.active_project() if project is None else project


def configured_for_update(args):
    if not _selected_project(args):
        return None
    repo = cfg.load_config().default_repo
    script = configured_script(repo)
    return (repo, script) if script else None


def _probe_command(project: str) -> list[str]:
    return [
        sys.executable, "-I", "-B", "-m", "agent_worktrees",
        "--project", project, "get", "update-controller",
    ]


def probe_for_submission(
    args, *, cwd: Path, timeout: float = PROBE_TIMEOUT, project: str | None = None,
) -> bool | None:
    project = _selected_project(args, project=project)
    if not project:
        return None
    from agent_procutil.owned_process import OutputLimitExceeded, run_caller_owned

    environment = dict(os.environ)
    for key in ("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", "UPDATE_DRIVER_WITNESS"):
        environment.pop(key, None)
    command = _probe_command(project)
    try:
        result = run_caller_owned(
            command, cwd=str(cwd), env=environment, timeout=timeout,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("project update controller probe timed out; no update ran") from error
    except OutputLimitExceeded as error:
        raise ValueError("project update controller probe exceeded its output limit") from error
    if result.returncode:
        raise ValueError("project update controller probe failed; no update ran")
    return bool(result.stdout.strip())


def run_project_update(args) -> int | None:
    """Return None only when this request does not select a project controller."""
    try:
        selected = configured_for_update(args)
    except (OSError, KeyError, RuntimeError, ValueError) as error:
        output.err(f"Cannot resolve the project update controller: {error}")
        return 1
    if selected is None:
        return None
    repo, script = selected
    request = getattr(args, "_controller_request", None)
    if request is not None:
        try:
            requested_backends = update_options.request_runtime_backends(request.argv)
        except (AttributeError, ValueError):
            output.err("The submitted runtime backend selection is invalid.")
            return 1
        if requested_backends != tuple(getattr(args, "runtime_backend", None) or ()):
            output.err("The runtime backend selection changed after submission.")
            return 1
    if getattr(args, "auto", False) and request is not None:
        from dataclasses import replace
        from update_driver.core import automatic_update_refusal
        from .update_driver_cli import _request_context

        try:
            context = _request_context(controller=True, cwd=Path(request.cwd))
            refusal = automatic_update_refusal(replace(request, context=context))
        except (OSError, ValueError) as error:
            output.err(f"Cannot resolve automatic update policy: {error}")
            return 1
        if refusal:
            output.err(refusal)
            return 3
    try:
        root, path = _controller_path(repo, script)
        if getattr(args, "runtime_backend", None) and not _controller_supports_runtime_backend(path):
            raise ValueError("the selected project controller does not advertise runtime backend intent")
        from update_driver import participant_environment

        custody = participant_environment()
        if not custody:
            raise ValueError("the update driver did not provide controller custody")
        flags = (["--no-manager"] if getattr(args, "no_manager", False) else [])
        flags.extend(update_options.update_flags(args))
        options = no_window_kwargs()
        if os.name == "nt":
            options.update(stdout=sys.stdout, stderr=sys.stderr)
        return subprocess.run(
            [sys.executable, "-I", "-B", str(path), *flags], cwd=root,
            env={**os.environ, **custody}, check=False, **options,
        ).returncode
    except (OSError, ValueError) as error:
        output.err(f"Project update controller failed: {error}")
        return 1
