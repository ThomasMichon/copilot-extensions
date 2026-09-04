"""Runtime configuration for the agent-index service shell."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0
RUN_DIR_ENV = "AGENT_INDEX_RUN_DIR"
STATE_DIR_ENV = "AGENT_INDEX_STATE_DIR"
LOG_DIR_ENV = "AGENT_INDEX_LOG_DIR"
CACHE_DIR_ENV = "AGENT_INDEX_CACHE_DIR"
CONFIG_ROOT_ENV = "AGENT_INDEX_CONFIG_ROOT"
ROUTING_DIR_ENV = "AGENT_INDEX_ROUTING_DIR"
ENDPOINT_ENV = "AGENT_INDEX_ENDPOINT"
HOME_ENV = "AGENT_INDEX_HOME"
ROLE_ENV = "AGENT_INDEX_ROLE"
CONFIG_ENV = "AGENT_INDEX_CONFIG"
EFFECTIVE_CONFIG_ENV = "AGENT_INDEX_EFFECTIVE_CONFIG"
CONFIG_DATA_ENV = "AGENT_INDEX_CONFIG_DATA_B64"
MACHINE_ENV = "AGENT_INDEX_MACHINE"
REPO_ENV = "AGENT_INDEX_REPO"
VALID_ROLES = ("host", "client")
UNCONFIGURED_ROLE = "unconfigured"
REPO_CONFIG_RELPATH = ".agent-index/config.yaml"


def install_dir() -> Path:
    """Runtime root for agent-index."""
    return Path(os.environ.get(HOME_ENV) or (Path.home() / ".agent-index"))


def data_dir() -> Path:
    """Durable data directory for index state and task queues."""
    override = os.environ.get("AGENT_INDEX_DATA_DIR") or os.environ.get(
        STATE_DIR_ENV
    )
    return Path(override).expanduser() if override else install_dir() / "data"


def run_dir() -> Path:
    """Directory holding the legacy endpoint rendezvous file."""
    return Path(os.environ.get(RUN_DIR_ENV) or (install_dir() / "run"))


def routing_dir() -> Path:
    """Stable zdd routing-table directory shared by all installed versions."""
    override = os.environ.get(ROUTING_DIR_ENV)
    return Path(override).expanduser() if override else install_dir()


def config_path() -> Path:
    """Machine-local role/config file: ``<install_dir>/config.yaml``.

    Overridable with ``AGENT_INDEX_CONFIG``. This is the machine-local half of
    the role source (the other being a source repo's ``<repo>/.agent-index/
    config.yaml``, a runtime/indexing concern resolved by the consuming repo).
    """
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    config_root = os.environ.get(CONFIG_ROOT_ENV)
    if config_root:
        return Path(config_root).expanduser() / "config.yaml"
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


def _load_inline_config() -> dict | None:
    encoded = os.environ.get(CONFIG_DATA_ENV)
    if not encoded:
        return None
    try:
        value = json.loads(
            base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_effective_repo_config(root: Path | None) -> dict:
    if CONFIG_DATA_ENV in os.environ:
        return _load_inline_config() or {}
    effective = os.environ.get(EFFECTIVE_CONFIG_ENV)
    if effective:
        return _load_yaml(Path(effective).expanduser())
    if root is None:
        return {}
    return _load_yaml(repo_config_path(root))


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
    config, or ``None`` when unset.

    Back-compat singular accessor: it returns the singular ``indexer:`` mapping,
    or, for a plural ``indexers:`` deployment, the **primary** (first) indexer.
    Callers that need the full ordered set use :func:`read_indexers`.
    """
    if root is None and not (
        os.environ.get(EFFECTIVE_CONFIG_ENV) or os.environ.get(CONFIG_DATA_ENV)
    ):
        return None
    data = _load_effective_repo_config(root)
    plural = read_indexers(root)
    if plural:
        return plural[0]
    ind = data.get("indexer")
    return ind if isinstance(ind, dict) and ind.get("machine") else None


