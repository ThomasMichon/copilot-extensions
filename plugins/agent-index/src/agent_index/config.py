"""Runtime configuration for the agent-index service shell."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0
RUN_DIR_ENV = "AGENT_INDEX_RUN_DIR"
ENDPOINT_ENV = "AGENT_INDEX_ENDPOINT"
HOME_ENV = "AGENT_INDEX_HOME"


def install_dir() -> Path:
    """Runtime root for agent-index."""
    return Path(os.environ.get(HOME_ENV) or (Path.home() / ".agent-index"))


def data_dir() -> Path:
    """Derived data directory for future index state."""
    return install_dir() / "data"


def run_dir() -> Path:
    """Directory holding the endpoint rendezvous file."""
    return Path(os.environ.get(RUN_DIR_ENV) or (install_dir() / "run"))


@dataclass(frozen=True)
class Config:
    """Resolved service configuration."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def load_config() -> Config:
    """Resolve service bind configuration from the environment."""
    return Config(
        host=os.environ.get("AGENT_INDEX_HOST", DEFAULT_HOST),
        port=int(os.environ.get("AGENT_INDEX_PORT", str(DEFAULT_PORT))),
    )


def discovered_endpoint():
    """Return the live local endpoint, or None when the service is not running."""
    from . import rendezvous

    override = os.environ.get(ENDPOINT_ENV)
    try:
        return rendezvous.resolve(run_dir(), override=override, probe=rendezvous.connect_probe)
    except rendezvous.EndpointUnavailable:
        return None


def client_url() -> str | None:
    """Return the discovered service URL, or None when no service is running."""
    ep = discovered_endpoint()
    if ep is None or ep.transport != "tcp":
        return None
    host, port = ep.tcp_host_port
    return f"http://{host}:{port}"
