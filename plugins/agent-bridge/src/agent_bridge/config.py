"""Configuration -- load and validate ~/.agent-bridge/config.yaml."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import yaml

from .models import ServiceConfig

log = logging.getLogger("agent-bridge")

_DEFAULT_CONFIG_DIR = "~/.agent-bridge"


def config_dir() -> Path:
    """Resolve the agent-bridge config/state directory."""
    d = Path(
        os.environ.get("AGENT_BRIDGE_CONFIG_DIR", _DEFAULT_CONFIG_DIR)
    ).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config() -> ServiceConfig:
    """Load config from YAML, falling back to defaults."""
    cfg_path = config_dir() / "config.yaml"
    if cfg_path.exists():
        try:
            data = yaml.safe_load(cfg_path.read_text()) or {}
            return ServiceConfig(**data)
        except Exception:
            log.warning("Failed to parse %s, using defaults", cfg_path)
    return ServiceConfig()


def load_or_create_auth_token() -> str:
    """Load the bearer token, generating one on first run."""
    auth_path = config_dir() / "auth.yaml"
    if auth_path.exists():
        try:
            data = yaml.safe_load(auth_path.read_text()) or {}
            token = data.get("token")
            if token:
                return str(token)
        except Exception:
            log.warning("Failed to parse %s, regenerating token", auth_path)

    # Generate a new token
    token = secrets.token_urlsafe(32)
    auth_path.write_text(yaml.dump({"token": token}, default_flow_style=False))
    # Restrict permissions (best-effort on Windows)
    try:
        auth_path.chmod(0o600)
    except OSError:
        pass
    log.info("Generated new auth token at %s", auth_path)
    return token


def write_default_config(cfg: ServiceConfig) -> Path:
    """Write a default config.yaml if none exists. Returns the path."""
    cfg_path = config_dir() / "config.yaml"
    if not cfg_path.exists():
        data = cfg.model_dump(exclude_defaults=False)
        cfg_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        log.info("Wrote default config to %s", cfg_path)
    return cfg_path