def read_indexers(root: Path | None) -> list[dict]:
    """Read the ordered indexer designation(s) from the repo config (primary first).

    A multi-indexer deployment declares a plural ``indexers:`` list -- each element a
    ``{machine, ssh?, endpoint?}`` mapping -- whose **order is the failover
    preference**: the first is the primary (freshness/authority), the rest are
    secondaries a client falls back to when the primary is unreachable
    (vision §adoption-designates-ordered-indexers; SSH-mesh robustness). A
    single-indexer deployment's singular ``indexer:`` block is accepted and returned
    as a one-element list. Each machine gets its **own** ssh alias + endpoint, so
    "which machine indexes" and "which server a given client dials" stay independent.

    Malformed entries (no ``machine``) are dropped defensively. Returns ``[]`` when
    neither key is set.
    """
    if root is None and not (
        os.environ.get(EFFECTIVE_CONFIG_ENV) or os.environ.get(CONFIG_DATA_ENV)
    ):
        return []
    data = _load_effective_repo_config(root)
    items = data.get("indexers")
    if isinstance(items, list):
        out = [it for it in items if isinstance(it, dict) and it.get("machine")]
        if out:
            return out
    ind = data.get("indexer")
    if isinstance(ind, dict) and ind.get("machine"):
        return [ind]
    return []


def read_corpus_sources() -> list[dict]:
    """Return the effective ``corpus.sources`` — a **virtual config grafted** from
    every adopted local project's own ``.agent-index/config.yaml``.

    Read **dynamically** (no caching) so edits are picked up on the next reindex
    without a service restart (each reindex runs in a fresh worker process). The
    sweep:

    1. Enumerate adopted local **projects** from the sibling agent-worktrees
       registry (``~/.agent-worktrees/projects.yaml`` — the set of repos that have
       a project binstub), resolving each to its checkout path via
       ``repos.yaml``.
    2. Read each project's committed ``<repo>/.agent-index/config.yaml`` and graft
       its ``corpus.sources`` into one list, deduped by source ``name`` (first
       contributor wins). The originating project's checkout path is attached as
       ``_repo_path`` so a ``git`` source resolves without a second lookup.
    3. Also graft the **machine-local** config's own ``corpus.sources``
       (``config_path()``) as a supplement, so a box may add machine-specific
       sources.

    Each element is a mapping like
    ``{name, type?, repo?, auth?: {account}, trust_domain?}``, plus internal keys
    ``_repo_path`` (the contributing project's checkout) and ``_contributed_by``
    (its project name). Malformed entries are dropped defensively. Returns ``[]``
    when nothing is declared anywhere.
    """
    def _sources_of_data(data: dict) -> list[dict]:
        corpus = data.get("corpus")
        if not isinstance(corpus, dict):
            return []
        srcs = corpus.get("sources")
        if not isinstance(srcs, list):
            return []
        return [s for s in srcs if isinstance(s, dict) and s.get("name")]

    def _sources_of(path: Path) -> list[dict]:
        return _sources_of_data(_load_yaml(path))

    graft: dict[str, dict] = {}

    effective_root = repo_root()
    for spec in _sources_of_data(_load_effective_repo_config(effective_root)):
        spec = dict(spec)
        if effective_root is not None:
            spec.setdefault("_repo_path", str(effective_root))
        spec.setdefault("_contributed_by", "effective-config")
        graft.setdefault(str(spec["name"]), spec)

    # (1)+(2) adopted projects, each self-declaring its index targets
    for name, root in _local_project_roots().items():
        for spec in _sources_of(repo_config_path(root)):
            spec = dict(spec)
            spec.setdefault("_repo_path", str(root))
            spec.setdefault("_contributed_by", name)
            graft.setdefault(str(spec["name"]), spec)

    # (3) machine-local supplement
    for spec in _sources_of(config_path()):
        graft.setdefault(str(spec["name"]), dict(spec))

    return list(graft.values())


def _agent_worktrees_home() -> Path:
    """The sibling agent-worktrees registry dir (``~/.agent-worktrees``)."""
    env = os.environ.get("AGENT_WORKTREES_HOME")
    return Path(env).expanduser() if env else (Path.home() / ".agent-worktrees")


