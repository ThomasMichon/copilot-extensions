"""Shared SSH reachability probe used by ``verify`` and ``refresh-mesh``."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

from agent_procutil import no_window_flags

IS_WINDOWS = sys.platform == "win32"


async def _probe_windows(
    name: str, timeout: int, *, config_file: str | None = None,
) -> bool:
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("Windows SSH probe timeout must be a positive integer")
    from ssh_manager import proxy as ssh_proxy
    from ssh_manager.config_sources import SSHProfileSource
    from ssh_manager.process import terminate_ssh_process_tree

    support = getattr(ssh_proxy, "WINDOWS_OWNED_PROBE_VERSION", None)
    if type(support) is not int or support != 1:
        raise RuntimeError("Owned Windows probe support is unavailable; update the SSH runtime")
    executable = shutil.which("ssh")
    if executable is None:
        raise FileNotFoundError("OpenSSH client executable `ssh` is not available")
    source = SSHProfileSource(
        name, config_file=config_file, resolve_timeout=timeout, ssh_executable=executable,
    )
    config = await asyncio.to_thread(source.get_ssh_config)
    args = [executable]
    if config_file is not None:
        args.extend(["-F", config_file])
    args.extend([
        "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}",
        "-o", "StrictHostKeyChecking=accept-new", name, "exit 0",
    ])
    process = await ssh_proxy.create_ssh_subprocess(
        *args,
        config=config, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout) == 0
    except asyncio.TimeoutError:
        return False
    finally:
        await terminate_ssh_process_tree(process)


def probe_alias(name: str, timeout: int) -> bool:
    """Return ``True`` if ``ssh <name> exit 0`` succeeds within *timeout* seconds.

    A shell-agnostic no-op is required: ``true`` is a POSIX shell builtin that
    does not exist on a pwsh remote shell (the DefaultShell on every
    Windows/dtssh host in this mesh), so it made every such host register as a
    false-negative "unreachable" even though the SSH session itself
    authenticated and ran fine (copilot-extensions#2199). ``exit 0`` is valid,
    no-op syntax under both pwsh and POSIX shells (bash/sh).
    """
    if IS_WINDOWS:
        return asyncio.run(_probe_windows(name, timeout))
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
