"""Layered configuration for agent-logger.

Resolution order (lowest precedence first):

1. Built-in defaults (:data:`DEFAULTS`).
2. ``$AGENT_LOGGER_HOME/config.yaml`` (or ``~/.agent-logger/config.yaml``).
3. Repository-local organization config (``.agent-logger.yaml`` by convention).
4. Environment-variable overrides (``AGENT_LOGGER_*``).

Everything that couples the reusable code to a particular facility -- the
digest store location, the sync target, the voice pack, the output path
template, machine naming, and the session-note marker -- lives here as
configuration with neutral defaults.
"""

from __future__ import annotations

import copy
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
    # Sync target -- see agent_logger.sync. "local" writes to a dotfolder
    # under $HOME; other targets are onedrive/ssh/ssh-tunnel/ingest.
    "sync": {
        "target": "local",
        # What to sync. None -> ~/.copilot (the Copilot CLI state dir).
        "source": None,
        # Only sync sessions whose workspace cwd/git_root matches one of
        # these (case-insensitive substring). Empty -> sync all sessions.
        "repo_allowlist": [],
        # Retention for destination pruning. None/<=0 -> retain everything.
        "retention_days": None,
        "lock_timeout_sec": 10,
        # Target-independent post-push notify. After any successful push the
        # engine fires a best-effort HTTP POST to `url` (JSON body
        # {"machine": <machine>}; `{machine}` in the url is also substituted),
        # so a downstream consumer can crunch immediately regardless of which
        # transport target is used. Empty url -> no notify. Facility-neutral:
        # point it at a public webhook callback (e.g. a Home Assistant webhook
        # that relays to a processing service).
        "notify": {
            "url": None,
            "bearer_token_file": None,
            "timeout": 5,
        },
        # Per-target options, keyed by target name.
        "targets": {
            "local": {"path": None},
            "onedrive": {"subfolder": "Apps/agent-logger/sessions"},
            "ssh": {},
            "ssh-tunnel": {},
            "ingest": {},
        },
    },
    # Log writer presentation. Repository-local config may override only this
    # block, so a checked-in convention cannot alter machine-local sync state.
    "log": {
        # Root directory under which logs are written. None = current
        # working directory (the repo the user is in).
        "root": None,
        # Path template for emitted logs, relative to root. Tokens:
        # {year} {month} {day} {hhmmss} {machine} {title}. Neutral default
        # groups by date and omits machine.
        "path_template": "{year}/{month}/{day} {hhmmss} {title}.md",
        # IANA timezone for log timestamps. None = system local time.
        "timezone": None,
        # Name of the voice pack (a skills directory). "none" = no persona.
        "voice_pack": "none",
        # Marker that flags operator-highlighted session notes.
        "note_marker": "SESSION NOTE:",
        # Optional Markdown outline/instructions for repository-specific log
        # body sections. Null means use the writer's built-in structure.
        "template": None,
    },
    # Machine identity. When name is None it is auto-detected (hostname,
    # with a -wsl suffix inside WSL).
    "machine": {
        "name": None,
    },
}

REPO_CONFIG_FILENAMES: tuple[str, ...] = (
    ".agent-logger.yaml",
    ".agent-logger.yml",
    ".config/agent-logger.yaml",
    ".config/agent-logger.yml",
)


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


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml is required to read config.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def _load_user_config(path: Path) -> dict[str, Any]:
    data = _load_yaml_config(path)
    if not data:
        return {}
    # Lazy schema migration (in memory, never persists / never raises) so a
    # still-old config reads at the current shape before install/update rewrites
    # the machine-local file.
    from . import config_migrations

    return config_migrations.migrate_loaded(data)


def _find_repo_root(start: Path | None = None) -> Path | None:
    """Find the nearest git repository root at or above ``start``."""
    here = (start or Path.cwd()).expanduser().resolve()
    if here.is_file():
        here = here.parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def find_repo_config(start: Path | None = None) -> Path | None:
    """Find the repository-local agent-logger config by convention.

    ``$AGENT_LOGGER_REPO_CONFIG`` may point at an explicit file. Set it to
    ``0``/``false``/``off``/``none`` to disable repo-local config discovery.
    Otherwise the nearest git root is searched for :data:`REPO_CONFIG_FILENAMES`.
    """
    env = os.environ.get("AGENT_LOGGER_REPO_CONFIG")
    if env:
        if env.strip().lower() in {"0", "false", "off", "none"}:
            return None
        explicit = Path(env).expanduser()
        return explicit if explicit.is_file() else None

    root = _find_repo_root(start)
    if root is None:
        return None
    for name in REPO_CONFIG_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _load_repo_config(path: Path) -> dict[str, Any]:
    """Load the repo-local organization config.

    Repo-local config is intentionally scoped to ``log`` settings. It lets a
    repository define its checked-in log organization without letting arbitrary
    checkouts change machine-local sync targets or runtime state locations.
    """
    data = _load_yaml_config(path)
    log = data.get("log") if isinstance(data, dict) else None
    if not isinstance(log, dict):
        return {}

    scoped = copy.deepcopy(log)
    config_base = path.parent.parent if path.parent.name == ".config" else path.parent
    root = scoped.get("root")
    if isinstance(root, str) and root.strip() and not root.startswith("~"):
        root_path = Path(root)
        if not root_path.is_absolute():
            scoped["root"] = str((config_base / root_path).resolve())
    return {"log": scoped}


