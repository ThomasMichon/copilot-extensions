"""Run an opted-in project's update controller under the suite update driver."""

from __future__ import annotations

import os
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
    return {"schema": DELEGATION_SCHEMA, "version": DELEGATION_VERSION, "engine_fallback": True}


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
    handed_off = manager(args)
    return handed_off if handed_off is not None else standalone()


def configured_script(repo) -> str:
    controllers = getattr(repo, "update_controller", {})
    platform = cfg.detect_platform()
    return controllers.get(platform) or (controllers.get("linux", "") if platform == "wsl" else "")


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
        root = Path(repo.anchor).resolve(strict=True)
        relative = Path(script)
        if relative.is_absolute() or relative.suffix.lower() != ".py":
            raise ValueError("the controller must be a repo-relative Python script")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("the controller is outside the project or is not a file")
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
