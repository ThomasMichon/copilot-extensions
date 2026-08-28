"""Configuration for agent-containers.

Runtime state lives under ``~/.agent-containers/`` (lease file, log).
Fleet/agent settings are read from a ``containers.yaml`` file, looked up
(in order) from:

1. ``$AGENT_CONTAINERS_CONFIG`` if set,
2. ``./containers.yaml`` in the current working directory,
3. ``~/.agent-containers/containers.yaml``.

A missing config is fine -- built-in defaults target a generic VS Code dev
container (user ``vscode``, workspace ``/workspace``). Point them at a real
repo by writing a ``containers.yaml`` (see the README / containers-fleet skill).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .private_state import ensure_private_dir

log = logging.getLogger("agent-containers")

# Canonical runtime paths.
RUNTIME_DIR = Path.home() / ".agent-containers"
# Windows/WSL installations that share one Docker provider can point only their
# mutable coordination state at one filesystem-visible directory. Runtime venvs
# and platform-specific installation artifacts remain under ``RUNTIME_DIR``.
STATE_DIR = Path(
    os.environ.get(
        "AGENT_CONTAINERS_STATE_DIR",
        str(RUNTIME_DIR),
    )
).expanduser()
LEASE_FILE = STATE_DIR / "leases.json"
LOG_FILE = RUNTIME_DIR / "agent-containers.log"
CONFIG_FILENAME = "containers.yaml"

# Label written on fleet containers at `up` time so discovery can find
# containers that were not created by the VS Code devcontainer flow (which
# would otherwise carry `devcontainer.local_folder`).
FLEET_LABEL = "agent-containers.fleet"
SECURITY_PROFILE_LABEL = "agent-containers.security-profile"
SECURITY_POLICY_LABEL = "agent-containers.security-policy"
SECURITY_HOME_LABEL = "agent-containers.security-home"
SECURITY_UID_LABEL = "agent-containers.security-uid"
SECURITY_GID_LABEL = "agent-containers.security-gid"
SECURITY_IMAGE_ID_LABEL = "agent-containers.security-image-id"

# Default ACP launch command run inside the container. Mirrors the codespaces
# resolver. ``--allow-all-tools`` is required for headless dispatch.
DEFAULT_ACP_COMMAND = "copilot --acp --stdio --allow-all-tools"
TRUSTED_PROFILE = "trusted"
RESTRICTED_PROFILE = "restricted"
SECURITY_PROFILES = {TRUSTED_PROFILE, RESTRICTED_PROFILE}
RESTRICTED_POLICY_VERSION = 2
DEFAULT_RESCUE_MEMBER_BYTES = 64 * 1024**2
DEFAULT_RESCUE_CAPTURE_BYTES = 256 * 1024**2
DEFAULT_RESCUE_TOTAL_BYTES = 1024**3
DEFAULT_RESCUE_RETAIN_PER_CONTAINER = 3
DEFAULT_RESCUE_OPERATION_SECONDS = 600.0
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE_ENV_RE = re.compile(
    r"(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIALS?)($|_)"
)


def is_sensitive_environment_name(name: str) -> bool:
    return bool(_SENSITIVE_ENV_RE.search(name.upper()))


@dataclass
class DotfilesConfig:
    """A designated host *dotfiles* repo to reproduce in a fleet container.

    Mirrors the GitHub Codespaces dotfiles flow (clone the repo, run its
    ``install.sh``, symlink ``.*`` files into ``$HOME``) for a local Docker
    container. After the container is created the host repo is copied in with
    ``docker cp`` to ``target`` (owned by the remote user), then
    ``install_command`` is run in ``target``.

    Copying (rather than bind-mounting) keeps the host repo pristine -- it is
    only read, never mounted, so ``install.sh`` writes git config / symlinks
    against the container-local copy. Optional + per-user: a missing ``repo``
    disables the whole step.
    """

    # Host path to the dotfiles repo to reproduce (absolute or ~-expanded).
    repo: str | None = None
    # Container path the repo is materialised at (matches the Codespaces layout
    # so dotfiles that hard-code it keep working).
    target: str = "/workspaces/.codespaces/.persistedshare/dotfiles"
    # Command run in ``target`` after the copy (login shell, as the remote
    # user). Empty / null skips the install step (mount + copy still happen).
    install_command: str | None = "bash install.sh"

    def host_repo(self) -> Path | None:
        return Path(self.repo).expanduser() if self.repo else None


@dataclass
class HarnessConfig:
    """A designated host *harness* repo (the control-plane checkout that carries
    effort / vision state) reproduced inside a fleet container.

    Kept DISTINCT from :class:`DotfilesConfig` (the GitHub-dotfiles shim): the
    harness carries effort / vision state referenced *in place*. Same
    copy-not-mount mechanics, but materialized at ``/workspaces/<basename(repo)>``
    -- the **standard repo-layout convention**, same as a CodeSpace, not a
    bespoke path -- and with **no install step** -- the harness is referenced,
    not installed. Optional + opt-in: a missing ``repo`` disables the step, so by
    default no harness is placed on the container and the local control-plane
    agent owns effort updates.
    """

    # Host path to the harness repo to reproduce (absolute or ~-expanded).
    repo: str | None = None
    # Command run in ``target`` after the copy. None (default) skips it -- the
    # harness is a checkout referenced in place, not an installer.
    install_command: str | None = None

    @property
    def target(self) -> str:
        """Container path the harness lands at: ``/workspaces/<basename(repo)>``
        by the standard repo-layout convention (mirrors the CodeSpace layout)."""
        name = Path(self.repo).name if self.repo else "harness"
        return f"/workspaces/{name}"

    def host_repo(self) -> Path | None:
        return Path(self.repo).expanduser() if self.repo else None


@dataclass
class RescueConfig:
    """Host-side limits for restricted session-evidence captures."""

    max_member_bytes: int = DEFAULT_RESCUE_MEMBER_BYTES
    max_capture_bytes: int = DEFAULT_RESCUE_CAPTURE_BYTES
    max_total_bytes: int = DEFAULT_RESCUE_TOTAL_BYTES
    retain_per_container: int = DEFAULT_RESCUE_RETAIN_PER_CONTAINER
    operation_timeout_seconds: float = DEFAULT_RESCUE_OPERATION_SECONDS

    def validate(self) -> None:
        for name in ("max_member_bytes", "max_capture_bytes", "max_total_bytes"):
            if getattr(self, name) <= 0:
                raise RuntimeError(f"rescue.{name} must be positive")
        if self.max_capture_bytes < self.max_member_bytes:
            raise RuntimeError(
                "rescue.max_capture_bytes must be at least max_member_bytes"
            )
        if self.max_total_bytes < self.max_capture_bytes:
            raise RuntimeError(
                "rescue.max_total_bytes must be at least max_capture_bytes"
            )
        if self.retain_per_container <= 0:
            raise RuntimeError("rescue.retain_per_container must be positive")
        if (
            self.operation_timeout_seconds <= 0
            or not math.isfinite(self.operation_timeout_seconds)
        ):
            raise RuntimeError("rescue.operation_timeout_seconds must be positive")


@dataclass
class FleetConfig:
    """A named pool of dev containers built from one devcontainer spec.

    Keyed by fleet name (e.g. ``myrepo``). Containers are named
    ``<name_prefix>-<n>`` (e.g. ``myrepo-1``).
    """

    repo: str = ""
    # Path to the devcontainer project (dir containing .devcontainer/) used
    # to build/create containers for this fleet. Resolved on the host.
    devcontainer_path: str | None = None
    # Path to a specific devcontainer.json, passed to the devcontainer CLI as
    # ``--config``. Needed when the spec is NOT at the default
    # ``<devcontainer_path>/.devcontainer/devcontainer.json`` -- e.g. a nested
    # ``.devcontainer/docker/devcontainer.json``. Resolved relative to
    # ``devcontainer_path`` when not absolute.
    devcontainer_config: str | None = None
    # Container image to `docker run` when not using the devcontainer CLI.
    image: str | None = None
    size: int = 1
    name_prefix: str | None = None
    workspace_folder: str | None = None
    exec_user: str | None = None
    acp_command: str | None = None
    # Trusted development venues preserve the historical host-integrated
    # behavior. Restricted venues are a fail-closed transport boundary.
    security_profile: str = TRUSTED_PROFILE
    # Docker network name/mode. Restricted fleets default to "none"; trusted
    # fleets retain Docker's default network when this is unset.
    network: str | None = None
    # Restricted resource ceilings. Safe defaults apply when omitted.
    memory: str | None = None
    cpus: float | None = None
    pids_limit: int | None = None
    workspace_size: str | None = None
    home_size: str | None = None
    # Explicit non-secret values baked into the container environment.
    environment: dict[str, str] = field(default_factory=dict)
    # Per-fleet credential overrides. Restricted fleets always resolve both
    # capabilities false, regardless of these values.
    forward_gh_token: bool | None = None
    relay_enabled: bool | None = None
    # "clone" (Model A, default) or "mount" (Model B, future).
    code_model: str = "clone"

    def prefix(self, fleet_name: str) -> str:
        return self.name_prefix or fleet_name

    def resolved_config(self) -> str | None:
        """Absolute path to the devcontainer.json for ``--config``, or None.

        Relative ``devcontainer_config`` is resolved against
        ``devcontainer_path``; an absolute value is returned as-is.
        """
        if not self.devcontainer_config:
            return None
        p = Path(self.devcontainer_config).expanduser()
        if not p.is_absolute() and self.devcontainer_path:
            p = Path(self.devcontainer_path).expanduser() / p
        return str(p)

    @property
    def restricted(self) -> bool:
        return self.security_profile == RESTRICTED_PROFILE

    def effective_network(self) -> str | None:
        if self.network:
            return self.network
        return "none" if self.restricted else None

    def effective_memory(self) -> str | None:
        if self.memory:
            return self.memory
        return "4g" if self.restricted else None

    def effective_cpus(self) -> float | None:
        if self.cpus is not None:
            return self.cpus
        return 2.0 if self.restricted else None

    def effective_pids_limit(self) -> int | None:
        if self.pids_limit is not None:
            return self.pids_limit
        return 256 if self.restricted else None

    def effective_workspace_size(self) -> str | None:
        if self.workspace_size:
            return self.workspace_size
        return "2g" if self.restricted else None

    def effective_home_size(self) -> str | None:
        if self.home_size:
            return self.home_size
        return "512m" if self.restricted else None

    def validate_restricted(self) -> None:
        """Reject restricted settings that disable their own resource bounds."""
        if not self.restricted:
            return
        if self.effective_cpus() <= 0 or not math.isfinite(self.effective_cpus()):
            raise RuntimeError("Restricted fleet 'cpus' must be a positive finite value")
        if self.effective_pids_limit() <= 0:
            raise RuntimeError("Restricted fleet 'pids_limit' must be positive")
        for field_name, value in (
            ("memory", self.effective_memory()),
            ("workspace_size", self.effective_workspace_size()),
            ("home_size", self.effective_home_size()),
        ):
            match = re.fullmatch(
                r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?)?\s*",
                str(value).lower(),
            )
            if not match or float(match.group(1)) <= 0:
                raise RuntimeError(
                    f"Restricted fleet '{field_name}' must be a positive byte size"
                )
        for name in self.environment:
            if not _ENV_NAME_RE.fullmatch(name):
                raise RuntimeError(f"Restricted environment name '{name}' is invalid")
            if is_sensitive_environment_name(name):
                raise RuntimeError(
                    f"Restricted environment '{name}' looks credential-bearing; "
                    "use a dedicated least-privilege identity channel instead"
                )

    def security_policy_fingerprint(
        self,
        workspace_folder: str,
        exec_user: str,
    ) -> str | None:
        """Fingerprint the enforced restricted creation policy.

        Persisted as a Docker label so a config change cannot make an old,
        differently-provisioned container merely *look* restricted. Bump
        ``RESTRICTED_POLICY_VERSION`` whenever the fixed hardening set changes.
        """
        if not self.restricted:
            return None
        policy = {
            "version": RESTRICTED_POLICY_VERSION,
            "network": self.effective_network(),
            "memory": self.effective_memory(),
            "memory_swap": self.effective_memory(),
            "cpus": self.effective_cpus(),
            "pids_limit": self.effective_pids_limit(),
            "workspace_size": self.effective_workspace_size(),
            "home_size": self.effective_home_size(),
            "workspace_folder": workspace_folder,
            "image": self.image,
            "exec_user": exec_user,
            "environment": dict(sorted(self.environment.items())),
            "rootfs": "read-only",
            "capabilities": "drop-all",
            "no_new_privileges": True,
            "host_mounts": False,
            "writable_surfaces": "bounded-tmpfs",
        }
        encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass
class ContainersConfig:
    """Top-level agent-containers configuration."""

    # Global defaults (overridable per-fleet)
    exec_user: str = "vscode"
    workspace_folder: str = "/workspace"
    acp_command: str | None = None
    # Forward the host `gh auth token` into the container as GH_TOKEN so the
    # in-container Copilot CLI is authenticated headlessly.
    forward_gh_token: bool = True
    # On-demand credential relay: deploy in-container shims at connect that fetch
    # tokens from the host relay through the trusted SSH reverse forward. Fixes
    # rush dev-deploy (Azure storage) by serving the host az-login identity.
    relay_enabled: bool = True
    # Legacy Transport-A host retained for config compatibility. Trusted SSH
    # dispatch always binds the relay on container loopback.
    relay_host: str = "host.docker.internal"
    # Container-loopback listen port for SSH -R. The host destination is
    # agent-bridge's independently published live relay port.
    relay_port: int = 9857
    # Azure scopes the relay may mint tokens for. "*" = any scope (gated behind
    # the per-container relay token; mirrors agent-codespaces). Faithfully serves
    # whatever scope the official `azure-auth-helper get-access-token "<scope>"`
    # broker requests -- storage.azure.com/.default, account-specific blob
    # scopes, etc. -- so the in-container consumer (rush build cache, dev-deploy
    # user-delegation SAS) gets a scope-matching AAD token.
    relay_azure_resources: list[str] = field(
        default_factory=lambda: ["*"]
    )
    # Image-name prefixes used as a discovery fallback when a container lacks
    # the devcontainer.local_folder / FLEET_LABEL labels. The default ``vsc-``
    # matches any VS Code devcontainer image; narrow it per machine if needed.
    image_prefixes: list[str] = field(
        default_factory=lambda: ["vsc-"]
    )
    # Optional designated dotfiles repo reproduced inside fleet containers
    # (Codespaces-style clone + install.sh). None disables the step.
    dotfiles: DotfilesConfig | None = None
    # Optional control-plane *harness* repo reproduced inside fleet containers,
    # kept SEPARATE from ``dotfiles`` (copied in at ``/workspaces/<basename>`` by
    # the standard repo-layout convention, no install). None (default) disables
    # it -> no on-venue harness; the local control-plane agent owns effort updates.
    harness: HarnessConfig | None = None
    # Host-owned restricted session-evidence retention and size limits.
    rescue: RescueConfig = field(default_factory=RescueConfig)
    fleets: dict[str, FleetConfig] = field(default_factory=dict)

    def effective_acp_command(
        self, workspace_folder: str | None = None, acp_command: str | None = None
    ) -> str:
        """Resolve the ACP launch command for a container.

        Priority: explicit ``acp_command`` arg > fleet/global ``acp_command``
        > ``cd <workspace_folder> && <DEFAULT_ACP_COMMAND>``.
        """
        cmd = acp_command or self.acp_command
        if cmd:
            return cmd
        ws = workspace_folder or self.workspace_folder
        if ws:
            return f"cd {ws} && {DEFAULT_ACP_COMMAND}"
        return DEFAULT_ACP_COMMAND

    def credentials_for(self, fleet: FleetConfig | None) -> tuple[bool, bool]:
        """Return effective (GitHub token, credential relay) forwarding.

        Restricted fleets are a hard boundary: config cannot opt either host
        credential path back in. Trusted fleets may override the global defaults.
        """
        if fleet and fleet.restricted:
            return False, False
        forward = (
            fleet.forward_gh_token
            if fleet and fleet.forward_gh_token is not None
            else self.forward_gh_token
        )
        relay = (
            fleet.relay_enabled
            if fleet and fleet.relay_enabled is not None
            else self.relay_enabled
        )
        return forward, relay

    def acp_command_for(self, fleet: FleetConfig | None) -> str:
        """Resolve a fleet launch command, failing closed for restricted fleets."""
        workspace = (
            fleet.workspace_folder if fleet and fleet.workspace_folder else None
        ) or self.workspace_folder
        if fleet and fleet.restricted:
            if not fleet.acp_command:
                raise RuntimeError(
                    "Restricted fleet requires an explicit per-fleet "
                    "'acp_command'; the trusted --allow-all-tools default is disabled"
                )
            return self.effective_acp_command(
                workspace_folder=workspace,
                acp_command=fleet.acp_command,
            )
        return self.effective_acp_command(
            workspace_folder=workspace,
            acp_command=(fleet.acp_command if fleet else None),
        )


def _knowledge_overlay_config() -> Path | None:
    """Resolve the bound knowledge repo's ``containers.yaml`` (the knowledge overlay).

    The citadel E1e **knowledge overlay** (config-graft, #947): when this machine
    binds a **stateless harness** to a knowledge repo, personal reference config --
    including the container fleet's ``containers.yaml`` -- lives in the knowledge
    repo, not the shareable harness tree. This asks ``agent-worktrees state-root``
    (run with the process cwd) only to LOCATE the knowledge checkout -- the
    config-READ axis, distinct from where personal state is written -- and returns
    its ``containers.yaml`` when present.

    Purely additive + fail-open: it is consulted only as a **fallback** after the
    explicit env / cwd / machine-local locations miss (so a deliberate machine-local
    ``~/.agent-containers/containers.yaml`` still wins), and a missing binstub /
    non-stateless / unbound repo / any error yields ``None``. Never raises.
    """
    import json
    import shutil
    import subprocess

    exe = shutil.which("agent-worktrees")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "state-root", "--json"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    if not data.get("requires_external") or not data.get("bound"):
        return None
    root = data.get("state_root")
    if not root:
        return None
    candidate = Path(root) / CONFIG_FILENAME
    return candidate if candidate.exists() else None


def _config_path() -> Path | None:
    """Locate the containers.yaml config file, or None if not found."""
    env = os.environ.get("AGENT_CONTAINERS_CONFIG")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    cwd = Path.cwd() / CONFIG_FILENAME
    if cwd.exists():
        return cwd
    runtime = RUNTIME_DIR / CONFIG_FILENAME
    if runtime.exists():
        return runtime
    # E1e knowledge overlay: a stateless harness keeps its containers.yaml in the
    # bound knowledge repo. Additive fallback only -- after the explicit locations
    # above miss, before built-in defaults.
    overlay = _knowledge_overlay_config()
    if overlay is not None:
        return overlay
    return None


def load_config(*, strict: bool = False) -> ContainersConfig:
    """Load configuration from containers.yaml, merged over defaults."""
    config = ContainersConfig()
    path = _config_path()
    if not path:
        log.debug("No containers.yaml found; using built-in defaults")
        return config

    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        if strict:
            raise RuntimeError(f"Failed to read {path}: {exc}") from exc
        log.warning("Failed to read %s: %s", path, exc)
        return config
    data = {} if parsed is None else parsed
    if not isinstance(data, dict):
        message = f"{path}: top-level configuration must be a mapping"
        if strict:
            raise RuntimeError(message)
        log.warning("%s; using built-in defaults", message)
        return config

    # Lazy schema migration (in memory, never persists / never raises) so a
    # still-old config reads at the current shape before install/update rewrites
    # the machine-local copy. A repo/cwd copy is only migrated in memory here.
    from . import config_migrations

    data = config_migrations.migrate_loaded(data)

    config.exec_user = data.get("exec_user", config.exec_user)
    config.workspace_folder = data.get("workspace_folder", config.workspace_folder)
    config.acp_command = data.get("acp_command", config.acp_command)
    config.forward_gh_token = bool(
        data.get("forward_gh_token", config.forward_gh_token)
    )
    relay = data.get("relay", {}) or {}
    if isinstance(relay, dict):
        config.relay_enabled = bool(relay.get("enabled", config.relay_enabled))
        config.relay_host = relay.get("host", config.relay_host)
        config.relay_port = int(relay.get("port", config.relay_port))
        if "deploy_ado" in relay:
            log.warning(
                "relay.deploy_ado is deprecated and ignored; trusted launches "
                "always deploy the launch-scoped Git/ADO helper"
            )
        if isinstance(relay.get("azure_resources"), list):
            config.relay_azure_resources = [str(r) for r in relay["azure_resources"]]
    if "image_prefixes" in data and isinstance(data["image_prefixes"], list):
        config.image_prefixes = [str(p) for p in data["image_prefixes"]]

    dotfiles = data.get("dotfiles", None)
    if isinstance(dotfiles, dict) and dotfiles.get("repo"):
        df = DotfilesConfig(repo=str(dotfiles["repo"]))
        if dotfiles.get("target"):
            df.target = str(dotfiles["target"])
        if "install_command" in dotfiles:
            ic = dotfiles["install_command"]
            df.install_command = str(ic) if ic else None
        config.dotfiles = df

    harness = data.get("harness", None)
    if isinstance(harness, dict) and harness.get("repo"):
        hc = HarnessConfig(repo=str(harness["repo"]))
        if "install_command" in harness:
            ic = harness["install_command"]
            hc.install_command = str(ic) if ic else None
        config.harness = hc

    rescue = data.get("rescue", {}) or {}
    if not isinstance(rescue, dict):
        raise RuntimeError("rescue config must be a key/value mapping")
    config.rescue = RescueConfig(
        max_member_bytes=int(
            rescue.get("max_member_bytes", config.rescue.max_member_bytes)
        ),
        max_capture_bytes=int(
            rescue.get("max_capture_bytes", config.rescue.max_capture_bytes)
        ),
        max_total_bytes=int(
            rescue.get("max_total_bytes", config.rescue.max_total_bytes)
        ),
        retain_per_container=int(
            rescue.get(
                "retain_per_container",
                config.rescue.retain_per_container,
            )
        ),
        operation_timeout_seconds=float(
            rescue.get(
                "operation_timeout_seconds",
                config.rescue.operation_timeout_seconds,
            )
        ),
    )
    config.rescue.validate()

    fleets = data.get("fleets", {}) or {}
    if not isinstance(fleets, dict):
        if strict:
            raise RuntimeError("fleets config must be a key/value mapping")
        log.warning("fleets config must be a key/value mapping; ignoring it")
        return config
    for name, raw in fleets.items():
        raw = raw or {}
        if not isinstance(raw, dict):
            message = f"Fleet '{name}' config must be a key/value mapping"
            if strict:
                raise RuntimeError(message)
            log.warning("%s; ignoring it", message)
            continue
        security_profile = str(raw.get("security_profile", TRUSTED_PROFILE)).lower()
        if security_profile not in SECURITY_PROFILES:
            expected = ", ".join(sorted(SECURITY_PROFILES))
            raise RuntimeError(
                f"Fleet '{name}' has invalid security_profile "
                f"'{security_profile}' (expected one of: {expected})"
            )
        raw_environment = raw.get("environment") or {}
        if not isinstance(raw_environment, dict):
            raise RuntimeError(
                f"Fleet '{name}' environment must be a key/value mapping"
            )
        fleet = FleetConfig(
            repo=raw.get("repo", ""),
            devcontainer_path=raw.get("devcontainer_path"),
            devcontainer_config=raw.get("devcontainer_config"),
            image=raw.get("image"),
            size=int(raw.get("size", 1)),
            name_prefix=raw.get("name_prefix"),
            workspace_folder=raw.get("workspace_folder"),
            exec_user=raw.get("exec_user"),
            acp_command=raw.get("acp_command"),
            security_profile=security_profile,
            network=raw.get("network"),
            memory=raw.get("memory"),
            cpus=(float(raw["cpus"]) if raw.get("cpus") is not None else None),
            pids_limit=(
                int(raw["pids_limit"])
                if raw.get("pids_limit") is not None
                else None
            ),
            workspace_size=raw.get("workspace_size"),
            home_size=raw.get("home_size"),
            environment={
                str(k): str(v)
                for k, v in raw_environment.items()
            },
            forward_gh_token=(
                bool(raw["forward_gh_token"])
                if "forward_gh_token" in raw
                else None
            ),
            relay_enabled=(
                bool(raw["relay_enabled"])
                if "relay_enabled" in raw
                else None
            ),
            code_model=raw.get("code_model", "clone"),
        )
        fleet.validate_restricted()
        config.fleets[name] = fleet

    log.debug("Loaded containers.yaml from %s (%d fleets)", path, len(config.fleets))
    return config


def ensure_runtime_dir() -> None:
    """Create the runtime directory if it does not exist."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def ensure_state_dir() -> None:
    """Create mutable coordination state with owner-only permissions."""
    ensure_private_dir(STATE_DIR)