def _registry_platform_key() -> str:
    """Which per-repo path key to read from ``repos.yaml`` on this host."""
    import sys

    if sys.platform == "win32":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _local_project_roots() -> dict[str, Path]:
    """Map adopted-project name → local checkout path.

    Sources the set from ``projects.yaml`` (adopted projects with a binstub) and
    the path from ``repos.yaml`` (the owning path registry). Tolerates a missing
    or malformed registry ({}) so the indexer degrades to the machine-local /
    repo config rather than failing.
    """
    home = _agent_worktrees_home()
    projects = _load_yaml(home / "projects.yaml").get("projects")
    repos = _load_yaml(home / "repos.yaml").get("repos")
    if not isinstance(projects, dict) or not isinstance(repos, dict):
        return {}
    key = _registry_platform_key()
    out: dict[str, Path] = {}
    for name in projects:
        entry = repos.get(name)
        if not isinstance(entry, dict):
            continue
        raw = entry.get(key) or entry.get("windows") or entry.get("linux") or entry.get("wsl")
        if isinstance(raw, str) and raw.strip():
            out[name] = Path(raw.strip()).expanduser()
    return out


def repo_checkout_path(name: str) -> Path | None:
    """Resolve a repo name to its local checkout path from the agent-worktrees
    ``repos.yaml`` registry (platform-appropriate key), or ``None`` if unknown."""
    home = _agent_worktrees_home()
    repos = _load_yaml(home / "repos.yaml").get("repos")
    if not isinstance(repos, dict):
        return None
    entry = repos.get(name)
    if not isinstance(entry, dict):
        return None
    key = _registry_platform_key()
    raw = entry.get(key) or entry.get("windows") or entry.get("linux") or entry.get("wsl")
    return Path(raw.strip()).expanduser() if isinstance(raw, str) and raw.strip() else None


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


def set_machine_config(updates: dict, remove: list[str] | None = None) -> Path:
    """Merge *updates* into the machine-local config file, optionally removing the
    keys in *remove* (e.g. clearing stale client routing when a box becomes a host).
    Returns its path."""
    path = config_path()
    data = _load_yaml(path)
    for key in remove or ():
        data.pop(key, None)
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


def write_indexers_designation(root: Path, indexers: list[dict]) -> Path:
    """Record an **ordered** multi-indexer designation (``indexers:`` list, primary
    first) into ``<repo>/.agent-index/config.yaml`` (merging other keys). Each entry
    is normalized to ``{machine, ssh?, endpoint?}``; entries without a ``machine`` are
    dropped. The singular ``indexer:`` key is removed so the plural list is the single
    source of truth. Returns the repo config path
    (vision §adoption-designates-ordered-indexers)."""
    path = repo_config_path(root)
    data = _load_yaml(path)
    norm: list[dict] = []
    for it in indexers:
        if not isinstance(it, dict):
            continue
        m = str(it.get("machine", "")).strip()
        if not m:
            continue
        entry: dict = {"machine": m}
        if it.get("ssh"):
            entry["ssh"] = str(it["ssh"]).strip()
        if it.get("endpoint"):
            entry["endpoint"] = str(it["endpoint"]).strip()
        norm.append(entry)
    data["indexers"] = norm
    data.pop("indexer", None)
    _dump_yaml(path, data)
    return path


