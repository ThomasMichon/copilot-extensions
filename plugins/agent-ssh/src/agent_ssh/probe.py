"""Shared SSH reachability probe used by ``verify`` and ``refresh-mesh``."""

from __future__ import annotations

import subprocess

from agent_procutil import no_window_flags


def probe_alias(name: str, timeout: int) -> bool:
    """Return ``True`` if ``ssh <name> exit 0`` succeeds within *timeout* seconds.

    A shell-agnostic no-op is required: ``true`` is a POSIX shell builtin that
    does not exist on a pwsh remote shell (the DefaultShell on every
    Windows/dtssh host in this mesh), so it made every such host register as a
    false-negative "unreachable" even though the SSH session itself
    authenticated and ran fine (copilot-extensions#2199). ``exit 0`` is valid,
    no-op syntax under both pwsh and POSIX shells (bash/sh).
    """
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={timeout}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            name,
            "exit 0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=no_window_flags(),
        check=False,
    )
    return proc.returncode == 0
