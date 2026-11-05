"""Run an opted-in project's update controller under the suite update driver."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import config as cfg
from . import output, update_options


def dispatch_update(args, auth, manager, standalone) -> int:
    if getattr(args, "auth_report", False):
        return auth(args)
    controlled = run_project_update(args)
    if controlled is not None:
        return controlled
    handed_off = manager(args)
    return handed_off if handed_off is not None else standalone()


def run_project_update(args) -> int | None:
    """Return None only when this request does not select a project controller."""
    if (
        getattr(args, "no_project_controller", False)
        or getattr(args, "phase", None)
        or getattr(args, "target", None) is not None
        or getattr(args, "runtime_sources", None) is not None
        or not cfg.active_project()
    ):
        return None
    try:
        repo = cfg.load_config().default_repo
        script = getattr(repo, "update_controller", {}).get(cfg.detect_platform())
    except (OSError, KeyError, ValueError) as error:
        output.err(f"Cannot resolve the project update controller: {error}")
        return 1
    if not script:
        return None
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
        return subprocess.run(
            [sys.executable, "-I", "-B", str(path), *flags], cwd=root,
            env={**os.environ, **custody}, check=False, **no_window_kwargs(),
        ).returncode
    except (OSError, ValueError) as error:
        output.err(f"Project update controller failed: {error}")
        return 1