def resolve_role() -> str:
    """Resolve this machine's agent-index role.

    ``host`` runs the durable embedding-engine daemon (heavy torch stack);
    ``client`` installs only the light, torch-free service/CLI and reaches a
    remote engine/service over the trusted transport. Precedence:
    ``AGENT_INDEX_ROLE`` env, then the machine-local config file's
    ``role:``/``engine:`` scalar, else ``unconfigured``. Plugin enablement
    delivers the capability; an explicit setup/configuration activates it.
    The plugin encodes **no** machine names -- role is pure configuration
    (effort agent-index-engine-daemon).
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
    return UNCONFIGURED_ROLE


def configured_role() -> str | None:
    """Return the explicit machine role, or ``None`` when inactive."""
    role = resolve_role()
    return None if role == UNCONFIGURED_ROLE else role


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


def configured_endpoints() -> list[str]:
    """The **ordered** remote endpoints a client may route to (primary first).

    A multi-indexer client records a plural ``endpoints:`` list in its machine-local
    config -- the failover order across the designated indexers. A single-indexer
    client records only the singular ``endpoint:``; it is returned as a one-element
    list. Returns ``[]`` when neither is set (a host resolves its own local service).
    """
    data = _load_yaml(config_path())
    eps = data.get("endpoints")
    if isinstance(eps, list):
        out = [e.strip() for e in eps if isinstance(e, str) and e.strip()]
        if out:
            return out
    single = configured_endpoint()
    return [single] if single else []


def _route_probe_timeout() -> float:
    """Per-endpoint failover probe timeout (seconds), from
    ``AGENT_INDEX_ROUTE_PROBE_TIMEOUT_S``. Defensively falls back to 1.5s on a
    missing/malformed/non-positive value so a stray env setting never crashes
    routing."""
    default = 1.5
    raw = os.environ.get("AGENT_INDEX_ROUTE_PROBE_TIMEOUT_S")
    if not raw:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _endpoint_healthy(base_url: str, timeout: float) -> bool:
    """Best-effort reachability probe of a service endpoint's ``GET /health``.

    Stdlib-only (no httpx in the light routing path) and fully defensive: any
    connect/timeout/HTTP error is treated as unreachable. A 2xx (``ok`` or the
    transient ``draining``) counts as reachable."""
    import urllib.request

    url = base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def client_url() -> str | None:
    """Return the active service URL.

    A **client** carries an explicit machine-local ``endpoint`` (its routing to the
    designated indexer) which wins; a **host** always follows its own local service
    via zdd routing, then rendezvous. (An ``AGENT_INDEX_ENDPOINT`` env override,
    handled by callers, still trumps everything.)

    The configured ``endpoint`` is honored **only for a client**. On a host it is
    stale/spurious and must not shadow the live zdd routing table, whose port
    changes every zero-downtime generation -- a fixed host ``endpoint`` there made
    ``status``/``stop`` probe a dead static port and report the running service as
    down (#1349)."""
    root = repo_root()
    indexers = read_indexers(root)
    if indexers:
        me = machine_id().strip().lower()
        role = (
            "host"
            if any(str(item.get("machine", "")).strip().lower() == me for item in indexers)
            else "client"
        )
    elif root is not None:
        return None
    else:
        role = resolve_role()
    if role == "client":
        repo_endpoints = [
            str(item["endpoint"]).strip()
            for item in indexers
            if isinstance(item.get("endpoint"), str)
            and str(item["endpoint"]).strip()
        ]
        # Client-local routing (for example an SSH forward on a machine-specific
        # port) overrides the shared repository endpoint for the same corpus.
        # It must not capture a different repo that declares SSH-only routing.
        endpoints = (
            configured_endpoints()
            if root is None or repo_endpoints
            else []
        )
        if not endpoints:
            endpoints = repo_endpoints
        if len(endpoints) > 1:
            # Ordered failover across the designated indexers (primary first): use
            # the first reachable one, so a down primary or a broken SSH hop
            # transparently falls back to a secondary (SSH-mesh robustness;
            # vision §adoption-designates-ordered-indexers). When none answer, return
            # the primary deterministically so the caller surfaces its connect error.
            timeout = _route_probe_timeout()
            for ep in endpoints:
                if _endpoint_healthy(ep, timeout):
                    return ep
            return endpoints[0]
        if endpoints:
            # Single configured target (singular ``endpoint:`` or a one-element
            # ``endpoints:`` list): returned as-is, never health-probed (back-compat).
            return endpoints[0]
    elif role != "host":
        return None

    routed = _routing_url()
    if routed:
        return routed

    ep = discovered_endpoint()
    if ep is None or ep.transport != "tcp":
        return None
    host, port = ep.tcp_host_port
    return f"http://{host}:{port}"
