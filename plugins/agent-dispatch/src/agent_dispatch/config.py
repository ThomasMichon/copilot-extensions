"""Runtime configuration for the agent-dispatch coordinator and CLI.

All values come from the environment so the same code runs loopback-only on a
lone dev box or against a designated coordinator host on a shared network:

- ``AGENT_DISPATCH_HOST`` / ``AGENT_DISPATCH_PORT`` -- where the coordinator binds.
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
DEFAULT_PORT = 9847
DEFAULT_DB = Path.home() / ".agent-dispatch" / "tasks.db"
DEFAULT_SWEEP_INTERVAL = 60.0


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
        port=int(os.environ.get("AGENT_DISPATCH_PORT", str(DEFAULT_PORT))),
        db_path=os.environ.get("AGENT_DISPATCH_DB", str(DEFAULT_DB)),
        token=os.environ.get("AGENT_DISPATCH_TOKEN") or None,
        sweep_interval=float(
            os.environ.get("AGENT_DISPATCH_SWEEP_INTERVAL", str(DEFAULT_SWEEP_INTERVAL))
        ),
    )


def client_url() -> str:
    """The base URL the CLI should talk to.

    Resolution order:

    1. ``AGENT_DISPATCH_URL`` -- explicit operator override.
    2. On a **WSL guest**, resolve the Windows-owned coordinator dynamically
       (probe ``127.0.0.1`` for mirrored, then the default gateway for NAT;
       cached best-effort). A WSL guest depends on the Windows host, which owns
       the coordinator (Phase 2 of the coordinator-inversion effort).
    3. Otherwise (standalone Linux, or the Windows host itself) the local
       coordinator URL, ``http://127.0.0.1:9847``.
    """
    override = os.environ.get("AGENT_DISPATCH_URL")
    if override:
        return override
    cfg = load_config()
    try:
        from .netinfo import is_wsl, resolve_wsl_client_url

        if is_wsl():
            return resolve_wsl_client_url(cfg.port)
    except Exception:
        # Detection/probe failure must never break the CLI -- fall back to the
        # local default and let the actual request fail loud if unreachable.
        return cfg.url
    return cfg.url


def client_token() -> str | None:
    """The bearer token the CLI should send, if any."""
    return os.environ.get("AGENT_DISPATCH_TOKEN") or None
