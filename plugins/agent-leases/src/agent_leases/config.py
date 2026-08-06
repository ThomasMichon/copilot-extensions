"""Machine-local configuration for agent-leases."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Configuration is absent or invalid."""


DEFAULT_CONFIG = Path.home() / ".agent-leases" / "config.json"
DEFAULT_REF_PREFIX = "refs/heads/copilot-leases/v1"
_CONFIG_KEYS = {
    "schema_version",
    "origin",
    "ref_prefix",
    "default_ttl_seconds",
    "max_ttl_seconds",
    "clock_skew_seconds",
    "acquire_retries",
}


@dataclass(frozen=True)
class Settings:
    """Validated protocol and remote settings."""

    origin: str
    ref_prefix: str = DEFAULT_REF_PREFIX
    default_ttl_seconds: int = 3600
    max_ttl_seconds: int = 86400
    clock_skew_seconds: int = 30
    acquire_retries: int = 3

    def __post_init__(self) -> None:
        if not self.origin.strip():
            raise ConfigError(
                "Git origin is required; set config key 'origin', "
                "AGENT_LEASES_ORIGIN, or --origin"
            )
        if not self.ref_prefix.startswith("refs/heads/"):
            raise ConfigError("ref_prefix must use the GitHub-compatible refs/heads/ namespace")
        components = self.ref_prefix.split("/")
        if (
            self.ref_prefix.endswith("/")
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", self.ref_prefix)
            or any(
                not component
                or component.startswith(".")
                or component.endswith(".")
                or component.endswith(".lock")
                for component in components
            )
            or any(token in self.ref_prefix for token in ("..", "@{"))
        ):
            raise ConfigError("ref_prefix is not a safe Git ref prefix")
        if not 1 <= self.default_ttl_seconds <= self.max_ttl_seconds:
            raise ConfigError("default_ttl_seconds must be between 1 and max_ttl_seconds")
        if not 1 <= self.max_ttl_seconds <= 604800:
            raise ConfigError("max_ttl_seconds must be between 1 and 604800")
        if not 0 <= self.clock_skew_seconds <= 3600:
            raise ConfigError("clock_skew_seconds must be between 0 and 3600")
        if not 0 <= self.acquire_retries <= 10:
            raise ConfigError("acquire_retries must be between 0 and 10")

    def ttl(self, requested: int | None) -> int:
        """Return a validated requested or default TTL."""
        ttl = self.default_ttl_seconds if requested is None else requested
        if not 1 <= ttl <= self.max_ttl_seconds:
            raise ConfigError(f"TTL must be between 1 and {self.max_ttl_seconds} seconds")
        return ttl


def load_settings(
    *,
    origin: str | None = None,
    config_path: Path | None = None,
) -> Settings:
    """Load settings from JSON, then apply environment and CLI origin overrides."""
    path = config_path or Path(os.environ.get("AGENT_LEASES_CONFIG", DEFAULT_CONFIG))
    raw: dict[str, object] = {}
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read config {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ConfigError(f"config {path} must contain a JSON object")
        unknown = set(value) - _CONFIG_KEYS
        if unknown:
            raise ConfigError(f"config {path} has unknown keys: {', '.join(sorted(unknown))}")
        if value.get("schema_version", 1) != 1:
            raise ConfigError(f"config {path} has unsupported schema_version")
        raw = value

    selected_origin = origin or os.environ.get("AGENT_LEASES_ORIGIN") or raw.get("origin")
    if not isinstance(selected_origin, str):
        selected_origin = ""

    def integer(name: str, default: int) -> int:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"config key '{name}' must be an integer")
        return value

    ref_prefix = raw.get("ref_prefix", DEFAULT_REF_PREFIX)
    if not isinstance(ref_prefix, str):
        raise ConfigError("config key 'ref_prefix' must be a string")
    return Settings(
        origin=selected_origin,
        ref_prefix=ref_prefix,
        default_ttl_seconds=integer("default_ttl_seconds", 3600),
        max_ttl_seconds=integer("max_ttl_seconds", 86400),
        clock_skew_seconds=integer("clock_skew_seconds", 30),
        acquire_retries=integer("acquire_retries", 3),
    )
