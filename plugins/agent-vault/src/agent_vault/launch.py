"""Detached launch of the vault service daemon.

The CLI's ``start`` path and the service's own Windows self-daemonize path
start the same resident daemon, so both go through :func:`spawn_service`.
"""

from __future__ import annotations

from agent_procutil.daemon_instances import DaemonInstanceSpec

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_procutil import spawn_daemon, windowless_python, windowless_python_env

from . import config
from .cache import CACHE_DIR_ENV

SERVICE_STDIO_LOG = "service-stdio.log"
_SERVICE_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "import runpy;runpy.run_module('agent_vault.service',run_name='__main__',alter_sys=True)"
)
# Every filesystem override the service reads (the client-side endpoint is a
# dial spec, not a path).
_PATH_OVERRIDES = (
    config.CONFIG_ENV, config.RUN_DIR_ENV, config.PID_ENV, config.LOG_ENV, config.SOCKET_ENV,
    CACHE_DIR_ENV, "KPDB", "AGENT_VAULT_KEK_DIR", "AGENT_VAULT_WINDOWS_RUN_DIR",
    "AGENT_VAULT_CORE_RUN_DIR",
)


def service_root() -> Path:
    """Create the lexical runtime selection without hiding redirected custody."""
    root = Path(os.path.abspath(config.home_dir()))
    if root.resolve() != root:
        raise OSError("vault runtime root is redirected")
    root.mkdir(parents=True, exist_ok=True)
    return root


def spawn_service(args: Sequence[str]) -> subprocess.Popen:
    """Start the trusted ``agent_vault.service`` package as a detached daemon.

    Launched through ``agent_procutil.spawn_daemon``: it runs from the vault
    runtime root (never the caller's cwd, which may be a worktree or the plugin
    payload), breaks away from a kill-on-close Job around the caller, and
    appends its stdout/stderr to ``<root>/service-stdio.log`` so a startup crash
    leaves a traceback (the service keeps its own rotating log too). A Job that
    forbids breakaway still starts the service inside that Job, where it always
    ran before, as a logged degradation. The child imports from this package's
    source root, including vendored layouts, rather than selecting its service
    implementation from the runtime cwd or the caller's ambient module path.
    """
    python = sys.executable
    root = service_root()
    overrides = dict(windowless_python_env(python))
    # The child runs from ``root``: a relative path override would resolve a
    # second time there, apart from the clients' copy. Pass each one resolved
    # against the caller's cwd; unset ones stay unset (the home override's
    # presence selects the cache location).
    if os.environ.get(config.HOME_ENV):
        overrides[config.HOME_ENV] = str(root)
    for name in _PATH_OVERRIDES:
        value = os.environ.get(name)
        if value:
            # Resolved exactly as its readers resolve it: only ``KPDB`` is
            # expanded (``~``, ``$VAR``); every other reader takes the raw path.
            overrides[name] = (
                config._expand_kpdb(value) if name == "KPDB" else os.path.abspath(value)
            )
    return spawn_daemon(
        [
            windowless_python(python), "-c", _SERVICE_BOOTSTRAP,
            str(Path(__file__).resolve().parent.parent), *args,
        ],
        cwd=root,
        instance=DaemonInstanceSpec("agent-vault", root, Path(__file__).resolve()),
        log_path=root / SERVICE_STDIO_LOG,
        env_overrides=overrides,
        contained_fallback=True,
        display="agent_vault.service",
    )
