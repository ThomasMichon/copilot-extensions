"""Native fixture helpers independent of an installed Manager application."""
from __future__ import annotations

import os
import subprocess

from agent_procutil import no_window_kwargs


def _environment(directory):
    home = directory / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"] = str(
        home / ".agent-worktrees" / "control-plane-providers.d"
    )
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "WORKTREE_MANAGER_ROOT"):
        env.pop(name, None)
    return env


def _prepare_command(argv, env, cwd):
    # The native group and production engine retain their own deadlines.
    return subprocess.run(
        list(map(str, argv)), env=env, cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", **no_window_kwargs(),
    )
