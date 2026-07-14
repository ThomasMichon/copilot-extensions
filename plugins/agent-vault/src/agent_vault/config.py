"""Configuration helpers for agent-vault."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IS_WINDOWS = platform.system() == "Windows"
DEFAULT_TCP_PORT = 19999

# Runtime endpoint paths. Each honors an environment override so a deployment can
# run the daemon at custom paths (e.g. a branded service, or several named vaults
# side by side without colliding). Unset -> the platform default below.
SOCKET_ENV = "AGENT_VAULT_SOCKET"
PID_ENV = "AGENT_VAULT_PID"
LOG_ENV = "AGENT_VAULT_LOG"

DEFAULT_SOCKET_PATH = "/tmp/agent-vault-service.sock"
SOCKET_PATH = os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET_PATH
PID_FILE_LINUX = "/tmp/agent-vault-service.pid"
PID_FILE_WIN = Path(os.environ.get("TEMP", "C:/Temp")) / "agent-vault-service.pid"
PID_FILE = os.environ.get(PID_ENV) or (
    str(PID_FILE_WIN) if IS_WINDOWS else PID_FILE_LINUX
)
LOG_FILE_LINUX = Path("/tmp") / "agent-vault-service.log"
LOG_FILE_WIN = Path(os.environ.get("TEMP", "C:/Temp")) / "agent-vault-service.log"
LOG_FILE = Path(os.environ.get(LOG_ENV) or (LOG_FILE_WIN if IS_WINDOWS else LOG_FILE_LINUX))
CONFIG_ENV = "AGENT_VAULT_CONFIG"
REPO_CONFIG_NAME = ".agent-vault.json"


@dataclass(frozen=True)
class VaultConfig:
    """Resolved agent-vault settings."""

    kpdb: str | None = None
    group: str | None = None
    port: int = DEFAULT_TCP_PORT
    socket_path: str = SOCKET_PATH
    pid_file: str = PID_FILE
    log_file: Path = LOG_FILE


@dataclass(frozen=True)
class ResolvedVault:
    """Resolved vault context for the current process/repository."""

    vault_name: str | None
    kpdb: str | None
    group: str | None
    port: int
    sources: dict[str, str]


def default_config_path() -> Path:
    """Return the default JSON settings file path."""
    if IS_WINDOWS:
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "agent-vault" / "config.json"


def _global_config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or default_config_path())


def load_global_config() -> dict[str, Any]:
    """Load the global JSON settings file."""
    path = _global_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_global_config(data: dict[str, Any]) -> None:
    """Write the global JSON settings file."""
    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _nonempty(value: Any) -> bool:
    return value is not None and str(value) != ""


def _clean_group(value: Any) -> str | None:
    if not _nonempty(value):
        return None
    group = str(value).strip("/ ")
    return group or None


def _expand_kpdb(value: Any, *, base_dir: Path | None = None) -> str | None:
    if not _nonempty(value):
        return None
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    path = Path(expanded)
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return str(path)


def _vault_registry(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = data.get("vaults")
    if not isinstance(raw, dict):
        return {}
    return {str(name): cfg for name, cfg in raw.items() if isinstance(cfg, dict)}


def _legacy_flat_config(data: dict[str, Any]) -> dict[str, Any]:
    legacy: dict[str, Any] = {}
    for key in ("kpdb", "group", "vault_group", "port"):
        if key in data:
            legacy[key] = data[key]
    return legacy


def _discover_repo_config(cwd: str | None) -> tuple[Path | None, dict[str, Any]]:
    start = Path(cwd or os.getcwd()).resolve()
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        path = directory / REPO_CONFIG_NAME
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return path, {}
            return path, data if isinstance(data, dict) else {}
    return None, {}


def _pick_vault_name(
    registry: dict[str, dict[str, Any]],
    repo_data: dict[str, Any],
    global_data: dict[str, Any],
    ext_data: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    env_vault = os.environ.get("AGENT_VAULT")
    if _nonempty(env_vault):
        return str(env_vault), "env"
    if _nonempty(repo_data.get("vault")):
        return str(repo_data["vault"]), "repo"
    if ext_data and _nonempty(ext_data.get("vault")):
        return str(ext_data["vault"]), "ext"
    if _nonempty(global_data.get("default_vault")):
        return str(global_data["default_vault"]), "global"
    if len(registry) == 1:
        name = next(iter(registry))
        return name, f"vault:{name}"
    return None, "default"


def _ext_config(cwd: str | None) -> dict[str, Any]:
    """Collect config contributed by registered extension config sources."""
    try:
        from .extensions import get_registry
    except Exception:
        return {}
    try:
        return get_registry().collect_config(cwd)
    except Exception:
        return {}


def resolve_context(cwd: str | None = None) -> ResolvedVault:
    """Resolve the active vault using env, repo, extension, global, and defaults."""
    global_data = load_global_config()
    registry = _vault_registry(global_data)
    legacy = _legacy_flat_config(global_data)
    repo_path, repo_data = _discover_repo_config(cwd)
    repo_base_dir = repo_path.parent if repo_path else None
    ext_data = _ext_config(cwd)

    vault_name, vault_source = _pick_vault_name(registry, repo_data, global_data, ext_data)
    named_base = registry.get(vault_name or "")
    base = named_base or {}
    base_source = f"vault:{vault_name}" if named_base and vault_name else "global"

    sources: dict[str, str] = {"vault": vault_source}

    env_kpdb = os.environ.get("KPDB")
    if _nonempty(env_kpdb):
        kpdb = _expand_kpdb(env_kpdb)
        sources["kpdb"] = "env"
    elif _nonempty(repo_data.get("kpdb")):
        kpdb = _expand_kpdb(repo_data["kpdb"], base_dir=repo_base_dir)
        sources["kpdb"] = "repo"
    elif _nonempty(ext_data.get("kpdb")):
        kpdb = _expand_kpdb(ext_data["kpdb"])
        sources["kpdb"] = "ext"
    elif _nonempty(base.get("kpdb")):
        kpdb = _expand_kpdb(base["kpdb"])
        sources["kpdb"] = base_source
    elif _nonempty(legacy.get("kpdb")):
        kpdb = _expand_kpdb(legacy["kpdb"])
        sources["kpdb"] = "global"
    else:
        kpdb = None
        sources["kpdb"] = "default"

    env_group = os.environ.get("VAULT_GROUP")
    if _nonempty(env_group):
        group = _clean_group(env_group)
        sources["group"] = "env"
    elif _nonempty(repo_data.get("group")):
        group = _clean_group(repo_data["group"])
        sources["group"] = "repo"
    elif _nonempty(ext_data.get("group")):
        group = _clean_group(ext_data["group"])
        sources["group"] = "ext"
    elif _nonempty(base.get("group")):
        group = _clean_group(base["group"])
        sources["group"] = base_source
    elif _nonempty(legacy.get("group") or legacy.get("vault_group")):
        group = _clean_group(legacy.get("group") or legacy.get("vault_group"))
        sources["group"] = "global"
    else:
        group = None
        sources["group"] = "default"

    env_port = os.environ.get("AGENT_VAULT_PORT")
    if _nonempty(env_port):
        port = _as_int(env_port, DEFAULT_TCP_PORT)
        sources["port"] = "env"
    elif _nonempty(repo_data.get("port")):
        port = _as_int(repo_data["port"], DEFAULT_TCP_PORT)
        sources["port"] = "repo"
    elif _nonempty(ext_data.get("port")):
        port = _as_int(ext_data["port"], DEFAULT_TCP_PORT)
        sources["port"] = "ext"
    elif _nonempty(base.get("port")):
        port = _as_int(base["port"], DEFAULT_TCP_PORT)
        sources["port"] = base_source
    elif _nonempty(legacy.get("port")):
        port = _as_int(legacy["port"], DEFAULT_TCP_PORT)
        sources["port"] = "global"
    else:
        port = DEFAULT_TCP_PORT
        sources["port"] = "default"

    return ResolvedVault(
        vault_name=vault_name,
        kpdb=kpdb,
        group=group,
        port=port,
        sources=sources,
    )


def list_vaults() -> dict[str, dict[str, Any]]:
    """Return configured named vaults."""
    return _vault_registry(load_global_config())


def add_vault(
    name: str,
    kpdb: str,
    group: str | None = None,
    port: int | None = None,
) -> None:
    """Add or update a named vault in the global registry."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("vault name is required")
    if not kpdb:
        raise ValueError("kpdb is required")

    data = load_global_config()
    vaults = data.setdefault("vaults", {})
    if not isinstance(vaults, dict):
        vaults = {}
        data["vaults"] = vaults

    item: dict[str, Any] = {"kpdb": kpdb}
    clean_group = _clean_group(group)
    if clean_group:
        item["group"] = clean_group
    if port is not None:
        item["port"] = int(port)
    vaults[clean_name] = item
    if not data.get("default_vault"):
        data["default_vault"] = clean_name
    save_global_config(data)


