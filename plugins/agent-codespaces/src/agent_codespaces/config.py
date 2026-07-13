"""Configuration loading and validation for agent-codespaces.

All configuration lives in adopting repos in ``codespaces.yaml``. The
runtime directory (``~/.agent-codespaces/``) contains only the adoption
manifest (``adopted-repos.yaml``) -- a list of repo paths. On every
start/reload the service reads ``codespaces.yaml`` live from each
adopted repo and merges in memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("agent-codespaces")

# Canonical paths
RUNTIME_DIR = Path.home() / ".agent-codespaces"
ADOPTED_REPOS_FILE = RUNTIME_DIR / "adopted-repos.yaml"
SOCKET_DIR = RUNTIME_DIR / "sockets"
LOG_FILE = RUNTIME_DIR / "agent-codespaces.log"
CONFIG_FILENAME = "codespaces.yaml"

# Standard location GitHub Codespaces clones the account dotfiles repo into.
# Canonical here (config is the layer both provision.py and the request-folder
# resolver share); ``provision`` re-exports it for back-compat.
DOTFILES_DIR = "/workspaces/.codespaces/.persistedshare/dotfiles"

# Standard location the control-plane *harness* checkout is materialized at on a
# venue -- kept DISTINCT from the ``DOTFILES_DIR`` housekeeping shim above. The
# harness carries effort / vision state; the dotfiles repo is just the
# GitHub-dotfiles bootstrap. Opt-in: the harness is only placed on a venue when
# ``defaults.harness_repo`` is set (see ``_provision_harness``); unset by
# default, so by default there is NO on-venue harness and the local
# control-plane agent owns effort updates. ``defaults.harness_dir`` overrides
# this generic default path.
HARNESS_DIR = "/workspaces/harness"


def ensure_runtime_dir() -> None:
    """Create the runtime directory (~/.agent-codespaces) if it is absent."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _norm_repo(value: str) -> str:
    """Normalize a repo name for cross-repo matching.

    Strips any ``owner/`` prefix, lower-cases, and drops a trailing
    ``-codespaces`` so a logical repo (``odsp-web``) matches its CodeSpaces
    host repo (``odsp-microsoft/odsp-web-codespaces``).
    """
    s = value.strip().lower().split("/")[-1]
    suffix = "-codespaces"
    return s[: -len(suffix)] if s.endswith(suffix) else s


def _repo_matches_codespace(repo: str, cs_repository: str | None) -> bool:
    """Whether ``repo`` addresses the CodeSpace's own hosted repository."""
    if not cs_repository:
        return False
    return _norm_repo(repo) == _norm_repo(cs_repository)

# Remote-resolved `cd` for a CodeSpace session when no explicit
# ``workspace_folder`` is configured (#33). Expanded by the remote
# ``bash -l -c`` at launch, so the agent lands in the repo checkout rather than
# ``/home/vscode``. Order: ``$CODESPACE_VSCODE_FOLDER`` (the VS Code/Codespaces
# convention, when exported to the shell) -> ``$WORKING_DIRECTORY`` (set by
# Codespaces to the devcontainer workspace folder; reliably present in the
# ``bash -l`` login shell the --stdio launch uses -- this is what rescues a
# dispatched agent from landing in ``/home/vscode`` when the other vars aren't
# exported, #134) -> ``$VM_REPO_PATH`` (set by many devcontainers) -> ``.``
# (no-op last resort: keep the SSH default cwd -- never forces $HOME).
_WORKSPACE_CD = (
    'cd "${CODESPACE_VSCODE_FOLDER:-${WORKING_DIRECTORY:-${VM_REPO_PATH:-.}}}"'
)


@dataclass
class CredentialSourceConfig:
    """Configuration for a single credential source type."""

    enabled: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_resources: list[str] = field(default_factory=list)


