"""Runtime configuration for the agent-dispatch coordinator and CLI.

All values come from the environment so the same code runs loopback-only on a
lone dev box or against a designated coordinator host on a shared network:

- ``AGENT_DISPATCH_HOST`` / ``AGENT_DISPATCH_PORT`` -- where the coordinator binds.
  The port defaults to 9330 on a host and 9331 on a WSL guest (which shares the
  Windows host's loopback under mirrored networking); set it to override.
- ``AGENT_DISPATCH_DB`` -- the SQLite queue file (server side).
- ``AGENT_DISPATCH_TOKEN`` -- optional bearer token (server validates, client sends).
- ``AGENT_DISPATCH_SWEEP_INTERVAL`` -- seconds between automatic lease-recovery
  sweeps (server side; ``0`` disables the sweep).
- ``AGENT_DISPATCH_URL`` -- the coordinator base URL the CLI talks to (defaults to
  ``http://<host>:<port>``); set this to point the CLI at a remote coordinator.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9330  # preferred host port (Windows / bare-metal Linux)
WSL_PORT = 9331  # a WSL guest shares the Windows host's loopback -> preferred+1
DEFAULT_DB = Path.home() / ".agent-dispatch" / "tasks.db"
DEFAULT_SWEEP_INTERVAL = 60.0


def _is_wsl_guest() -> bool:
    """True on a WSL guest, which shares the Windows host's TCP port namespace.

    Deliberately asks "am I a WSL guest?", not "am I non-Windows?" -- a
    bare-metal Linux host stays on the preferred port. Mirrors agent-bridge's
    discriminator (WSL_DISTRO_NAME env, else ``microsoft`` in /proc/version).
    """
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def default_port() -> int:
    """Preferred coordinator port (9330); a WSL guest uses 9331 to avoid
    colliding with the Windows host on shared loopback (mirrored networking),
    matching agent-bridge's 9280/9281 host/guest split.
    """
    return WSL_PORT if _is_wsl_guest() else DEFAULT_PORT


@dataclass(frozen=True)
class Config:
    """Resolved coordinator configuration."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_path: str = str(DEFAULT_DB)
    token: str | None = None
    sweep_interval: float = DEFAULT_SWEEP_INTERVAL

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def load_config() -> Config:
    """Resolve the coordinator config from the environment."""
    return Config(
        host=os.environ.get("AGENT_DISPATCH_HOST", DEFAULT_HOST),
        port=int(os.environ.get("AGENT_DISPATCH_PORT", str(default_port()))),
        db_path=os.environ.get("AGENT_DISPATCH_DB", str(DEFAULT_DB)),
        token=os.environ.get("AGENT_DISPATCH_TOKEN") or None,
        sweep_interval=float(
            os.environ.get("AGENT_DISPATCH_SWEEP_INTERVAL", str(DEFAULT_SWEEP_INTERVAL))
        ),
    )


def client_url() -> str:
    """The base URL the CLI should talk to (``AGENT_DISPATCH_URL`` overrides)."""
    return os.environ.get("AGENT_DISPATCH_URL") or load_config().url


def client_token() -> str | None:
    """The bearer token the CLI should send, if any."""
    return os.environ.get("AGENT_DISPATCH_TOKEN") or None
