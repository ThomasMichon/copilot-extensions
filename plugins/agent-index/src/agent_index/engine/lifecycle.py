"""Engine lifecycle - ensure on-demand embedding engines are running.

The code embedding engine (port 8421) runs as a socket-activated systemd
service. Indexing requires it, so the indexer ensures it is reachable before
the embed phase -- the reachability probe itself connects to the socket, which
triggers on-demand activation; the explicit start path remains as a safety net
for non-socket-activated deployments. Since #1495 the *search* path embeds in
process on the CPU and never touches the engines, so the engines are driven by
indexing alone and idle out (AGENT_INDEX_ENGINE_IDLE_TIMEOUT) after each reindex.

Without this, a reindex whose code engine is down silently skips code
embedding -- code chunks are content-stored but never vectorised, so they
never appear in search (see #775). This module makes the indexer start the
engine and fail loudly if it cannot, instead of stalling silently.

Systemd scope (user vs system) follows ``AGENT_INDEX_SYSTEMD_SCOPE`` (default
``user``); the system units set it to ``system`` so engine units are managed
with the system ``systemctl`` rather than ``systemctl --user``.

All systemd interaction is best-effort: if ``systemctl`` is unavailable
(tests, non-systemd hosts) the helpers fall back to a plain reachability
check, so indexing still works when an engine is already up.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import TYPE_CHECKING

from agent_index.engine.client import EngineUnavailableError

if TYPE_CHECKING:
    from agent_index.index_config import ModelProfile
    from agent_index.engine.client import EngineClient

log = logging.getLogger(__name__)

# Starting the uvicorn process is quick; the model loads lazily on the first
# embed call, so we only wait for the HTTP server to answer /health here.
_START_TIMEOUT = 60.0
_POLL_INTERVAL = 1.0


def _systemctl(*args: str, timeout: float = 30.0) -> bool:
    """Run a ``systemctl`` command in the configured scope. Returns True on success.

    Scope is read from ``AGENT_INDEX_SYSTEMD_SCOPE`` (``user`` by default; ``system``
    on the system-scoped deployment). User scope passes ``--user``.
    """
    exe = shutil.which("systemctl")
    if not exe:
        return False
    scope = os.environ.get("AGENT_INDEX_SYSTEMD_SCOPE", "user").strip().lower()
    cmd = [exe, *args] if scope == "system" else [exe, "--user", *args]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("systemctl (scope=%s) %s failed: %s", scope, " ".join(args), exc)
        return False


def _reachable(client: EngineClient) -> bool:
    """True if the engine HTTP server answers /health (model load optional)."""
    return client.health().get("status") != "unreachable"


def ensure_engine(profile: ModelProfile, client: EngineClient) -> bool:
    """Ensure *profile*'s engine HTTP server is reachable.

    Returns:
        True if this call started the engine (caller owns shutdown),
        False if it was already running.

    Raises:
        EngineUnavailableError: if the engine cannot be made reachable.
    """
    if _reachable(client):
        return False

    unit = profile.systemd_unit
    if not unit:
        raise EngineUnavailableError(
            f"Engine for model '{profile.model_id}' unreachable at "
            f"{profile.engine_url} and no systemd unit is configured to "
            f"start it"
        )

    log.info("Engine '%s' not running -- starting %s", profile.model_id, unit)
    if not _systemctl("start", unit):
        raise EngineUnavailableError(
            f"Failed to start engine unit '{unit}' for model "
            f"'{profile.model_id}' (systemctl unavailable or unit missing); "
            f"engine unreachable at {profile.engine_url}"
        )

    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if _reachable(client):
            log.info("Engine '%s' is up at %s", profile.model_id, profile.engine_url)
            return True
        time.sleep(_POLL_INTERVAL)

    raise EngineUnavailableError(
        f"Engine '{profile.model_id}' did not become reachable within "
        f"{_START_TIMEOUT:.0f}s after starting unit '{unit}'"
    )


def stop_engine(profile: ModelProfile) -> None:
    """Best-effort stop of *profile*'s engine systemd unit (on-demand cleanup)."""
    unit = profile.systemd_unit
    if not unit:
        return
    log.info("Stopping on-demand engine '%s' (%s)", profile.model_id, unit)
    _systemctl("stop", unit)