def set_default_vault(name: str) -> None:
    """Set the global default vault by name."""
    clean_name = name.strip()
    data = load_global_config()
    if clean_name not in _vault_registry(data):
        raise KeyError(f"unknown vault: {clean_name}")
    data["default_vault"] = clean_name
    save_global_config(data)


def remove_vault(name: str) -> None:
    """Remove a named vault from the global registry."""
    clean_name = name.strip()
    data = load_global_config()
    vaults = data.get("vaults")
    if not isinstance(vaults, dict) or clean_name not in vaults:
        raise KeyError(f"unknown vault: {clean_name}")
    del vaults[clean_name]
    if data.get("default_vault") == clean_name:
        data.pop("default_vault", None)
    save_global_config(data)


def load_config() -> VaultConfig:
    """Resolve configuration from JSON settings and environment variables."""
    data = load_global_config()
    context = resolve_context()
    socket_path = str(data.get("socket_path") or SOCKET_PATH)
    pid_file = str(data.get("pid_file") or PID_FILE)
    log_file = Path(str(data.get("log_file") or LOG_FILE))
    return VaultConfig(
        kpdb=context.kpdb,
        group=context.group,
        port=context.port,
        socket_path=socket_path,
        pid_file=pid_file,
        log_file=log_file,
    )


def resolve_kpdb(*, required: bool = True) -> str:
    """Return the configured KeePass database path."""
    kpdb = resolve_context().kpdb
    if kpdb:
        return kpdb
    if required:
        raise RuntimeError("KeePass database path is not configured; set KPDB to your .kdbx path")
    return ""


def tcp_port() -> int:
    """Return the configured localhost TCP port."""
    return resolve_context().port


def normalize_entry(entry: str, group: str | None = None) -> str:
    """Return a plain KeePass path, prefixing bare names with a configured group."""
    entry = entry.strip()
    effective_group = _clean_group(group)
    if group is None:
        effective_group = load_config().group
    if effective_group and entry and "/" not in entry and not entry.startswith(
        effective_group + "/"
    ):
        return f"{effective_group}/{entry}"
    return entry
