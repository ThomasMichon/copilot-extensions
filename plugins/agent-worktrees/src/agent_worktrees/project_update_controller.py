"""Run an opted-in project's update controller under the suite update driver."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import config as cfg
from . import output, update_options

PROBE_TIMEOUT = 30


def dispatch_update(args, auth, manager, standalone) -> int:
    if getattr(args, "auth_report", False):
        return auth(args)
    controlled = run_project_update(args)
    if controlled is not None:
        return controlled
    handed_off = manager(args)
    return handed_off if handed_off is not None else standalone()


def configured_script(repo) -> str:
    controllers = getattr(repo, "update_controller", {})
    platform = cfg.detect_platform()
    return controllers.get(platform) or (controllers.get("linux", "") if platform == "wsl" else "")


def _selected_project(args):
    if (
        getattr(args, "no_project_controller", False)
        or getattr(args, "phase", None)
        or getattr(args, "target", None) is not None
    ):
        return None
    return cfg.active_project()


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


def probe_for_submission(args, *, cwd: Path, timeout: float = PROBE_TIMEOUT) -> bool | None:
    project = _selected_project(args)
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
