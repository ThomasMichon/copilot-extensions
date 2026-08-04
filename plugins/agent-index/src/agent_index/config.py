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
MACHINE_ENV = "AGENT_INDEX_MACHINE"
REPO_ENV = "AGENT_INDEX_REPO"
VALID_ROLES = ("host", "client")
REPO_CONFIG_RELPATH = ".agent-index/config.yaml"


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


def _load_yaml(path: Path) -> dict:
    """Load a YAML mapping from *path*, tolerating a missing/broken file ({})."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_config_role(path: Path) -> str | None:
    """Read a top-level ``role:`` (preferred) or ``engine:`` scalar from a config
    file. Prefers PyYAML; falls back to a dependency-light scanner so a partially
    broken config still resolves a role rather than hard-failing the service.
    """
    data = _load_yaml(path)
    for key in ("role", "engine"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
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


def machine_id() -> str:
    """This machine's identity for indexer designation. ``AGENT_INDEX_MACHINE``
    overrides; otherwise the short hostname. Used only to match against a config
    designation -- the plugin bakes in no machine names."""
    override = os.environ.get(MACHINE_ENV)
    if override and override.strip():
        return override.strip()
    import socket

    return socket.gethostname().split(".")[0].strip().lower()


def repo_root(explicit: str | None = None) -> Path | None:
    """Resolve the harness repo being adopted: an explicit path, ``AGENT_INDEX_REPO``,
    or the CWD's git top-level. ``None`` when not in/at a repo."""
    cand = explicit or os.environ.get(REPO_ENV)
    if cand:
        return Path(cand).expanduser().resolve()
    try:
        import subprocess

        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def repo_config_path(root: Path) -> Path:
    """The repo-committed adoption config: ``<repo>/.agent-index/config.yaml``."""
    return root / REPO_CONFIG_RELPATH


def read_indexer(root: Path | None) -> dict | None:
    """Read the shared indexer designation (``indexer:`` block) from the repo
    config, or ``None`` when unset."""
    if root is None:
        return None
    data = _load_yaml(repo_config_path(root))
    ind = data.get("indexer")
    return ind if isinstance(ind, dict) else None


def _dump_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_machine_role(role: str) -> Path:
    """Write this machine's role into the machine-local config (merging existing
    keys). Returns the config path."""
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role {role!r} (expected one of {VALID_ROLES})")
    return set_machine_config({"role": role})


def set_machine_config(updates: dict) -> Path:
    """Merge *updates* into the machine-local config file. Returns its path."""
    path = config_path()
    data = _load_yaml(path)
    data.update(updates)
    _dump_yaml(path, data)
    return path


def machine_device() -> str | None:
    """The engine device recorded in the machine-local config (``cpu``/``cuda``),
    or ``None`` when unset. Written by adoption's capability match."""
    val = _load_yaml(config_path()).get("device")
    return val.strip().lower() if isinstance(val, str) and val.strip() else None


def write_indexer_designation(
    root: Path, machine: str, *, ssh: str | None = None, endpoint: str | None = None
) -> Path:
    """Record the shared indexer designation into ``<repo>/.agent-index/config.yaml``
    (merging existing keys). Returns the repo config path."""
    path = repo_config_path(root)
    data = _load_yaml(path)
    ind: dict = {"machine": machine}
    if ssh:
        ind["ssh"] = ssh
    if endpoint:
        ind["endpoint"] = endpoint
    data["indexer"] = ind
    _dump_yaml(path, data)
    return path


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


def configured_endpoint() -> str | None:
    """The remote service endpoint recorded in the machine-local config
    (``endpoint:``), written by adoption on a **client** so it reaches the
    designated indexer over the trusted transport (effort agent-index-engine-daemon,
    Phase 8; vision §local-first-standalone). ``None`` when unset (a host/single
    machine resolves its own local service instead)."""
    val = _load_yaml(config_path()).get("endpoint")
    return val.strip() if isinstance(val, str) and val.strip() else None


def client_url() -> str | None:
    """Return the active service URL.

    A **client** carries an explicit machine-local ``endpoint`` (its routing to the
    designated indexer) which wins; otherwise the local service is followed via zdd
    routing, then rendezvous. (An ``AGENT_INDEX_ENDPOINT`` env override, handled by
    callers, still trumps everything.)"""
    configured = configured_endpoint()
    if configured:
        return configured

    routed = _routing_url()
    if routed:
        return routed

    ep = discovered_endpoint()
    if ep is None or ep.transport != "tcp":
        return None
    host, port = ep.tcp_host_port
    return f"http://{host}:{port}"
