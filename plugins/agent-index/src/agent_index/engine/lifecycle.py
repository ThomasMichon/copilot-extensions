"""Engine lifecycle -- ensure on-demand embedding engines are running.

Indexing requires an embedding engine; the *search* path embeds in process on
the CPU (#1495) and never touches the engines, so the engines are driven by
indexing alone and idle out (``AGENT_INDEX_ENGINE_IDLE_TIMEOUT``) after each
reindex. Without this, a reindex whose engine is down silently skips code
embedding -- chunks are content-stored but never vectorised, so they never
appear in search (#775). This module makes the indexer bring the engine up and
fail loudly if it cannot, instead of stalling silently.

**Separation modes** (``AGENT_INDEX_ENGINE_MODE`` / ``ModelProfile.engine_mode``)
let the operator choose how much the engine is separated from the service:

- ``subprocess`` -- the service/indexer spawns ``python -m agent_index.engine.app``
  as a detached child process. Cross-platform (no systemd needed), so it is the
  easy default for local / user-side installs (a single service task that brings
  its own engine up on demand -- e.g. Windows).
- ``systemd`` -- start the model's systemd unit (Linux system deployments;
  supports socket activation). Honours ``AGENT_INDEX_SYSTEMD_SCOPE``.
- ``external`` -- never manage the engine; a container or an externally managed
  task owns it (the VEI-style containerized split). The engine is only probed
  for reachability and a loud error is raised if it is down.
- ``auto`` (default) -- ``systemd`` when a unit is configured and ``systemctl``
  is available, otherwise ``subprocess``.

All systemd interaction is best-effort: if ``systemctl`` is unavailable the
helper reports failure and the caller raises a loud, actionable error.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from agent_procutil import detached_kwargs, windowless_python

from agent_index.engine.client import EngineUnavailableError

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import ModelProfile

log = logging.getLogger(__name__)

# Starting the uvicorn process is quick; the model loads lazily on the first
# embed call, so we only wait for the HTTP server to answer /health here.
_START_TIMEOUT = 60.0
_POLL_INTERVAL = 1.0

# Child engine processes this module spawned (subprocess mode), keyed by
# model_id. ``ensure_engine`` and ``stop_engine`` run in the same process (the
# indexer starts an engine then stops it in its ``finally``), so an in-memory
# handle is sufficient to own the child's lifecycle.
_spawned: dict[str, subprocess.Popen] = {}


def _resolve_mode(profile: ModelProfile) -> str:
    """Resolve the effective engine mode for *profile* (expands ``auto``)."""
    mode = (getattr(profile, "engine_mode", None) or "auto").strip().lower()
    if mode != "auto":
        return mode
    if profile.systemd_unit and shutil.which("systemctl"):
        return "systemd"
    return "subprocess"


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
        subprocess.run(  # noqa: S603
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


def _await_reachable(
    profile: ModelProfile,
    client: EngineClient,
    *,
    proc: subprocess.Popen | None = None,
) -> bool:
    """Poll /health until reachable or the start timeout elapses.

    When *proc* is given (subprocess mode), fail fast if the child exits early.
    Returns True on success; raises :class:`EngineUnavailableError` on timeout or
    early exit.
    """
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise EngineUnavailableError(
                f"Engine subprocess for model '{profile.model_id}' exited early "
                f"(exit code {proc.returncode}); unreachable at {profile.engine_url}"
            )
        if _reachable(client):
            log.info("Engine '%s' is up at %s", profile.model_id, profile.engine_url)
            return True
        time.sleep(_POLL_INTERVAL)
    raise EngineUnavailableError(
        f"Engine '{profile.model_id}' did not become reachable within "
        f"{_START_TIMEOUT:.0f}s at {profile.engine_url}"
    )


def _spawn_engine(profile: ModelProfile) -> subprocess.Popen:
    """Spawn the engine worker as a detached child process (cross-platform).

    The child inherits this process's environment (so ``AGENT_INDEX_DEVICE`` and
    friends carry through) and is detached from the console so it can serve the
    whole reindex; it self-terminates on its idle timeout, and ``stop_engine``
    ends it explicitly when the indexer is done.
    """
    cmd = [
        windowless_python(sys.executable),
        "-m",
        "agent_index.engine.app",
        "--host",
        profile.engine_host,
        "--port",
        str(profile.engine_port),
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": os.environ.copy(),
    }
    # Detach from the parent console so the engine is independent.
    kwargs.update(detached_kwargs())
    return subprocess.Popen(cmd, **kwargs)  # type: ignore[arg-type]  # noqa: S603


def ensure_engine(profile: ModelProfile, client: EngineClient) -> bool:
    """Ensure *profile*'s engine HTTP server is reachable.

    The engine is brought up according to ``profile.engine_mode`` (see the module
    docstring). Returns True if this call started the engine (the caller owns
    shutdown), False if it was already running. Raises
    :class:`EngineUnavailableError` if the engine cannot be made reachable.
    """
    if _reachable(client):
        return False

    mode = _resolve_mode(profile)

    if mode == "external":
        raise EngineUnavailableError(
            f"Engine for model '{profile.model_id}' unreachable at "
            f"{profile.engine_url} and engine_mode is 'external' -- an external "
            f"owner (container / managed task) must start it"
        )

    if mode == "subprocess":
        log.info(
            "Engine '%s' not running -- spawning subprocess at %s",
            profile.model_id,
            profile.engine_url,
        )
        proc = _spawn_engine(profile)
        _spawned[profile.model_id] = proc
        return _await_reachable(profile, client, proc=proc)

    if mode == "systemd":
        unit = profile.systemd_unit
        if not unit:
            raise EngineUnavailableError(
                f"Engine for model '{profile.model_id}' unreachable at "
                f"{profile.engine_url} and engine_mode is 'systemd' but no "
                f"systemd unit is configured to start it"
            )
        log.info("Engine '%s' not running -- starting %s", profile.model_id, unit)
        if not _systemctl("start", unit):
            raise EngineUnavailableError(
                f"Failed to start engine unit '{unit}' for model "
                f"'{profile.model_id}' (systemctl unavailable or unit missing); "
                f"engine unreachable at {profile.engine_url}"
            )
        return _await_reachable(profile, client)

    raise EngineUnavailableError(
        f"Unknown engine_mode '{mode}' for model '{profile.model_id}' "
        f"(expected subprocess, systemd, external, or auto)"
    )


def stop_engine(profile: ModelProfile) -> None:
    """Best-effort stop of *profile*'s engine (on-demand cleanup).

    Ends the spawned child (subprocess mode) or stops the systemd unit; a no-op
    in ``external`` mode, where the engine is owned elsewhere.
    """
    mode = _resolve_mode(profile)

    if mode == "subprocess":
        proc = _spawned.pop(profile.model_id, None)
        if proc is not None and proc.poll() is None:
            log.info(
                "Stopping spawned engine '%s' (pid %s)", profile.model_id, proc.pid
            )
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        return

    if mode == "systemd":
        unit = profile.systemd_unit
        if not unit:
            return
        log.info("Stopping on-demand engine '%s' (%s)", profile.model_id, unit)
        _systemctl("stop", unit)