@dataclass
class CredentialsConfig:
    """Credential relay configuration."""

    sources: dict[str, CredentialSourceConfig] = field(default_factory=dict)
    relay_port: int = 9857
    # Default ADO host for bare `get-access-token` requests that carry no host.
    # Set this to your Azure DevOps host (e.g. ``your-org.visualstudio.com`` or
    # ``dev.azure.com``). Left unset, such requests are rejected rather than
    # assuming an organization.
    ado_host: str | None = None
    # #77: enforce host `az login` at connect time when the host cannot mint an
    # ADO REST bearer (the relay's get-azure-token path needs a signed-in host
    # az identity). Default True: a connect to an ADO workspace runs `az login`
    # on the host when needed and ABORTS the connect (with a clear message) if it
    # can't complete -- failing fast is better than a silent ADO-REST failure
    # that only surfaces later mid-dispatch. Set False to downgrade to a loud
    # warning that never aborts an otherwise-healthy SSH+relay session. The relay
    # itself always logs a loud, actionable not-logged-in error regardless.
    enforce_ado_rest_login: bool = True


@dataclass
class RepoConfig:
    """Per-target-repo CodeSpace settings.

    Keyed by the CodeSpace repository (e.g.
    ``my-org/my-codespaces-repo``). ``provision`` hooks declared
    here apply only to CodeSpaces of this repo.

    A CodeSpaces repo frequently differs from the product checkout it hosts
    (e.g. ``my-org/odsp-web-codespaces`` serves a ``/workspaces/odsp-web``
    checkout). The directional "consume-from" relationship -- *we consume
    CodeSpaces from this repo for product repo X* -- is recorded with
    ``workspace_repo``, mirroring agent-worktrees' "related repos" concept.
    The remote workspace folder then derives from it
    (``/workspaces/<basename(workspace_repo)>``) unless an explicit
    ``workspace_folder`` overrides it. This is what makes an agent launched
    for ``odsp-web-codespaces`` land in ``/workspaces/odsp-web`` rather than
    the (wrong) ``/workspaces/odsp-web-codespaces``.
    """

    workspace_repo: str | None = None
    workspace_folder: str | None = None
    machine_type: str | None = None
    location: str | None = None
    # Which devcontainer config ``gh codespace create`` should use for this
    # repo. Only consulted when the repo exposes MORE THAN ONE discoverable
    # ``devcontainer.json`` -- in which case ``gh`` would otherwise prompt and
    # hard-fail headless (``failed to prompt: no terminal``). Set this to the
    # config a CodeSpace should be built from (e.g.
    # ``.devcontainer/devcontainer.json``) when the repo also ships alternate
    # devcontainers not meant for CodeSpaces (e.g. a local-Docker one). See
    # ``lifecycle.resolve_devcontainer_path``.
    devcontainer_path: str | None = None
    bootstrap_post_create: str | None = None
    provision: ProvisionConfig | None = None


@dataclass
class ProvisionFile:
    """A file an adopting repo deploys into the CodeSpace on connect.

    ``src`` is resolved relative to the repo that declares it (the dir
    containing the ``codespaces.yaml``). ``dest`` is the remote path and
    may start with ``~``.
    """

    src: str
    dest: str
    mode: str = "0644"
    repo_dir: Path | None = None  # set during merge, for resolving src


@dataclass
class ProvisionConfig:
    """By-convention provisioning hook declared in ``codespaces.yaml``.

    Lets an adopting repo deploy its own files (e.g. shell env snippets)
    and run setup commands on every ``agent-codespaces ssh`` connect,
    without bespoke per-repo SSH tooling. Generic relay setup is handled
    separately by the plugin; this is purely repo-specific extras.

    Can be declared globally (applies to all CodeSpaces) or under
    ``repos.<repo>.provision`` (applies only to that repo's CodeSpaces).
    """

    files: list[ProvisionFile] = field(default_factory=list)
    on_connect: list[str] = field(default_factory=list)
    # Commands run once, right after creation (post-create injection).
    # Use for one-time setup such as running an install script.
    on_create: list[str] = field(default_factory=list)


