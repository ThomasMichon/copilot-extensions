"""Windowless monitor spawning with optional owning-lifecycle process custody."""

from __future__ import annotations

import logging
import subprocess


def _spawn_detached(
    argv: list[str], *, spawned_handles: list[subprocess.Popen] | None = None,
) -> bool:
    from . import status_monitor_runtime as runtime

    override = runtime._core_helper("_spawn_detached", _spawn_detached)
    if override is not _spawn_detached:
        if spawned_handles is not None:
            return override(argv, spawned_handles=spawned_handles)
        return override(argv)
    argv = list(argv)
    from .runtime_monitor_launch import retained_monitor_argv

    env = runtime._daemon_environment()
    try:
        argv = retained_monitor_argv(argv)
        from .status_monitor_cutover import spawn_monitor

        argv[0], extra_env = runtime.direct_monitor_python(argv[0])
        env.update(extra_env)
        handle = spawn_monitor(argv, env, console_children=True)
        if spawned_handles is not None:
            spawned_handles.append(handle)
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning("Cannot spawn status-monitor: %s", exc)
        return False
