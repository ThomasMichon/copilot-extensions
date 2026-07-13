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


def save_config(cfg: ServiceConfig) -> Path:
    """Write config.yaml atomically (via tmp + rename)."""
    cfg_path = config_dir() / "config.yaml"
    tmp_path = cfg_path.with_suffix(".yaml.tmp")
    data = cfg.model_dump(exclude_defaults=False)
    tmp_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    tmp_path.replace(cfg_path)
    return cfg_path


# One-time config migrations, each guarded by a marker file under
# ``<config dir>/.migrations`` so it applies exactly once per machine even though
# ``load_config`` runs on every daemon start. Keyed by a stable name; add new
# migrations as new markers.
_MIGRATION_DIR = ".migrations"


def migrate_config(cfg: ServiceConfig) -> ServiceConfig:
    """Apply one-time config migrations; persist + return the updated config.

    Each migration is guarded by its **own** marker file under
    ``<config dir>/.migrations`` so it applies exactly once per machine even
    though ``load_config`` runs on every daemon start, and a deliberate operator
    override set *after* a migration sticks (the marker prevents re-flipping).

    **session_host_default_on:** Session Hosts are now the durable-dispatch
    default (see #145/#177). A machine still on the OLD default (``session_host_
    enabled: false``, written explicitly by the full-serialization config writer)
    adopts the new default **once**.

    **idle_reap_default_on:** The idle-session reaper (#1826) is now armed by
    default (``idle_reap_ttl_seconds`` model default ``0 -> 600``) -- the natural
    complement to Session Hosts being default-on, so an idle Session Host child
    can't leak indefinitely if a consumer crashes or forgets to ``DELETE`` its
    session. A machine still carrying the OLD explicit ``idle_reap_ttl_seconds:
    0`` (full-serialization writer) adopts the armed default **once**; a
    deliberate ``0`` set *after* this migration sticks.
    """
    changed = False
    mig_dir = config_dir() / _MIGRATION_DIR

    # -- session_host_default_on ------------------------------------------
    marker = mig_dir / "session_host_default_on"
    if not marker.exists():
        if not cfg.session_host_enabled:
            cfg = cfg.model_copy(update={"session_host_enabled": True})
            changed = True
            log.info(
                "Config migration: session_host_enabled -> True "
                "(Session Hosts are now default-on)"
            )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("applied\n", encoding="utf-8")

    # -- idle_reap_default_on ---------------------------------------------
    marker = mig_dir / "idle_reap_default_on"
    if not marker.exists():
        if cfg.idle_reap_ttl_seconds == 0:
            cfg = cfg.model_copy(update={"idle_reap_ttl_seconds": 600})
            changed = True
            log.info(
                "Config migration: idle_reap_ttl_seconds 0 -> 600 "
                "(idle-session reaper is now armed by default)"
            )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("applied\n", encoding="utf-8")

    if changed:
        save_config(cfg)
    return cfg


def adopt_topology(
    profile_name: str,
    repo_path: str,
    machines_yaml: str | None = None,
    agents_config: str | None = None,
) -> ServiceConfig:
    """Add or update a topology profile pointing to a repo's config files.

    Auto-discovers machines.yaml at conventional locations. The agent roster is
    **derived** from topology (machines.yaml + related.yaml), so ``acp-agents.json``
    is no longer auto-discovered; an explicit ``agents_config`` is still honored
    as a deprecated override.

    Returns the updated ServiceConfig (already saved to disk).
    """
    from .models import TopologyProfile

    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"Repo path does not exist: {repo}")

    # Auto-discover machines.yaml
    if not machines_yaml:
        for candidate in [
            repo / "machines.yaml",
            repo / "config" / "machines.yaml",
            repo / ".github" / "machines.yaml",
        ]:
            if candidate.is_file():
                machines_yaml = str(candidate)
                break

    # acp-agents.json auto-discovery is retired -- the roster is derived from
    # machines.yaml (+ related.yaml). An explicit agents_config is still honored
    # (deprecated back-compat) but never auto-discovered.

    if not machines_yaml:
        raise FileNotFoundError(
            f"No machines.yaml found in {repo}. "
            "Specify it explicitly with --machines-yaml."
        )

    # Validate discovered paths
    if machines_yaml and not Path(machines_yaml).is_file():
        raise FileNotFoundError(f"machines_yaml not found: {machines_yaml}")
    if agents_config and not Path(agents_config).is_file():
        raise FileNotFoundError(f"agents_config not found: {agents_config}")

    # Normalize to forward slashes for cross-platform config portability
    if machines_yaml:
        machines_yaml = str(Path(machines_yaml).resolve()).replace("\\", "/")
    if agents_config:
        agents_config = str(Path(agents_config).resolve()).replace("\\", "/")

    cfg = load_config()
    cfg.topologies[profile_name] = TopologyProfile(
        machines_yaml=machines_yaml,
        agents_config=agents_config,
    )
    save_config(cfg)
    return cfg


def remove_topology(profile_name: str) -> ServiceConfig:
    """Remove a topology profile. Raises KeyError if not found."""
    cfg = load_config()
    if profile_name not in cfg.topologies:
        raise KeyError(f"Topology profile '{profile_name}' not found")
    del cfg.topologies[profile_name]
    save_config(cfg)
    return cfg


def validate_config() -> list[str]:
    """Validate the current config. Returns a list of issues (empty = OK)."""
    issues: list[str] = []
    cfg = load_config()

    if not cfg.topologies:
        issues.append("No topology profiles configured")

    for name, profile in cfg.topologies.items():
        if profile.machines_yaml and not Path(profile.machines_yaml).expanduser().is_file():
            issues.append(f"topologies.{name}.machines_yaml: file not found: {profile.machines_yaml}")
        if profile.agents_config and not Path(profile.agents_config).expanduser().is_file():
            issues.append(f"topologies.{name}.agents_config: file not found: {profile.agents_config}")
        if not profile.machines_yaml and not profile.agents_config:
            issues.append(f"topologies.{name}: no machines_yaml or agents_config configured")

    db_path = Path(cfg.db_path).expanduser()
    if not db_path.parent.is_dir():
        issues.append(f"db_path parent directory does not exist: {db_path.parent}")

    return issues