@dataclass
class CodespacesConfig:
    """Merged configuration from all adopted repos."""

    # Defaults for CodeSpace creation
    default_machine_type: str = "largePremiumLinux"
    default_location: str = "EastUs"
    dotfiles_repo: str | None = None
    ssh_user: str = "vscode"

    # Control-plane *harness* repo (the repo that carries effort / vision
    # state), kept SEPARATE from ``dotfiles_repo`` (the GitHub-dotfiles
    # housekeeping shim). When set, the harness is cloned/synced onto the venue
    # at ``harness_dir`` on connect (see ``_provision_harness``) so an on-venue
    # agent can reference effort / vision state locally. Unset by default -> the
    # harness is NOT put on the venue; the local control-plane agent manages
    # effort updates. This decouples "the harness" from "the dotfiles shim":
    # where the two were once the same repo (so the dotfiles clone doubled as
    # the harness), they are now independent.
    harness_repo: str | None = None
    harness_dir: str = HARNESS_DIR

    # Fallback devcontainer config path used when a repo exposes more than one
    # discoverable ``devcontainer.json`` and no per-repo ``devcontainer_path``
    # (nor an explicit CLI override) is set. The GitHub Codespaces default
    # location, so single-devcontainer repos are unaffected (the path is only
    # PASSED to ``gh`` when the repo actually has multiple configs). See
    # ``lifecycle.resolve_devcontainer_path``.
    default_devcontainer_path: str = ".devcontainer/devcontainer.json"

    # Workspace folder on the CodeSpace.  When set, the remote agent
    # command ``cd``s into this directory before launching Copilot CLI,
    # ensuring a cold-started CodeSpace lands in the repo root even if
    # the workspace volume is still mounting when the SSH session
    # connects.  Typical value: ``/workspaces/<your-repo>``.
    workspace_folder: str | None = None

    # Remote agent command -- what to run on the CodeSpace when
    # connecting via agent-bridge.  Built dynamically from
    # ``workspace_folder`` if not explicitly overridden.  Only set
    # this if you need a completely custom launch command.
    acp_command: str | None = None

    # Credential relay
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)

    # Per-target-repo settings
    repos: dict[str, RepoConfig] = field(default_factory=dict)

    # Global provisioning hooks (apply to every CodeSpace)
    provision: ProvisionConfig = field(default_factory=lambda: ProvisionConfig())

    # Operator-declared CodeSpace-scoped plugins (the control-plane's own
    # `codespace_plugins:` list in codespaces.yaml). Same entry shape as a
    # harness plugin's `codespacePlugins` manifest array
    # (``{source, enable?, forWorkspaceRepo?}``) -- resolved by
    # ``codespace_plugins.resolve_codespace_plugins`` alongside the ones swept
    # from installed harness plugins. This is where an operator declares the
    # generic plugins every CodeSpace should get (e.g. agent-worktrees, efforts)
    # WITHOUT baking that choice into a shared or repo-specific plugin.json.
    codespace_plugins: list[dict] = field(default_factory=list)

    # Source tracking
    source_paths: list[Path] = field(default_factory=list)

    @property
    def effective_acp_command(self) -> str:
        """Return the resolved remote agent command (global / no repo context).

        Equivalent to ``effective_acp_command_for(None)`` -- see that method
        for the full resolution order. Retained for callers with no CodeSpace
        repository in hand.
        """
        return self.effective_acp_command_for(None)

    def workspace_folder_for(self, repo: str | None) -> str | None:
        """Resolve the remote workspace folder for a CodeSpace repository.

        Resolution order (most specific wins):

        1. ``repos.<repo>.workspace_folder`` -- explicit per-repo override.
        2. ``repos.<repo>.workspace_repo`` -- the product repo this CodeSpace
           hosts; the folder derives as ``/workspaces/<basename>`` (the
           GitHub Codespaces checkout convention). This is the "related
           repo" link: it lets ``odsp-web-codespaces`` map to
           ``/workspaces/odsp-web`` without restating the path.
        3. ``defaults.workspace_folder`` -- the global fallback.

        Returns ``None`` when nothing is configured, so the caller falls back
        to the remote-resolved workspace (see ``_WORKSPACE_CD``).
        """
        repo_cfg = self.repos.get(repo) if repo else None
        if repo_cfg is not None:
            if repo_cfg.workspace_folder:
                return repo_cfg.workspace_folder
            if repo_cfg.workspace_repo:
                basename = repo_cfg.workspace_repo.rstrip("/").split("/")[-1]
                if basename:
                    return f"/workspaces/{basename}"
        return self.workspace_folder

    def workspace_folder_for_request(
        self, cs_repository: str | None, requested_repo: str,
    ) -> tuple[str | None, bool]:
        """Resolve the workspace folder for a ``<requested_repo>@<codespace>``.

        Implements the CodeSpace repo-layout **convention** (#174): a repo
        ``<r>`` lives at ``/workspaces/<basename(r)>`` on the CodeSpace, with two
        pre-populated special cases the CodeSpace bootstrap already owns.

        Returns ``(folder, prepopulated)``:

        - ``requested_repo`` is the **account dotfiles repo** -> ``DOTFILES_DIR``
          (``/workspaces/.codespaces/.persistedshare/dotfiles``), ``prepopulated``
          True -- the universal bootstrap clones/keeps it current.
        - ``requested_repo`` is the **CodeSpace's own product** (e.g. ``odsp-web``
          on an ``odsp-web-codespaces`` CodeSpace) -> the bare default folder
          (``workspace_folder_for(cs_repository)``), ``prepopulated`` True -- the
          devcontainer already checked it out.
        - **any other repo** -> ``/workspaces/<basename(requested_repo)>``,
          ``prepopulated`` False -- caller clones-if-missing.

        ``prepopulated`` tells the command builder whether a clone-if-missing is
        appropriate (never for a folder the bootstrap owns).
        """
        if self.dotfiles_repo and _norm_repo(requested_repo) == _norm_repo(
            self.dotfiles_repo
        ):
            return DOTFILES_DIR, True
        is_own = _repo_matches_codespace(requested_repo, cs_repository)
        if is_own:
            # Honor an explicit bare-default override (per-repo workspace_folder
            # or workspace_repo) for the CodeSpace's own product; otherwise the
            # convention basename below yields the same /workspaces/<basename>.
            configured = self.workspace_folder_for(cs_repository)
            if configured:
                return configured, True
        basename = requested_repo.rstrip("/").split("/")[-1]
        folder = f"/workspaces/{basename}" if basename else None
        return folder, is_own

    def effective_acp_command_for(
        self, repo: str | None, *,
        requested_repo: str | None = None,
        repo_remote: str | None = None,
    ) -> str:
        """Return the resolved remote agent command for a CodeSpace repo.

        ``repo`` is the CodeSpace's own hosted repository (used for per-repo
        config + the bare-default workspace folder). ``requested_repo`` (with an
        optional ``repo_remote`` URL) is the ``<repo>`` half of a
        ``<repo>@<codespace>`` cross-repo address (#174).

        **Bare** (``requested_repo`` is ``None``) -- unchanged:
        1. Explicit ``acp_command`` if set (a complete custom override).
        2. ``cd <workspace_folder> && copilot ...`` when a workspace folder
           resolves for ``repo`` (see ``workspace_folder_for``).
        3. ``cd "<remote-resolved workspace>" && copilot ...`` otherwise -- the
           directory is resolved *on the CodeSpace* at launch (see
           ``_WORKSPACE_CD``) so a session lands in the repo checkout rather
           than ``/home/vscode`` (#33).

        **Cross-repo** (``requested_repo`` set) -- apply the repo-layout
        convention (``workspace_folder_for_request``):
        - a **pre-populated** folder (own product / dotfiles) -> plain
          ``cd <folder> && copilot ...`` (no clone; the bootstrap owns it).
        - **any other** folder with a known ``repo_remote`` ->
          ``[ -d <folder>/.git ] || git clone <remote> <folder>; cd <folder> &&
          copilot ...`` (clone-if-missing over the credential relay the
          ``--stdio`` login shell already set up).
        - any other folder with **no** remote -> plain ``cd <folder> && ...``;
          the ``cd`` fails loudly if the checkout is absent, surfacing the
          missing-remote misconfiguration rather than silently launching in the
          wrong place.

        ``--allow-all-tools`` is required for headless dispatch: there is no
        human to answer interactive tool-permission prompts.
        """
        copilot = "copilot --acp --stdio --allow-all-tools"

        if requested_repo is not None:
            folder, prepopulated = self.workspace_folder_for_request(
                repo, requested_repo
            )
            if folder is None:
                return f"{_WORKSPACE_CD} && {copilot}"
            if prepopulated or not repo_remote:
                return f"cd {folder} && {copilot}"
            clone = f"[ -d {folder}/.git ] || git clone {repo_remote} {folder}"
            return f"{clone}; cd {folder} && {copilot}"

        if self.acp_command:
            return self.acp_command
        workspace_folder = self.workspace_folder_for(repo)
        if workspace_folder:
            return f"cd {workspace_folder} && {copilot}"
        return f"{_WORKSPACE_CD} && {copilot}"

    def provision_for_repo(self, repo: str | None) -> ProvisionConfig:
        """Collect provisioning hooks that apply to a CodeSpace.

        Returns the union of the global ``provision`` hooks and any
        declared under ``repos.<repo>.provision`` for the CodeSpace's
        repository. Global hooks run first.
        """
        files = list(self.provision.files)
        on_connect = list(self.provision.on_connect)
        on_create = list(self.provision.on_create)
        if repo and repo in self.repos:
            repo_prov = self.repos[repo].provision
            if repo_prov:
                files.extend(repo_prov.files)
                on_connect.extend(repo_prov.on_connect)
                on_create.extend(repo_prov.on_create)
        return ProvisionConfig(
            files=files, on_connect=on_connect, on_create=on_create,
        )


