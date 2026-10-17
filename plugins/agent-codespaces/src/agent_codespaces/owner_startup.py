"""Bounded readiness observation for an on-demand Connection Owner child."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from subprocess import Popen

log = logging.getLogger("agent-codespaces")

DEFAULT_OWNER_STARTUP_TIMEOUT = 30.0


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
