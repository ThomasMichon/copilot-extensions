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
ROLE_ENV = "AGENT_INDEX_ROLE"
CONFIG_ENV = "AGENT_INDEX_CONFIG"
VALID_ROLES = ("host", "client")


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


def config_path() -> Path:
    """Machine-local role/config file: ``<install_dir>/config.yaml``.

    Overridable with ``AGENT_INDEX_CONFIG``. This is the machine-local half of
    the role source (the other being a source repo's ``<repo>/.agent-index/
    config.yaml``, a runtime/indexing concern resolved by the consuming repo).
    """
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return install_dir() / "config.yaml"


def _read_config_role(path: Path) -> str | None:
    """Read a top-level ``role:`` (preferred) or ``engine:`` scalar from a config
    file. Deliberately dependency-light -- a single documented scalar, so we scan
    for it rather than pulling PyYAML into the torch-free service runtime.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    for key in ("role", "engine"):
        m = re.search(
            rf"(?mi)^[ \t]*{key}[ \t]*:[ \t]*[\"']?([A-Za-z]+)[\"']?[ \t]*(?:#.*)?$",
            text,
        )
        if m:
            return m.group(1).strip().lower()
    return None


def resolve_role() -> str:
    """Resolve this machine's agent-index role.

    ``host`` runs the durable embedding-engine daemon (heavy torch stack);
    ``client`` installs only the light, torch-free service/CLI and reaches a
    remote engine/service over the trusted transport. Precedence:
    ``AGENT_INDEX_ROLE`` env, then the machine-local config file's
    ``role:``/``engine:`` scalar, else ``client``. The plugin encodes **no**
    machine names -- role is pure configuration (effort agent-index-engine-daemon).
    """
    env = os.environ.get(ROLE_ENV)
    if env and env.strip().lower() in VALID_ROLES:
        return env.strip().lower()
    cfg = _read_config_role(config_path())
    if cfg in VALID_ROLES:
        return cfg
    if cfg in ("engine", "server", "indexer"):
        return "host"
    if cfg in ("none", "consumer"):
        return "client"
    return "client"


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
