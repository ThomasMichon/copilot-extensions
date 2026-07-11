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
DEFAULT_SOCKET_PATH = "/tmp/agent-vault-service.sock"
SOCKET_PATH = DEFAULT_SOCKET_PATH
PID_FILE_LINUX = "/tmp/agent-vault-service.pid"
PID_FILE_WIN = Path(os.environ.get("TEMP", "C:/Temp")) / "agent-vault-service.pid"
PID_FILE = str(PID_FILE_WIN) if IS_WINDOWS else PID_FILE_LINUX
LOG_FILE_LINUX = Path("/tmp") / "agent-vault-service.log"
LOG_FILE_WIN = Path(os.environ.get("TEMP", "C:/Temp")) / "agent-vault-service.log"
LOG_FILE = LOG_FILE_WIN if IS_WINDOWS else LOG_FILE_LINUX
CONFIG_ENV = "AGENT_VAULT_CONFIG"


@dataclass(frozen=True)
class VaultConfig:
    """Resolved agent-vault settings."""

    kpdb: str | None = None
    group: str | None = None
    port: int = DEFAULT_TCP_PORT
    socket_path: str = DEFAULT_SOCKET_PATH
    pid_file: str = PID_FILE
    log_file: Path = LOG_FILE


def default_config_path() -> Path:
    """Return the default JSON settings file path."""
    if IS_WINDOWS:
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "agent-vault" / "config.json"


def _load_config_file() -> dict[str, Any]:
    path = Path(os.environ.get(CONFIG_ENV) or default_config_path())
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_config() -> VaultConfig:
    """Resolve configuration from JSON settings and environment variables."""
    data = _load_config_file()
    kpdb = os.environ.get("KPDB") or data.get("kpdb")
    group = os.environ.get("VAULT_GROUP") or data.get("group") or data.get("vault_group")
    port = _as_int(os.environ.get("AGENT_VAULT_PORT") or data.get("port"), DEFAULT_TCP_PORT)
    socket_path = str(data.get("socket_path") or DEFAULT_SOCKET_PATH)
    pid_file = str(data.get("pid_file") or PID_FILE)
    log_file = Path(str(data.get("log_file") or LOG_FILE))
    return VaultConfig(
        kpdb=str(kpdb) if kpdb else None,
        group=str(group).strip("/ ") if group else None,
        port=port,
        socket_path=socket_path,
        pid_file=pid_file,
        log_file=log_file,
    )


def resolve_kpdb(*, required: bool = True) -> str:
    """Return the configured KeePass database path."""
    kpdb = load_config().kpdb
    if kpdb:
        return kpdb
    if required:
        raise RuntimeError("KeePass database path is not configured; set KPDB to your .kdbx path")
    return ""


def tcp_port() -> int:
    """Return the configured localhost TCP port."""
    return load_config().port


def normalize_entry(entry: str) -> str:
    """Return a plain KeePass path, prefixing bare names with VAULT_GROUP if set."""
    entry = entry.strip()
    group = load_config().group
    if group and entry and "/" not in entry and not entry.startswith(group + "/"):
        return f"{group}/{entry}"
    return entry



