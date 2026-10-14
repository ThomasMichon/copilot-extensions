"""Detached launch of the vault service daemon.

The CLI's ``start`` path and the service's own Windows self-daemonize path
start the same resident daemon, so both go through :func:`spawn_service`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_procutil import spawn_daemon, windowless_python, windowless_python_env

from . import config

SERVICE_STDIO_LOG = "service-stdio.log"


def service_root() -> Path:
    """The vault runtime root a detached service runs from, created if absent."""
    root = config.home_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def spawn_service(args: Sequence[str]) -> subprocess.Popen:
    """Start ``python -m agent_vault.service *args`` as a detached daemon.

    Launched through ``agent_procutil.spawn_daemon``: it runs from the vault
    runtime root (never the caller's cwd, which may be a worktree or the plugin
    payload), breaks away from a kill-on-close Job around the caller, and
    appends its stdout/stderr to ``<root>/service-stdio.log`` so a startup crash
    leaves a traceback (the service keeps its own rotating log too). A Job that
    forbids breakaway still starts the service inside that Job, where it always
    ran before, as a logged degradation.
    """
    python = sys.executable
    root = service_root()
    overrides = dict(windowless_python_env(python))
    if os.environ.get(config.HOME_ENV):
        # The child runs from ``root``: a relative override would resolve a
        # second time there. Left unset otherwise (its presence selects the
        # cache location).
        overrides[config.HOME_ENV] = str(root)
    return spawn_daemon(
        [windowless_python(python), "-m", "agent_vault.service", *args],
        cwd=root,
        log_path=root / SERVICE_STDIO_LOG,
        env_overrides=overrides,
        contained_fallback=True,
        display="agent_vault.service",
    )
