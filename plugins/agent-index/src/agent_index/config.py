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
    """Durable data directory for index state and task queues."""
    return install_dir() / "data"


def run_dir() -> Path:
    """Directory holding the legacy endpoint rendezvous file."""
    return Path(os.environ.get(RUN_DIR_ENV) or (install_dir() / "run"))


def routing_dir() -> Path:
    """Stable zdd routing-table directory shared by all installed versions."""
    return install_dir()


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
    """Return the rendezvous endpoint, or None when the service is not running."""
    from . import rendezvous

    override = os.environ.get(ENDPOINT_ENV)
    try:
        return rendezvous.resolve(run_dir(), override=override, probe=rendezvous.connect_probe)
    except rendezvous.EndpointUnavailable:
        return None


def _routing_url() -> str | None:
    """Return the zdd active endpoint URL, defensively falling back on failure."""
    try:
        from zdd.routing import read_active_endpoint

        ep = read_active_endpoint(routing_dir())
    except Exception:
        return None
    return ep.base_url if ep is not None else None


def client_url() -> str | None:
    """Return the active service URL, following zdd routing before rendezvous."""
    routed = _routing_url()
    if routed:
        return routed

    ep = discovered_endpoint()
    if ep is None or ep.transport != "tcp":
        return None
    host, port = ep.tcp_host_port
    return f"http://{host}:{port}"