@dataclass
class AdoptedRepo:
    """A repo registered in the adoption manifest."""

    path: Path
    adopted_at: str | None = None


def load_adopted_repos() -> list[AdoptedRepo]:
    """Load the adoption manifest from the runtime directory."""
    if not ADOPTED_REPOS_FILE.exists():
        return []

    with open(ADOPTED_REPOS_FILE) as f:
        data = yaml.safe_load(f) or {}

    repos = []
    for entry in data.get("repos", []):
        repos.append(AdoptedRepo(
            path=Path(entry["path"]),
            adopted_at=entry.get("adopted_at"),
        ))
    return repos


def save_adopted_repos(repos: list[AdoptedRepo]) -> None:
    """Write the adoption manifest to the runtime directory."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "repos": [
            {"path": str(r.path), "adopted_at": r.adopted_at}
            for r in repos
        ]
    }
    with open(ADOPTED_REPOS_FILE, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)


def load_repo_config(repo_path: Path) -> dict[str, Any] | None:
    """Load codespaces.yaml from a single repo. Returns None if missing."""
    config_file = repo_path / CONFIG_FILENAME
    if not config_file.exists():
        log.warning("No %s found in %s", CONFIG_FILENAME, repo_path)
        return None

    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def _parse_credential_source(raw: dict[str, Any]) -> CredentialSourceConfig:
    """Parse a credential source config block."""
    return CredentialSourceConfig(
        enabled=raw.get("enabled", False),
        allowed_hosts=raw.get("allowed_hosts", []),
        allowed_resources=raw.get("allowed_resources", []),
    )


def _parse_provision(raw: dict[str, Any], repo_dir: Path | None) -> ProvisionConfig:
    """Parse a ``provision`` block, tagging files with their repo dir."""
    files: list[ProvisionFile] = []
    for f in raw.get("files", []) or []:
        if not isinstance(f, dict) or "src" not in f or "dest" not in f:
            log.warning("Skipping invalid provision file entry: %r", f)
            continue
        files.append(ProvisionFile(
            src=f["src"],
            dest=f["dest"],
            mode=str(f.get("mode", "0644")),
            repo_dir=repo_dir,
        ))
    on_connect = [str(c) for c in (raw.get("on_connect", []) or [])]
    on_create = [str(c) for c in (raw.get("on_create", []) or [])]
    return ProvisionConfig(files=files, on_connect=on_connect, on_create=on_create)


def _parse_repo_config(raw: dict[str, Any], repo_dir: Path | None = None) -> RepoConfig:
    """Parse a per-target-repo config block."""
    bootstrap = raw.get("bootstrap", {})
    provision_raw = raw.get("provision")
    return RepoConfig(
        workspace_repo=raw.get("workspace_repo"),
        workspace_folder=raw.get("workspace_folder"),
        machine_type=raw.get("machine_type"),
        location=raw.get("location"),
        devcontainer_path=raw.get("devcontainer_path"),
        bootstrap_post_create=bootstrap.get("post_create"),
        provision=(
            _parse_provision(provision_raw, repo_dir) if provision_raw else None
        ),
    )


def load_merged_config() -> CodespacesConfig:
    """Load and merge config from all adopted repos.

    Reads ``codespaces.yaml`` live from each adopted repo path.
    First repo's values win on conflicts (except credential sources
    which are unioned).
    """
    adopted = load_adopted_repos()
    if not adopted:
        return CodespacesConfig()

    merged = CodespacesConfig()
    defaults_set = False

    for entry in adopted:
        raw = load_repo_config(entry.path)
        if raw is None:
            continue

        merged.source_paths.append(entry.path)

        # Defaults (first wins)
        defaults = raw.get("defaults", {})
        if not defaults_set and defaults:
            merged.default_machine_type = defaults.get(
                "machine_type", merged.default_machine_type
            )
            merged.default_location = defaults.get(
                "location", merged.default_location
            )
            merged.default_devcontainer_path = defaults.get(
                "devcontainer_path", merged.default_devcontainer_path
            )
            merged.dotfiles_repo = defaults.get(
                "dotfiles_repo", merged.dotfiles_repo
            )
            merged.harness_repo = defaults.get(
                "harness_repo", merged.harness_repo
            )
            merged.harness_dir = defaults.get(
                "harness_dir", merged.harness_dir
            )
            merged.ssh_user = defaults.get(
                "ssh_user", merged.ssh_user
            )
            merged.acp_command = defaults.get(
                "acp_command", merged.acp_command
            )
            merged.workspace_folder = defaults.get(
                "workspace_folder", merged.workspace_folder
            )
            defaults_set = True

        # Credentials (union sources across repos)
        creds_raw = raw.get("credentials", {})
        if creds_raw:
            merged.credentials.relay_port = creds_raw.get(
                "relay_port", merged.credentials.relay_port
            )
            merged.credentials.ado_host = creds_raw.get(
                "ado_host", merged.credentials.ado_host
            )
            merged.credentials.enforce_ado_rest_login = bool(creds_raw.get(
                "enforce_ado_rest_login",
                merged.credentials.enforce_ado_rest_login,
            ))
            for source_name, source_raw in creds_raw.get("sources", {}).items():
                if source_name not in merged.credentials.sources:
                    merged.credentials.sources[source_name] = _parse_credential_source(
                        source_raw
                    )
                else:
                    # Union allowed hosts
                    existing = merged.credentials.sources[source_name]
                    new_hosts = set(existing.allowed_hosts) | set(
                        source_raw.get("allowed_hosts", [])
                    )
                    existing.allowed_hosts = sorted(new_hosts)

        # Repos (first wins on conflicts)
        for repo_key, repo_raw in raw.get("repos", {}).items():
            if repo_key not in merged.repos:
                merged.repos[repo_key] = _parse_repo_config(repo_raw, entry.path)

        # Global provisioning hooks (union across all adopted repos)
        provision_raw = raw.get("provision")
        if provision_raw:
            parsed = _parse_provision(provision_raw, entry.path)
            merged.provision.files.extend(parsed.files)
            merged.provision.on_connect.extend(parsed.on_connect)
            merged.provision.on_create.extend(parsed.on_create)

        # Operator-declared CodeSpace-scoped plugins (union across adopted repos).
        cs_plugins_raw = raw.get("codespace_plugins")
        if isinstance(cs_plugins_raw, list):
            merged.codespace_plugins.extend(
                e for e in cs_plugins_raw if isinstance(e, dict)
            )

    return merged


def validate_config(config: CodespacesConfig) -> list[str]:
    """Validate a merged config. Returns a list of warnings/errors."""
    issues: list[str] = []

    if not config.source_paths:
        issues.append("No adopted repos with codespaces.yaml found")

    for source_name, source_cfg in config.credentials.sources.items():
        if source_cfg.enabled and not source_cfg.allowed_hosts:
            issues.append(
                f"Credential source '{source_name}' is enabled but has no allowed_hosts"
            )

    return issues
