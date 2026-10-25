"""Bounded readiness observation for an on-demand Connection Owner child."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from subprocess import Popen

log = logging.getLogger("agent-codespaces")

DEFAULT_OWNER_STARTUP_TIMEOUT = 30.0


def spawn_owner_process(root: Path, *, source: str) -> tuple[Popen[bytes], Path]:
    """Launch the owner with its existing durable cwd, console and log policy."""
    from agent_procutil import spawn_daemon
    from agent_procutil.daemon_instances import DaemonInstanceSpec

    root = Path(os.path.abspath(root))
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "logs" / "owner-stdio.log"
    child = spawn_daemon(
        [sys.executable, "-m", "agent_codespaces", "owner"],
        instance=DaemonInstanceSpec("agent-codespaces-owner", root, Path(source).resolve()),
        cwd=os.path.expanduser("~"),
        log_path=log_path,
        console_children=True,
        display="agent_codespaces owner",
        env_overrides={"AGENT_CODESPACES_HOME": str(root)},
    )
    return child, log_path


def wait_for_owner_startup(
    child: Popen[bytes], is_live: Callable[[], bool], *,
    timeout: float, poll: float, log_path: Path,
) -> bool:
    """Observe the identity-checked beacon without changing process ownership."""
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if is_live():
            return True
        returncode = child.poll()
        if returncode is not None:
            # A losing singleton contender can exit while the winner goes live.
            if is_live():
                return True
            log.warning(
                "Connection Owner exited before readiness (exit code %s); "
                "see %s", returncode, log_path,
            )
            return False
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll, remaining))
    if is_live():
        return True
    log.warning(
        "Connection Owner did not become live within %.1f seconds; "
        "startup may still be in progress; see %s", timeout, log_path,
    )
    return False
