"""Configuration -- load and validate ~/.agent-bridge/config.yaml."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import yaml
from agent_procutil import no_window_kwargs

from .models import RepoBridgeConfig, ServiceConfig

log = logging.getLogger("agent-bridge")

_DEFAULT_CONFIG_DIR = "~/.agent-bridge"

#: In-repo agent-bridge config location, relative to a repo root.
REPO_CONFIG_RELPATH = ".agent-bridge/config.yaml"


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
            # Lazy schema migration (in memory, never persists / never raises) so
            # a still-old config.yaml loads at the current shape before an
            # install/update has rewritten it on disk.
            from . import config_migrations

            data = config_migrations.migrate_loaded(data)
            return ServiceConfig(**data)
        except Exception:
            log.warning("Failed to parse %s, using defaults", cfg_path)
    return ServiceConfig()


def load_repo_bridge_config(repo_root: Path) -> RepoBridgeConfig | None:
    """Load a repo's in-repo agent-bridge config (``<repo>/.agent-bridge/config.yaml``).

    Returns ``None`` when the file is absent (the common case) or unparseable --
    the in-repo config is purely additive, so a missing/bad file simply means "no
    repo-provided settings", never an error. ``repo_root`` is the repo the topology
    profile derives its roster from (the parent of its ``machines.yaml``).
    """
    cfg_path = Path(repo_root).expanduser() / REPO_CONFIG_RELPATH
    if not cfg_path.exists():
        return None
    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
        return RepoBridgeConfig(**data)
    except Exception:
        log.warning("Failed to parse in-repo config %s, ignoring", cfg_path, exc_info=True)
        return None


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

    **idle_reap_default_on:** The idle-session reaper (#1826) is now armed by
    default (``idle_reap_ttl_seconds`` model default ``0 -> 600``) -- the natural
    complement to always-on Session Hosts, so an idle Session Host child
    can't leak indefinitely if a consumer crashes or forgets to ``DELETE`` its
    session. A machine still carrying the OLD explicit ``idle_reap_ttl_seconds:
    0`` (full-serialization writer) adopts the armed default **once**; a
    deliberate ``0`` set *after* this migration sticks.

    (The former ``session_host_default_on`` value-migration is retired: Session
    Hosts are now the *only* mode -- the ``session_host_enabled`` toggle was
    removed -- so a persisted ``session_host_enabled: false`` is simply ignored
    on load and dropped on the next config write. See dotfiles#1478.)
    """
    changed = False
    mig_dir = config_dir() / _MIGRATION_DIR

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


def _state_root_machines_yaml(repo: Path) -> str | None:
    """Resolve the bound knowledge repo's ``machines.yaml`` (the knowledge overlay).

    The citadel E1e **knowledge overlay** (config-graft, #947): a stateless
    harness carries no ``machines.yaml`` of its own -- machine topology is personal
    reference config that lives in the bound knowledge repo. This asks
    ``agent-worktrees state-root`` (run with cwd=repo) only to LOCATE the knowledge
    checkout -- the config-READ axis, distinct from where personal state is
    written -- then searches the conventional ``machines.yaml`` locations under it.

    Best-effort + fail-open: a missing ``agent-worktrees`` binstub, a
    non-stateless / unbound repo, or any error yields ``None`` (the caller then
    raises its normal "no machines.yaml" error). Never raises.
    """
    import json
    import shutil
    import subprocess

    exe = shutil.which("agent-worktrees")  # marketplace-isolation: allow agent-worktrees-management
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "state-root", "--json"], cwd=str(repo),
            capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    # Only graft when the launch repo actually requires an external state root
    # (a stateless harness); a self-hosted repo resolves to itself and must not
    # be redirected.
    if not data.get("requires_external") or not data.get("bound"):
        return None
    root = data.get("state_root")
    if not root:
        return None
    kroot = Path(root)
    for candidate in [
        kroot / "machines.yaml",
        kroot / ".agent-worktrees" / "machines.yaml",
        kroot / "config" / "machines.yaml",
        kroot / ".github" / "machines.yaml",
    ]:
        if candidate.is_file():
            return str(candidate)
    return None


def _canonical_repo_root(repo: Path) -> Path:
    """Return a linked worktree's stable anchor, or ``repo`` unchanged.

    Topology profiles outlive individual worktrees. Persisting a path beneath a
    linked worktree therefore guarantees a stale profile once that worktree is
    removed. Git's common directory identifies the anchor without requiring
    agent-worktrees to be installed.
    """
    import shutil
    import subprocess

    git = shutil.which("git")
    if not git:
        return repo
    try:
        top_proc = subprocess.run(
            [git, "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
            **no_window_kwargs(),
        )
        if top_proc.returncode != 0 or not (top_proc.stdout or "").strip():
            return repo
        if Path(top_proc.stdout.strip()).resolve() != repo:
            return repo
        common_proc = subprocess.run(
            [git, "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return repo
    if common_proc.returncode != 0 or not (common_proc.stdout or "").strip():
        return repo

    common = Path(common_proc.stdout.strip())
    if not common.is_absolute():
        common = repo / common
    common = common.resolve()
    if common.name != ".git":
        return repo

    anchor = common.parent.resolve()
    if anchor == repo:
        return repo
    try:
        root_proc = subprocess.run(
            [git, "-C", str(anchor), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return repo
    if root_proc.returncode != 0 or not (root_proc.stdout or "").strip():
        return repo
    if Path(root_proc.stdout.strip()).resolve() != anchor:
        return repo

    log.info("Canonicalized linked worktree %s to anchor %s", repo, anchor)
    return anchor


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

    requested_repo = Path(repo_path).resolve()
    if not requested_repo.is_dir():
        raise FileNotFoundError(f"Repo path does not exist: {requested_repo}")
    repo = _canonical_repo_root(requested_repo)

    # Auto-discover machines.yaml -- conventional in-repo locations first, then
    # the knowledge overlay (a stateless harness carries no machines.yaml of its
    # own; machine topology is personal config in the bound knowledge repo --
    # citadel E1e, #947). The .agent-worktrees/ location is the canonical one (#950).
    if not machines_yaml:
        for candidate in [
            repo / "machines.yaml",
            repo / ".agent-worktrees" / "machines.yaml",
            repo / "config" / "machines.yaml",
            repo / ".github" / "machines.yaml",
        ]:
            if candidate.is_file():
                machines_yaml = str(candidate)
                break

    # Knowledge-overlay fallback: when the repo itself has no machines.yaml but
    # is a stateless harness bound to a knowledge repo, resolve the knowledge
    # repo's machines.yaml (the config-graft READ axis; the state-root resolver is
    # only reused to LOCATE the knowledge checkout). Best-effort; a missing binstub
    # / unbound harness just leaves machines_yaml unset and the FileNotFoundError
    # below fires.
    if not machines_yaml:
        machines_yaml = _state_root_machines_yaml(repo)

    # acp-agents.json auto-discovery is retired -- the roster is derived from
    # machines.yaml (+ related.yaml). An explicit agents_config is still honored
    # (deprecated back-compat) but never auto-discovered.

    if not machines_yaml:
        canonical_note = (
            f" (canonicalized from linked worktree {requested_repo})"
            if repo != requested_repo
            else ""
        )
        raise FileNotFoundError(
            f"No machines.yaml found in {repo}{canonical_note}. "
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