class Config:
    """Resolved agent-logger configuration."""

    def __init__(
        self, data: dict[str, Any], home: Path, repo_config_path: Path | None = None
    ) -> None:
        self._data = data
        self.home = home
        self.repo_config_path = repo_config_path

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
        """Default local-target root (``<home>/sessions``)."""
        local = self._data.get("sync", {}).get("targets", {}).get("local", {}) or {}
        configured = local.get("path")
        if configured:
            return Path(configured).expanduser()
        return self.home / "sessions"

    @property
    def sync_source(self) -> Path:
        """What to sync. Defaults to the Copilot CLI state dir ``~/.copilot``."""
        configured = self._data.get("sync", {}).get("source")
        if configured:
            return Path(configured).expanduser()
        return Path.home() / ".copilot"

    @property
    def sync_retention_days(self) -> int | None:
        """Retention in days, or ``None`` to retain everything.

        Accepts the sentinel strings ``infinite``/``forever``/``never`` (and
        blank) as "retain all".
        """
        raw = self._data.get("sync", {}).get("retention_days")
        if raw is None:
            return None
        if isinstance(raw, str):
            if raw.strip().lower() in {"infinite", "forever", "never", "none", ""}:
                return None
            try:
                return int(raw)
            except ValueError:
                return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @property
    def sync_lock_timeout(self) -> int:
        return int(self._data.get("sync", {}).get("lock_timeout_sec", 10))

    @property
    def sync_repo_allowlist(self) -> list[str]:
        """Repo patterns to include; empty list means "sync all"."""
        raw = self._data.get("sync", {}).get("repo_allowlist", [])
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        return [str(s).strip() for s in raw if str(s).strip()]

    @property
    def sync_notify(self) -> dict[str, Any]:
        """Resolved target-independent post-push notify config.

        ``url`` empty/None means no notify. ``bearer_token_file`` is optional;
        ``timeout`` defaults to 5s.
        """
        raw = dict(self._data.get("sync", {}).get("notify", {}) or {})
        return {
            "url": (raw.get("url") or "").strip(),
            "bearer_token_file": (raw.get("bearer_token_file") or "").strip(),
            "timeout": int(raw.get("timeout") or 5),
        }

    def target_options(self, name: str) -> dict[str, Any]:
        """Resolved options for the named sync target.

        The ``local`` target's ``path`` defaults to :attr:`sync_path` so the
        destination stays tied to the configured home dir.
        """
        opts = dict(self._data.get("sync", {}).get("targets", {}).get(name, {}) or {})
        if name == "local" and not opts.get("path"):
            opts["path"] = str(self.sync_path)
        return opts

    @property
    def log_path_template(self) -> str:
        return self._data.get("log", {}).get("path_template", DEFAULTS["log"]["path_template"])

    @property
    def log_root(self) -> Path:
        configured = self._data.get("log", {}).get("root")
        if configured:
            return Path(configured).expanduser()
        return Path.cwd()

    @property
    def log_timezone(self) -> str | None:
        return self._data.get("log", {}).get("timezone")

    @property
    def voice_pack(self) -> str:
        return self._data.get("log", {}).get("voice_pack", "none")

    @property
    def note_marker(self) -> str:
        return self._data.get("log", {}).get("note_marker", DEFAULTS["log"]["note_marker"])

    @property
    def log_template(self) -> str | None:
        """Optional repository-supplied Markdown outline for the log body."""
        raw = self._data.get("log", {}).get("template")
        if raw is None:
            return None
        return str(raw)

    @property
    def machine_name(self) -> str | None:
        return self._data.get("machine", {}).get("name")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        """Return a deep copy of the merged config.

        Deep (not shallow): without a user override for a given nested block,
        ``_deep_merge`` leaves that block as a reference into the module-global
        ``DEFAULTS``. A shallow copy here would let a caller's nested mutation
        (e.g. ``cfg.as_dict()["sync"]["notify"] = ...``) corrupt ``DEFAULTS``
        for the whole process. Deep-copying keeps the config a value, not a
        shared view.
        """
        return copy.deepcopy(self._data)


def load_config(
    home: Path | None = None,
    *,
    repo_start: Path | None = None,
    include_repo: bool = True,
) -> Config:
    """Load layered configuration into a :class:`Config`."""
    resolved_home = home or home_dir()
    data = _deep_merge(DEFAULTS, _load_user_config(resolved_home / "config.yaml"))
    repo_config_path = find_repo_config(repo_start) if include_repo else None
    if repo_config_path:
        data = _deep_merge(data, _load_repo_config(repo_config_path))

    # Environment overrides (flat, opt-in).
    if os.environ.get("AGENT_LOGGER_SYNC_TARGET"):
        data["sync"]["target"] = os.environ["AGENT_LOGGER_SYNC_TARGET"]
    if os.environ.get("AGENT_LOGGER_VOICE_PACK"):
        data["log"]["voice_pack"] = os.environ["AGENT_LOGGER_VOICE_PACK"]

    return Config(data, resolved_home, repo_config_path)
