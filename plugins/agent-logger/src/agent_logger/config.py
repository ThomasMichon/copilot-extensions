"""Layered configuration for agent-logger.

Resolution order (lowest precedence first):

1. Built-in defaults (:data:`DEFAULTS`).
2. ``$AGENT_LOGGER_HOME/config.yaml`` (or ``~/.agent-logger/config.yaml``).
3. Environment-variable overrides (``AGENT_LOGGER_*``).

Everything that couples the reusable code to a particular facility -- the
digest store location, the sync target, the voice pack, the output path
template, machine naming, and the session-note marker -- lives here as
configuration with neutral defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dependency
    yaml = None  # type: ignore[assignment]

#: Neutral, personality- and facility-free defaults.
DEFAULTS: dict[str, Any] = {
    # Where collated digest chunks are written/read.
    "store_dir": None,  # resolved to <home>/session-digests when None
    # Sync target -- see agent_logger.sync (later phase). "local" writes to a
    # dotfolder under $HOME; other targets are onedrive/ssh/ingest.
    "sync": {
        "target": "local",
        "path": None,  # resolved to <home>/sessions when None
    },
    # Log writer presentation.
    "log": {
        # Path template for emitted logs. Tokens: {year} {month} {day}
        # {hhmmss} {machine} {title}. Neutral default groups by date.
        "path_template": "{year}/{month}/{day} {hhmmss} {title}.md",
        # Name of the voice pack (a skills directory). "none" = no persona.
        "voice_pack": "none",
        # Marker that flags operator-highlighted session notes.
        "note_marker": "SESSION NOTE:",
    },
    # Machine identity. When name is None it is auto-detected (hostname,
    # with a -wsl suffix inside WSL).
    "machine": {
        "name": None,
    },
}


def home_dir() -> Path:
    """Return the agent-logger runtime/home directory.

    Honors ``$AGENT_LOGGER_HOME``; defaults to ``~/.agent-logger``. This is a
    *local* directory and must never be a cloud-synced folder (an active
    SQLite state DB lives here in later phases).
    """
    env = os.environ.get("AGENT_LOGGER_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".agent-logger"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_user_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml is required to read config.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


class Config:
    """Resolved agent-logger configuration."""

    def __init__(self, data: dict[str, Any], home: Path) -> None:
        self._data = data
        self.home = home

    # -- resolved convenience accessors ---------------------------------

    @property
    def store_dir(self) -> Path:
        configured = self._data.get("store_dir")
        if configured:
            return Path(configured).expanduser()
        return self.home / "session-digests"

    @property
    def sync_target(self) -> str:
        return self._data.get("sync", {}).get("target", "local")

    @property
    def sync_path(self) -> Path:
        configured = self._data.get("sync", {}).get("path")
        if configured:
            return Path(configured).expanduser()
        return self.home / "sessions"

    @property
    def log_path_template(self) -> str:
        return self._data.get("log", {}).get("path_template", DEFAULTS["log"]["path_template"])

    @property
    def voice_pack(self) -> str:
        return self._data.get("log", {}).get("voice_pack", "none")

    @property
    def note_marker(self) -> str:
        return self._data.get("log", {}).get("note_marker", DEFAULTS["log"]["note_marker"])

    @property
    def machine_name(self) -> str | None:
        return self._data.get("machine", {}).get("name")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


def load_config(home: Path | None = None) -> Config:
    """Load layered configuration into a :class:`Config`."""
    resolved_home = home or home_dir()
    data = _deep_merge(DEFAULTS, _load_user_config(resolved_home / "config.yaml"))

    # Environment overrides (flat, opt-in).
    if os.environ.get("AGENT_LOGGER_SYNC_TARGET"):
        data["sync"]["target"] = os.environ["AGENT_LOGGER_SYNC_TARGET"]
    if os.environ.get("AGENT_LOGGER_VOICE_PACK"):
        data["log"]["voice_pack"] = os.environ["AGENT_LOGGER_VOICE_PACK"]

    return Config(data, resolved_home)
