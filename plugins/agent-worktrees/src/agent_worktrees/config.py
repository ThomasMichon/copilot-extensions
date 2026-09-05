"""Config loading and machine detection.

Reads per-project config from ~/.{project}/config.yaml and provides
typed access.  Runtime lives at ~/.agent-worktrees/ (shared across
projects); per-project state at ~/.{project}/.

The active project is resolved from the current working directory (git-like),
or an explicit ``--project``; it is threaded in-process, not read from
``$WORKTREE_PROJECT``.
"""

from __future__ import annotations

import os
import platform
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config_migrations

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_ASSIGNMENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# In-repo, committed config carrying *repo-level policy/settings* (shared
# across every machine that checks out the repo), as opposed to the
# machine-local ``~/.{project}/config.yaml`` which carries machine-specific
# paths. It is the **base** layer for a repo's settings; the machine-local
# ``repos.<name>`` block overrides it per key. The schema is **flat
# repo-settings** (no ``anchor`` / ``worktree_root`` -- those are machine
# paths -- and no ``repos:`` map).
#
# Preferred location is the **directory form**
# ``<anchor>/.agent-worktrees/config.yaml``; the legacy single-file
# ``<anchor>/.agent-worktrees.yaml`` (which carried only a ``pr:`` block) is
# still read as a back-compat fallback.
INREPO_CONFIG_DIRNAME = ".agent-worktrees"        # <anchor>/.agent-worktrees/config.yaml
INREPO_CONFIG_FILENAME = ".agent-worktrees.yaml"  # legacy single-file fallback

# Global, machine-wide config: the user-owned BASE layer holding only
# machine-wide settings -- top-level ``srcroot`` / ``machine`` / ``platform`` /
# ``copilot_profiles`` / ``session_backend`` / ``auto_fast_forward`` /
# ``headless``. It never carries
# per-repo settings or a registry of repos/machines; the full merged config for
# a target repo is computed on demand by ``load_config``. Lives at
# ``~/.agent-worktrees/config.yaml`` (the shared runtime root).
GLOBAL_CONFIG_FILENAME = "config.yaml"

@dataclass(frozen=True)
class CopilotProfile:
    """A named Copilot backend configuration."""

    name: str
    label: str
    env: dict[str, str] = field(default_factory=dict)
    copilot_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SessionBackendConfig:
    """Machine-local interactive session host selection."""

    kind: str = "direct"
    endpoint_url: str = ""
    github_account: str = ""
    protocol_versions: tuple[str, ...] = ("0.7.0",)
    auth_resource: str = "https://api.github.com"
    connect_timeout_seconds: float = 15.0

    @property
    def is_ahp(self) -> bool:
        return self.kind == "ahp"


@dataclass(frozen=True)
class ProfileAssignmentPolicy:
    """Effective user-armed policy over existing named Copilot profiles."""

    name: str = ""
    mode: str = ""
    armed: bool = False
    profiles: tuple[str, ...] = ()
    assignment_label: str = ""
    eligible_lanes: tuple[str, ...] = ("new", "handoff-cutover")
    error: str = ""
    repository_error: str = ""


# Synthetic default when no profiles are configured.
DEFAULT_PROFILE = CopilotProfile(name="cloud", label="☁️  Cloud (GitHub)")


@dataclass(frozen=True)
class PRConfig:
    """Pull-request workflow configuration for a managed repo.

    When ``enabled`` is false (the default), the repo uses the direct-push
    finalization flow unchanged.  When enabled, finalization runs the
    PR-based flow (see ``docs/plans/pr-workflow.md`` in test-chamber).

    ``required`` is the enforcement switch.  With ``enabled`` alone, PR mode
    is *available* but optional: ``push-changes``/``finalize`` only take the
    PR path once a PR record exists (a ``create-pr`` was run), otherwise they
    finalize direct-to-master.  With ``required: true`` the direct-to-master
    path is **refused** -- ``push-changes`` (and the unmerged-work guard in
    ``finalize``) will not push to the default branch; the only way to land
    work is ``create-pr`` -> open PR -> merge.  Setting ``required: true``
    implies ``enabled: true``.

    ``strategy`` selects the default *disposition* after ``create-pr`` --
    it does not control squash timing (squashing always happens at
    ``create-pr``):

    - ``detach``    -- finalize the worktree immediately; resume later via a
                       fresh ``create`` workflow if the PR needs more work.
    - ``keep-alive`` -- keep the worktree open to iterate on review feedback,
                        pushing updates to the feature branch.
    """

    enabled: bool = False
    required: bool = False         # enforce PRs: refuse direct-to-master
    provider: str = "gitea"        # gitea | github | azure-devops
    strategy: str = "detach"       # default disposition: keep-alive | detach
    branch_prefix: str = "feature"
    # ``head_scheme`` selects how create-pr *publishes* the PR head (#1815) --
    # its NAME + push mechanism. It does NOT change the local worktree, which
    # ALWAYS lands on the squashed commit: HEAD stays on ``worktree/<id>`` and
    # the branch is never reset off it (#1804), under either scheme.
    #
    # - ``refspec`` (default, #1815/#1899) -- push ``worktree/<id>`` directly to
    #   the PR head ref via a refspec (no local feature branch; PR head named
    #   ``pr/<slug>``). Requires every PR-mode repo's pre-push hook to allow the
    #   mediated refspec push (a multi-machine system hook that blocks ``worktree/*`` by ref
    #   name must honor ``AGENT_WORKTREES_PR_PUSH=1`` first -- see test-chamber
    #   #1815/#1889). A parallel ``--new`` PR auto-falls-back to a snapshot ref.
    # - ``snapshot`` (legacy/compatible) -- copy the squashed commit onto a
    #   separate local ``{prefix}/<slug>`` branch (older ``feature/`` namespace)
    #   and push that. Needs no pre-push-hook cooperation, so it is the safe
    #   opt-out for a repo whose hook still blocks the refspec push.
    #   ``worktree/<id>`` keeps the squashed commit either way (sits ahead of
    #   master while the PR is open; a later ``git sync`` reconciles it on merge).
    #
    # ``head_pattern`` is the PR head-name template (tokens ``{prefix}``,
    # ``{slug}``, ``{suffix}``, ``{username}``, ``{machine}``). Empty means the
    # scheme default: ``pr/{slug}-{suffix}`` under ``refspec`` and
    # ``{prefix}/{slug}-{suffix}`` under ``snapshot`` (``feature/<slug>``).
    # Repos that want e.g. ``user/<username>/<slug>-<suffix>`` set it explicitly.
    head_scheme: str = "refspec"   # refspec (default) | snapshot
    head_pattern: str = ""         # empty -> scheme default (see above)
    # Provider-plugin settings (PR creation via a provider CLI). ``api_base``
    # is the hosting endpoint -- required for self-hosted Gitea
    # (e.g. https://host/gitea) and Azure DevOps org URLs; GitHub defaults to
    # the public API. ``token_command`` (a shell command that prints a token,
    # e.g. a vault fetch) takes precedence over ``token_env`` (an env var
    # name); GitHub falls back to ``gh`` auth when neither is set. ``labels``
    # are applied to every opened PR (``{machine}`` is templated). ``auto_open``
    # is **opt-in** (default False): only when a repo sets it true does
    # ``create-pr`` open the PR via the provider; otherwise the branch is just
    # pushed and PR creation is left to the agent (manual flow).
    api_base: str = ""
    token_env: str = ""
    token_command: str = ""
    labels: tuple[str, ...] = ()
    auto_open: bool = False        # opt-in: open the PR via the provider after push
    # Embed raw worktree/machine/session provenance in a hidden PR-body marker.
    # Closed-circuit systems may opt in; public repos should leave this false.
    source_attribution: bool = False
    # Markdown headings whose sections must contain visible text before
    # create-pr may auto-open a PR. Empty keeps the generic default permissive.
    required_body_sections: tuple[str, ...] = ()
    # Review-vocabulary binding (the "multi-machine system hook" for the pr-* command
    # family: pr-watch / pr-merge / pr-status). The plugin ships these EMPTY so
    # it stays provider-generic -- a repo with no binding gets a no-op, never a
    # crash. The multi-machine system supplies its vocabulary in .agent-worktrees/config.yaml
    # (e.g. automerge_label: auto-merge; hold_labels: [do-not-merge,
    # needs-rebase, wip]; wip_title_prefixes: ["wip:", "[wip]", "draft:", ...]).
    #
    # - ``automerge_label``    -- the label whose presence signals MERGE CONSENT
    #   (pr-merge applies it after an approval; think ADO's "auto-complete").
    #   Empty => no auto-merge mechanism is configured, so pr-merge declines
    #   rather than guessing a label. "Consent" is the *concept*; this field
    #   names the concrete *label* that expresses it.
    # - ``hold_labels``        -- labels that BLOCK consent/merge (an explicit
    #   hold, or a state needing author action such as a rebase).
    # - ``wip_title_prefixes`` -- case-insensitive title prefixes treated as
    #   work-in-progress (never eligible for consent).
    #
    # Verdict semantics (approve / request-changes / comment) are NOT a binding:
    # they are intrinsic to the review backend and live in the provider. A
    # ``review:*`` status tag needs no binding -- it is simply neither the
    # auto-merge label nor a hold label, so the classifier ignores it.
    automerge_label: str = ""
    hold_labels: tuple[str, ...] = ()
    wip_title_prefixes: tuple[str, ...] = ()
    # ── Auto-complete completion policy (consumed when pr-merge requests
    # auto-complete). ``automerge_label`` is the abstract "merge consent /
    # auto-complete requested" marker: gitea/github apply it as a real label;
    # Azure DevOps has *native* auto-complete (no label) and emits the marker in
    # a snapshot only once auto-complete is set. These knobs shape ADO's native
    # completion and the eligibility gate:
    #
    # - ``approval_required`` -- must a PR be APPROVED before pr-merge requests
    #   completion? True (default) preserves the review-gated shape. False suits
    #   a self-complete repo (we own the merge): eligible when simply not
    #   changes-requested (no approval vote needed).
    # - ``allow_stale_approval`` -- may an approval submitted against an older
    #   head still authorize completion when the live PR is otherwise mergeable?
    #   False by default; enable only where repository policy permits it.
    # - ``squash`` / ``delete_source_branch`` -- ADO auto-complete options.
    # - ``bypass_policy`` / ``bypass_reason`` -- complete PAST branch policies
    #   (for a default branch whose policy never auto-satisfies for our own PRs,
    #   e.g. a central governance status policy). ADO-only; ignored elsewhere.
    approval_required: bool = True
    allow_stale_approval: bool = False
    squash: bool = True
    delete_source_branch: bool = True
    bypass_policy: bool = False
    bypass_reason: str = ""
    # ── PR-flow legibility matrix (surfaced as agent reminders by the pr-*
    # verbs via ``pr_contract.pr_reminder``). These describe *who does what* so
    # a calling agent is reminded of THIS repo's rules + correct next step and
    # never has to remember or guess the per-repo flow. All optional + defaulted
    # -> backward-compatible; empty means "derive / unknown" and the classifier
    # falls back to today's behavior.
    #
    # - ``reviewer``        -- who reviews: ``"copilot"`` (a bot PR reviewer),
    #   ``"agent:<name>"`` (a designated reviewer agent), ``"external"``, or
    #   ``""`` (none/unknown).
    # - ``review_blocking`` -- is the review a merge GATE (must approve) rather
    #   than a non-blocking advisory pass?
    # - ``review_latency_hint`` -- rough human hint for how long review takes
    #   (e.g. ``"~2m"``), shown by pr-watch so an agent knows how long to wait.
    # - ``self_approve``    -- legacy provider capability: may the submitter
    #   satisfy a provider-specific approval step? This (or ``merge_actor:
    #   submitter-direct``) selects the ``pr-self-merge`` profile. It never
    #   means a GitHub author should cast an approving review on their own PR.
    # - ``merge_actor``     -- how the merge is performed. **Only
    #   ``"submitter-direct"`` is consumed as an input today**: it (or
    #   ``self_approve``) selects the ``pr-self-merge`` profile. The other modes
    #   a flow can report -- ``reviewer`` (the reviewer merges) and
    #   ``consent-gate`` (a consent label triggers the gate) -- are **derived**
    #   from ``automerge_label`` / ``self_approve`` by ``classify_pr_flow`` and
    #   are not (yet) accepted here; setting them has no effect. Leave ``""`` to
    #   derive the mode.
    # - ``conflict_retriggers_review`` -- does a post-approval rebase+push send
    #   the PR back through review? (informs the conflict-path reminder.)
    reviewer: str = ""
    review_blocking: bool = False
    review_latency_hint: str = ""
    self_approve: bool = False
    merge_actor: str = ""
    conflict_retriggers_review: bool = True
    # ── Merge/update policy defaults (repo-overridable; #225). Express the
    # multi-machine system's default PR-flow policy so every pr-* surface reports it and the
    # update/merge paths honor it. All optional + defaulted -> backward
    # compatible. Provider-neutral: each provider maps the policy or reports
    # "unsupported" and falls back.
    #
    # - ``branch_update_strategy`` -- how a PR branch is brought current against
    #   a moved base: ``"rebase"`` (default; linear history, the tool's
    #   squash-then-force-push model) or ``"merge"`` (merge the base in, for a
    #   repo that forbids force-pushes / rebases).
    # - ``merge_strategy`` -- how the final merge is performed: ``"squash"``
    #   (default) | ``"merge"`` | ``"rebase"``. Honors a repo that only allows a
    #   subset of methods. (Distinct from the ADO auto-complete ``squash`` knob
    #   above, which shapes ADO's native completion options.)
    # - ``prefer_auto_merge`` -- when True (default), pr-merge prefers the
    #   provider's CI-gated native auto-merge/auto-complete over an immediate
    #   merge, so the merge waits on required checks; falls back to a direct
    #   merge where the provider offers no auto-merge.
    branch_update_strategy: str = "rebase"
    merge_strategy: str = "squash"
    prefer_auto_merge: bool = True


@dataclass(frozen=True)
class RepoConfig:
    """Configuration for a single managed repository."""

    anchor: str
    worktree_root: str
    default_branch: str = "master"
    remote: str = "origin"
    launch: dict[str, list[str]] = field(default_factory=dict)
    launch_recovery: dict[str, list[str]] = field(default_factory=dict)
    copilot_path: dict[str, str] = field(default_factory=dict)
    """Optional Copilot executable, keyed by platform ("windows" / "linux").
    The normalized launcher uses this instead of the ``copilot`` command on
    PATH. Values may use the standard launch placeholders. Declaring it forces
    the normalized launcher so the selection composes with setup hooks,
    environment priming, profiles, resume, and ACP launches. An explicit
    ``launch`` template remains authoritative and ignores this setting."""
    setup_hook: dict[str, str] = field(default_factory=dict)
    """Optional repo **session setup hook**, keyed by platform ("windows" /
    "linux"). The value is a path to a script (relative to ``anchor`` unless
    absolute) that agent-worktrees' normalized launcher runs -- passing context
    by argument (``-Machine`` / ``-Recovery``), not ambient env -- *before* it
    execs Copilot. The hook does repo-specific work (vault, MCP) and returns; it
    does NOT launch Copilot itself. Declaring it opts the repo into the
    normalized launch flow (inverting the legacy ``setup.ps1``-as-launch)."""
    env_script: dict[str, str] = field(default_factory=dict)
    """Optional repo **environment-priming script**, keyed by platform
    ("windows" / "linux"). The value is a path to a shell/batch script (relative
    to ``anchor`` unless absolute) that the normalized launcher runs and whose
    *resulting environment is captured and applied to the Copilot exec*. This is
    the crucial difference from ``setup_hook``: a setup hook runs as a **child
    process** so its env is discarded, whereas ``env_script`` is sourced/called
    in the launcher's own shell (Windows: ``call <script>`` then snapshot ``set``;
    POSIX: ``source`` with ``set -a``) so the vars it exports reach Copilot. It
    exists for **Windows enlistment-style repos** whose build tooling only works
    inside an environment a setup script establishes dynamically (e.g. an
    Office/SPO ``OpenEnlistment.bat`` that sets OTOOLS/VC++/SDK vars + PATH). A
    plain ``copilot`` in such a repo can read code but cannot build; declaring
    ``env_script`` makes the base-repo agent build-ready with no hand-authored
    launch wrapper. Runs on every launch **including recovery** (the build env is
    always needed) and opts the repo into the normalized launcher (like
    ``setup_hook``); it is ignored when an explicit ``launch`` template is set."""
    session_path: dict[str, list[str]] = field(default_factory=dict)
    """Optional directories the normalized launcher prepends to ``PATH`` before
    launch, keyed by platform. Each entry is templated (``{work_dir}``,
    ``{anchor}``, ``{machine}``, ``{repo_name}``) -- e.g.
    ``["{work_dir}/tools/bin"]``. The generic mechanism that lets a repo expose
    its tool binstubs without an ambient PATH export in a setup script."""
    session_env: dict[str, str] = field(default_factory=dict)
    """Optional environment variables the launch plan applies to the Copilot
    session (e.g. ``COPILOT_FEATURE_FLAGS``). Merged into the plan ``env`` by
    ``_build_env`` (below the profile, so a profile can override). This is how a
    repo contributes session env **without** an ambient export in a setup script
    -- and it works with the normalized launcher, where the repo setup hook runs
    as a child process and therefore cannot set env for the Copilot exec."""
    validate_paths: list[str] = field(default_factory=list)
    validate_hook: dict[str, list[str]] = field(default_factory=dict)
    service_paths: list[str] = field(default_factory=list)
    post_install_hook: dict[str, list[str]] = field(default_factory=dict)
    pr: PRConfig = field(default_factory=PRConfig)
    base_repo: bool = False
    """When true, this repo is driven in **base-repo (no-worktree)** mode: the
    anchor checkout is used directly and no worktree is ever created. Used to
    adopt repos that do not support worktrees (e.g. an enlistment-based monorepo)
    so agent-bridge can launch an ACP agent against the anchor via a custom
    ``launch`` command. Configured entirely from the user-local
    ``~/.<project>/config.yaml`` overlay -- nothing is written into the repo."""
    stateless: bool = False
    """When true, this repo is a **stateless harness**: it holds the shareable
    control-plane intelligence (instructions, config, skills, sub-agents) but no
    personal state (efforts, logs, visions, artifacts). Personal state is routed
    to a separately-bound **knowledge repo** (top-level ``knowledge_repo`` in the
    machine-local config; see :attr:`Config.knowledge_repo`). Consumed by the
    state-root resolver (``agent-worktrees state-root``) so effort/vision/log
    plugins default their writes into the bound knowledge checkout instead of the
    harness tree. Declared in the repo's own committed
    ``<anchor>/.agent-worktrees/config.yaml`` (``stateless: true``) -- it is a
    property of the harness, not of a machine. **Implies**
    :attr:`requires_external_state_root` (a stateless harness by definition
    requires an external state root). See the ``stateless-harness`` vision /
    ``citadel-harness-split`` effort."""
    requires_external_state_root: bool = False
    """When true, personal state (efforts/visions/logs) **must** be written to a
    separately-bound external **knowledge repo**, not into this repo's checkout.
    The state-root resolver routes to the bound knowledge repo and **refuses**
    (rather than falling back to the launch repo) when nothing is bound. This is
    the explicit, plugin-facing knob the ``efforts`` and ``visions`` plugins key
    on; it is **decoupled** from :attr:`stateless` (a repo can require external
    state without being a fully guarded/linted stateless harness), but
    ``stateless: true`` **implies** it, so a harness never has to set both.
    **Default false** -- a normal repo is its own state home (fully
    backward-compatible). Declared in the repo's committed
    ``<anchor>/.agent-worktrees/config.yaml``."""


@dataclass(frozen=True)
class Config:
    """Top-level project configuration."""

    srcroot: str
    machine: str
    platform: str
    repo_name: str = ""
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    knowledge_repo: str = ""
    """Name of the **knowledge repo** this machine routes personal state to when
    the launch repo is a **stateless harness** (see :attr:`RepoConfig.stateless`).
    Machine-local only -- carried in ``~/.<project>/config.yaml`` (top-level
    ``knowledge_repo:``), never committed into the shareable harness tree, so a
    fork or a different operator changes only their own machine-local pointer.
    Name-only: the checkout path is resolved via the repos registry
    (``agent-worktrees repos find <name>``). Consumed by the state-root resolver.
    Empty means unbound -- the resolver then refuses to silently write state into
    the harness. See the ``stateless-harness`` vision."""
    copilot_profiles: list[CopilotProfile] = field(default_factory=list)
    session_backend: SessionBackendConfig = field(
        default_factory=SessionBackendConfig
    )
    profile_assignment: ProfileAssignmentPolicy | None = None
    headless: bool = False
    """When true, the project is driven via CLI only -- its bare binstub
    invocation lists worktrees instead of launching an interactive Copilot
    session. Used to control external repos (e.g. copilot-extensions) whose
    worktree lifecycle is managed from another project's session."""
    auto_fast_forward: bool = True
    """When true (the default), resuming a clean worktree that is strictly
    behind its upstream default branch fast-forwards it before launch, so
    the session and setup script see an up-to-date tree.  Only ever a
    fast-forward (clean + no local commits ahead); dirty/ahead/diverged
    worktrees are left untouched.  Set false to opt out of auto-update."""
    new_picker: bool = True
    """Whether the bare binstub launches the overhauled Textual worktree picker.
    **True by default** -- the Textual picker is the default everywhere; no
    opt-in is needed.  ``picker disable`` writes ``new_picker: false`` to opt a
    machine *out* to the legacy ANSI picker (persistent, machine-local > global);
    ``picker enable`` restores the default.  The env vars still override for a
    single invocation: ``AGENT_WORKTREES_LEGACY_PICKER`` forces the legacy picker
    (rollback) and ``AGENT_WORKTREES_NEW_PICKER`` forces the new one.  (Windows
    over SSH always auto-falls-back to legacy -- see _new_picker_blocked_by_ssh.)"""

    @property
    def default_repo(self) -> RepoConfig:
        """Return the default repo for this project.

        Looks up ``self.repo_name`` in the repos map first, then falls
        back to the sole entry if there is exactly one repo.  Raises
        ``KeyError`` otherwise.
        """
        if self.repo_name in self.repos:
            return self.repos[self.repo_name]
        if len(self.repos) == 1:
            return next(iter(self.repos.values()))
        raise KeyError(
            f"No repo '{self.repo_name}' in config and multiple repos defined. "
            f"Available: {', '.join(self.repos)}"
        )

# --- Machine registry ---

@dataclass(frozen=True)
class SSHEnvironment:
    """An SSH environment for a machine (windows, wsl, linux)."""

    name: str
    alias: str
    shell: str = ""


@dataclass(frozen=True)
class MachineEntry:
    """A registered machine from machines.yaml."""

    key: str
    display_name: str
    environment: str
    alias: str = ""
    # Raw OS hostname (COMPUTERNAME) when it differs from ``key``. Lets a machine
    # be keyed by a stable friendly name (e.g. ``host-augloop1``) while the box
    # reports a different, unrenameable COMPUTERNAME (e.g. ``cpc-tmich-oixui``).
    # Empty means ``key`` is the hostname (the common case).
    hostname: str = ""
    role: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    ssh_environments: list[SSHEnvironment] = field(default_factory=list)
    ssh_ready: bool = False
    copilot: bool = True


def machines_yaml_path(repo_dir: str | Path) -> Path:
    """Resolve the ``machines.yaml`` path for a repo.

    Canonical location is the in-repo ``.agent-worktrees/machines.yaml`` (agent-*
    plugin config belongs in the plugin's ``.agent-*/`` folder, alongside
    ``config.yaml`` / ``related.yaml``). Falls back to the legacy repo-root
    ``machines.yaml`` when the canonical file is absent, so existing repos keep
    working until migrated. Prefers the canonical path when both exist.

    **Knowledge overlay (config-graft, E1e):** when ``repo_dir`` is the **stateless
    launch harness** and has no ``machines.yaml`` of its own, redirect to the bound
    **knowledge repo's** ``machines.yaml`` -- the machine topology is personal
    reference config that lives in the knowledge repo, not the shareable harness
    tree. This is the config-READ axis (distinct from the state-root, the
    personal-state *write* destination); it makes every ``load_machines_yaml``
    caller knowledge-aware without changing its call. The redirect only fires for
    the launch/default repo, never an arbitrary ``repo_dir`` (a product repo with
    no ``machines.yaml`` still reports its own canonical path, unchanged).
    """
    canonical = Path(repo_dir) / INREPO_CONFIG_DIRNAME / "machines.yaml"
    if canonical.exists():
        return canonical
    legacy = Path(repo_dir) / "machines.yaml"
    if legacy.exists():
        return legacy
    overlay = _overlay_machines_yaml_path(repo_dir)
    if overlay is not None:
        return overlay
    return canonical  # non-existent: report the canonical path in errors


def _overlay_machines_yaml_path(repo_dir: str | Path) -> Path | None:
    """Resolve the knowledge-repo overlay's ``machines.yaml`` for a stateless
    launch harness, via the state-root config-source seam. Fail-safe -> ``None``.

    Only redirects when ``repo_dir`` is the launch/default repo AND that repo
    requires an external state root -- so a plain product repo without a
    ``machines.yaml`` is never silently redirected.
    """
    try:
        from . import state_root
        config = load_config()
        try:
            default_anchor = config.default_repo.anchor
        except KeyError:
            return None
        if not default_anchor or os.path.abspath(default_anchor) != os.path.abspath(
            str(repo_dir)
        ):
            return None
        srcs = state_root.config_source_anchors(config, base_anchor=str(repo_dir))
    except Exception:
        return None
    # Walk the overlay sources (everything past the base) for a machines.yaml;
    # the knowledge overlay wins.
    for src in srcs:
        if os.path.abspath(src.anchor) == os.path.abspath(str(repo_dir)):
            continue
        cand = Path(src.anchor) / INREPO_CONFIG_DIRNAME / "machines.yaml"
        if cand.exists():
            return cand
        legacy = Path(src.anchor) / "machines.yaml"
        if legacy.exists():
            return legacy
    return None


def load_machines_yaml(repo_dir: str | Path) -> dict[str, MachineEntry]:
    """Load the machine registry from ``machines.yaml``.

    Reads the canonical ``<repo>/.agent-worktrees/machines.yaml`` (falling back to
    the legacy repo-root ``<repo>/machines.yaml`` -- see :func:`machines_yaml_path`).
    Returns a dict mapping machine key → MachineEntry.
    Raises FileNotFoundError if machines.yaml is missing.
    """
    path = machines_yaml_path(repo_dir)
    if not path.exists():
        raise FileNotFoundError(f"Machine registry not found at {path}")

    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    if not raw or "machines" not in raw:
        raise ValueError(f"machines.yaml at {path} is missing 'machines' key")

    entries: dict[str, MachineEntry] = {}
    for key, data in raw["machines"].items():
        if not isinstance(data, dict):
            continue
        description_raw = data.get("description", "")
        if description_raw is None:
            description_raw = ""
        if not isinstance(description_raw, str):
            raise ValueError(
                f"machine '{key}' description must be a string"
            )
        capabilities_raw = data.get("capabilities", [])
        if capabilities_raw is None:
            capabilities_raw = []
        if not isinstance(capabilities_raw, list):
            raise ValueError(
                f"machine '{key}' capabilities must be a list"
            )
        capabilities: list[str] = []
        for capability_raw in capabilities_raw:
            if not isinstance(capability_raw, str):
                raise ValueError(
                    f"machine '{key}' capabilities must contain only strings"
                )
            capability = capability_raw.strip()
            if capability and capability not in capabilities:
                capabilities.append(capability)
        ssh_envs: list[SSHEnvironment] = []
        ssh_block = data.get("ssh", {})
        if ssh_block is None:
            ssh_block = {}
        if not isinstance(ssh_block, dict):
            raise ValueError(f"machine '{key}' ssh must be a mapping")
        for env in ssh_block.get("environments", []):
            if isinstance(env, dict) and "name" in env and "alias" in env:
                ssh_envs.append(SSHEnvironment(
                    name=env["name"], alias=env["alias"],
                    shell=env.get("shell", ""),
                ))
        entries[key] = MachineEntry(
            key=key,
            display_name=data.get("display_name", key),
            environment=data.get("environment", ""),
            alias=data.get("alias", ""),
            hostname=data.get("hostname", ""),
            role=data.get("role", ""),
            description=description_raw.strip(),
            capabilities=capabilities,
            ssh_environments=ssh_envs,
            ssh_ready=bool(ssh_block.get("ready", False)),
            copilot=bool(data.get("copilot", True)),
        )
    return entries


def machine_name(entry: MachineEntry) -> str:
    """Return the canonical name for a machine entry.

    Returns the alias if one is defined (the colloquial multi-machine system name),
    otherwise the key (which is the real hostname).
    """
    return entry.alias or entry.key


def find_machine_entry(
    entries: dict[str, MachineEntry], name: str,
) -> MachineEntry | None:
    """Look up a machine by key, alias, ``hostname`` field, or display_name
    (case-insensitive).

    Hostnames are case-insensitive, so match without regard to case (Windows
    reports COMPUTERNAME in mixed case but tooling often lowercases it). Matching
    the explicit ``hostname`` field lets a machine keyed by a friendly name still
    be found by its raw COMPUTERNAME. Returns None if no entry matches.
    """
    if name in entries:
        return entries[name]
    name_lower = name.lower()
    for key, entry in entries.items():
        if key.lower() == name_lower:
            return entry
        if entry.alias and entry.alias.lower() == name_lower:
            return entry
        if entry.hostname and entry.hostname.lower() == name_lower:
            return entry
        if entry.display_name and entry.display_name.lower() == name_lower:
            return entry
    return None


def detect_machine(repo_dir: str | Path | None = None) -> str:
    """Auto-detect machine name from hostname.

    If *repo_dir* is provided, reads ``machines.yaml`` and matches
    the COMPUTERNAME against machine keys, the explicit ``hostname`` field, and
    aliases (exact match). Returns the canonical name (alias if set, otherwise
    key). Falls back to the raw hostname if no registry is available.
    """
    hostname = socket.gethostname().lower()

    if repo_dir is not None:
        try:
            entries = load_machines_yaml(repo_dir)
            # Exact match on key (real hostname) first -- case-insensitive
            for key, entry in entries.items():
                if hostname == key.lower():
                    return machine_name(entry)
            # Then the explicit ``hostname`` field (key decoupled from COMPUTERNAME)
            for entry in entries.values():
                if entry.hostname and hostname == entry.hostname.lower():
                    return machine_name(entry)
            # Then check aliases
            for entry in entries.values():
                if entry.alias and hostname == entry.alias.lower():
                    return machine_name(entry)
        except (FileNotFoundError, ValueError):
            pass  # no registry -- fall through to raw hostname

    return hostname


def render_copilot_instructions(
    entry: MachineEntry, project: str = "",
) -> str:
    """Render the content of ``machine.instructions.md`` for a machine.

    Detects the current platform and includes it along with the
    deployment environment (SSH alias) so agents know their exact
    identity for service deployments.  When *project* is provided,
    includes project and binstub metadata.
    """
    plat = detect_platform()

    # Find the SSH alias matching the current platform
    deploy_env = ""
    for ssh_env in entry.ssh_environments:
        if ssh_env.name == plat:
            deploy_env = ssh_env.alias
            break

    lines = [
        f"Machine: {entry.display_name}",
        f"Hostname: {entry.key}",
        f"Environment: {entry.environment}",
        f"Platform: {plat}",
    ]
    if deploy_env:
        lines.append(f"Deployment environment: {deploy_env}")
    if entry.role:
        lines.append(f"Role: {entry.role}")
    if entry.description:
        lines.append(f"Description: {entry.description}")
    if entry.capabilities:
        lines.append(f"Capabilities: {', '.join(entry.capabilities)}")
    if project:
        lines.append(f"Project: {project}")
        lines.append(f"Binstub: {project}")
    return "\n".join(lines) + "\n"


def detect_platform() -> str:
    """Detect the current platform: 'windows', 'wsl', or 'linux'."""
    if platform.system() == "Windows":
        return "windows"
    # WSL detection
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def _home() -> Path:
    """Cross-platform home directory, with a sandbox-root override.

    ``AGENT_HOME`` (when set) replaces ``~/`` as the root under which the whole
    agent-* state tree lives -- ``~/.agent-worktrees``, ``~/.<project>``,
    ``~/.copilot/installed-plugins``, the pivots dir, etc. It relocates *only*
    that harness state, deliberately **not** the real home: ``gh``/``ssh``/git
    credentials still resolve from the actual ``~/`` (via ``USERPROFILE``/
    ``HOME``), so a sandbox can run the live stack against real auth without
    stomping -- or being stomped by -- the active deployment. This is the seam
    the ``preview-picker`` harness and any isolated test deployment use.
    """
    override = os.environ.get("AGENT_HOME", "").strip()
    if override:
        return Path(override)
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home())))
    return Path.home()


# ── Active project (in-process, git-like context) ───────────────────────
# The active project is resolved once per invocation from the current working
# directory (or an explicit ``--project``) and threaded in-process here -- it is
# NOT read from ambient environment variables. ``main()`` sets it after CWD/flag
# resolution; every consumer reads it through ``project_name()``.
#
# There is deliberately no "assumed CWD" override: when ``--project`` targets a
# project the caller is not already inside, ``main()`` performs a real
# ``os.chdir`` to that project's anchor (git ``-C`` semantics), so *every* code
# path -- worktree-id inference, repo discovery, git subprocesses -- resolves
# consistently from the process's actual working directory.
_ACTIVE_PROJECT: str | None = None


def set_active_project(name: str | None) -> None:
    """Set the in-process active project (resolved from CWD or ``--project``)."""
    global _ACTIVE_PROJECT
    _ACTIVE_PROJECT = name.strip() if name else None


def active_project() -> str | None:
    """Return the in-process active project name, or ``None`` if unresolved."""
    return _ACTIVE_PROJECT


def project_name() -> str:
    """Return the in-process active project name (resolved from CWD or ``--project``).

    Resolution is git-like: the active project is derived from the current
    directory (or an explicit ``--project``) by ``main()`` and threaded in
    process -- it is **not** read from ``$WORKTREE_PROJECT`` (the transitional
    env fallback was retired once the shell layer migrated to CWD/``--project``;
    cwd-resolution effort Phase 3). Raises ``RuntimeError`` when no project could
    be resolved -- callers that must render without context (e.g. the ``repos``
    usage banner) catch it and fall back to the generic binstub name.
    """
    name = (_ACTIVE_PROJECT or "").strip()
    if not name:
        raise RuntimeError(
            "No active project could be resolved. agent-worktrees discovers its "
            "context from the current directory (like git); run from inside a "
            "managed repo or worktree, or pass --project <name>."
        )
    if not _PROJECT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid project name: {name!r}. "
            "Must be 1-64 alphanumeric/dash/dot/underscore characters."
        )
    return name


def install_dir() -> Path:
    """Shared runtime root (``~/.agent-worktrees/``)."""
    return _home() / ".agent-worktrees"


def project_dir(name: str | None = None) -> Path:
    """Per-project config/state root (``~/.{name}/``)."""
    return _home() / f".{name or project_name()}"


def default_config_path() -> Path:
    """Return the machine-local config path for the active project."""
    return project_dir() / "config.yaml"


def global_config_path() -> Path:
    """Return the global, machine-wide config path (lowest config tier)."""
    return install_dir() / GLOBAL_CONFIG_FILENAME


def inrepo_config_path(anchor: str | Path) -> Path:
    """Return the preferred in-repo config path (directory form) for an anchor."""
    return Path(anchor) / INREPO_CONFIG_DIRNAME / GLOBAL_CONFIG_FILENAME


def load_config(
    path: Path | None = None,
    *,
    include_control_plane_related_pr: bool = True,
    project: str | None = None,
) -> Config:
    """Load and parse the layered project config.

    Merges these tiers (highest precedence wins):

    1. ``~/.<project>/config.yaml`` (machine-local; ``path``) -- per-machine,
       per-repo overrides and machine paths. **Optional**: a repo designed for
       this system carries its settings in-repo and needs no machine-local
       file; machine-local config is the adapter that makes *foreign* repos
       compatible.
    1b. **Knowledge overlay (E1e, #947)** -- for a **stateless harness** bound to
       a knowledge repo, the knowledge repo's ``config.yaml`` contributes portable
       **operator-preference** keys (``copilot_profiles``/``headless``/
       ``auto_fast_forward``/``new_picker``) as a tier BETWEEN the in-repo base and
       machine-local, so those prefs can be versioned in the knowledge repo while
       machine-local stays minimal. Machine-specifics + the binding never graft;
       ``{}`` (no effect) for a normal repo.
    2. ``<anchor>/.agent-worktrees/config.yaml`` (in-repo; the repo's own
       committed config -- the base for its settings).
    3. ``~/.agent-worktrees/config.yaml`` (global; machine-wide defaults).

    Top-level fields (``srcroot``/``machine``/``platform``/``copilot_profiles``
    /``headless``/``auto_fast_forward``) resolve machine-local > global >
    detected -- with the knowledge overlay inserted just under machine-local for
    the portable operator-preference keys only. Per-repo settings merge in-repo
    flat settings < machine-local ``repos.<name>`` block (the global tier carries
    no per-repo settings). Anchors come from the machine-local file or, when
    absent, from ``~/.agent-worktrees/repos.yaml``.

    Args:
        path: Machine-local config path. Uses the default if None.
        include_control_plane_related_pr: Include foreign-repo PR overlays from
            active harness plugins. Related-index bootstrap disables this to
            avoid resolving the same active-plugin corpus twice.
        project: Explicit project identity. When supplied, it overrides legacy
            machine/global ``repo_name`` values for this load.

    Returns:
        Parsed Config object.

    Raises:
        ValueError: If no repo can be resolved (no machine-local repos and no
            registry anchor for the active project).
    """
    if path is None:
        path = default_config_path()

    # Tier 1 (lowest): global machine-wide defaults. Lazily migrate the parsed
    # doc to the current schema in memory (never persists here; never raises) so
    # a still-old ~/.agent-worktrees/config.yaml loads correctly before an
    # install/update has rewritten it.
    global_raw = config_migrations.migrate_loaded(
        _load_yaml_safe(global_config_path()), config_migrations.SCHEMA_CONFIG
    )
    # Tier 3 (highest): machine-local. Optional -- absent is fine. Service
    # config-drop-ins (``<config-dir>/config.d/*.yaml``) form a base UNDER the
    # machine-local ``config.yaml`` (so an explicit config.yaml still wins),
    # letting a service register machine-local settings -- e.g. the vault
    # contributing ``session_env.SUDO_ASKPASS`` -- without editing the shared
    # config.yaml. Drop-ins deep-merge with everything else, so a per-repo
    # ``session_env`` addition survives alongside the repo's own keys.
    machine_raw = _load_yaml_safe(path)
    preliminary_repo_name = (
        project
        or machine_raw.get("repo_name")
        or global_raw.get("repo_name")
        or _project_name_safe()
    )
    dropins = _load_config_d(
        path.parent / "config.d",
        project_name=str(preliminary_repo_name or ""),
        warn=True,
    )
    if dropins:
        machine_raw = _deep_merge(dropins, machine_raw)

    # Resolved top-level fields: machine-local > global > detected.
    platform = (
        machine_raw.get("platform")
        or global_raw.get("platform")
        or detect_platform()
    )
    machine = (
        machine_raw.get("machine")
        or global_raw.get("machine")
        or detect_machine()
    )
    srcroot = machine_raw.get("srcroot") or global_raw.get("srcroot") or ""

    # Active project / default repo name.
    repo_name = (
        project
        or machine_raw.get("repo_name")
        or global_raw.get("repo_name")
        or _project_name_safe()
    )

    # E1e config.yaml knowledge overlay (#947): when the launch repo is a
    # STATELESS harness bound to a knowledge repo, layer the knowledge repo's
    # config.yaml as a tier BETWEEN the in-repo base and machine-local -- but only
    # for portable operator-preference keys (never machine-specifics or the
    # binding). Gated + fail-open -> ``{}`` for a normal (non-stateless) repo, so
    # the hot config path is unchanged off the stateless track.
    knowledge_raw = _load_knowledge_overlay_config(
        machine_raw, global_raw, repo_name, platform
    )

    machine_repos = machine_raw.get("repos") or {}
    if not isinstance(machine_repos, dict):
        machine_repos = {}

    # Control-plane related.yaml (+ ``<repo>-harness`` plugin) ``pr:`` overlays,
    # computed once: a checked-in control-plane view of how to land PRs in a
    # FOREIGN repo, layered per-repo below. Fail-safe -> ``{}``.
    cp_related_pr = (
        _control_plane_related_pr_map()
        if include_control_plane_related_pr
        else {}
    )

    # Build the set of repos to resolve: those named in the machine-local file,
    # plus the active project (so a convention-adopted repo with no
    # machine-local block still loads, with its anchor from the registry).
    names = [n for n in machine_repos if isinstance(machine_repos[n], dict)]
    if repo_name and repo_name not in names:
        names.append(repo_name)

    repos: dict[str, RepoConfig] = {}
    inrepo_profile_assignments: dict[str, Any] = {}
    for name in names:
        machine_repo = machine_repos.get(name) or {}
        if not isinstance(machine_repo, dict):
            machine_repo = {}

        anchor = machine_repo.get("anchor") or _resolve_anchor_from_registry(
            name, platform
        )
        if not anchor:
            # No anchor anywhere -- can't manage this repo. Skip silently unless
            # it was the only candidate (validated after the loop).
            continue

        worktree_root = machine_repo.get("worktree_root") or derive_worktree_root(
            anchor
        )

        # Tier 2: the repo's own in-repo flat settings (base for repo settings).
        inrepo_settings = _load_inrepo_config(anchor)
        inrepo_profile_assignments[name] = inrepo_settings.get(
            "profile_assignment"
        )

        # NEW tier (between in-repo and machine-local): a control-plane-supplied
        # ``pr:`` overlay for this FOREIGN repo (from the control plane's
        # ``related.yaml`` or a ``<repo>-harness`` plugin). Lets the checked-in
        # control-plane view drive the repo's PR workflow without committing
        # harness config into it. A machine-local ``repos.<name>.pr`` still wins.
        base_settings = inrepo_settings
        cp_pr = cp_related_pr.get(name)
        if cp_pr:
            base_settings = _deep_merge(base_settings, {"pr": cp_pr})

        # Merge per-repo settings: in-repo base < control-plane related pr <
        # machine-local override. (The global tier carries only machine-wide
        # top-level defaults -- srcroot/machine/platform/profiles -- never
        # per-repo settings.)
        merged = _deep_merge(base_settings, machine_repo)

        # Fall back to the registries for adoption facts that _build_repo_config
        # would otherwise read ONLY from the overlay/in-repo (default_branch,
        # base_repo). This lets a machine-local overlay stay minimal -- it need
        # not restate a value the registry already owns. Overlay/in-repo still
        # win (setdefault only fills absent keys). ``repos doctor`` flags an
        # overlay that redundantly restates these.
        for _k, _v in _resolve_adoption_defaults_from_registry(
            name, platform
        ).items():
            merged.setdefault(_k, _v)

        repos[name] = _build_repo_config(merged, anchor, str(worktree_root))

    if not repos:
        raise ValueError(
            f"No repo could be resolved for project {repo_name or '?'!r}.\n"
            f"Checked machine-local config ({path}) and the repos registry "
            f"({install_dir() / 'repos.yaml'}).\n"
            "Run the installer / register the repo first:\n"
            "  pwsh -File <repo>/plugins/agent-worktrees/scripts/install.ps1 install"
        )

    # copilot_profiles: machine-local > knowledge overlay > global.
    if "copilot_profiles" in machine_raw:
        profiles_raw = machine_raw.get("copilot_profiles")
    elif "copilot_profiles" in knowledge_raw:
        profiles_raw = knowledge_raw.get("copilot_profiles")
    else:
        profiles_raw = global_raw.get("copilot_profiles", [])
    parsed_profiles = _parse_profiles(profiles_raw or [])

    # Profile assignment has a deliberate trust-aware merge rather than the
    # ordinary "highest value wins" top-level merge. User-owned layers may arm
    # and define the pool; committed repository config may only provide a
    # default-off template or intersect an already user-owned pool/lanes.
    user_assignment_raw: Any = {}
    for source in (
        global_raw,
        knowledge_raw,
        machine_raw,
        machine_repos.get(repo_name) if isinstance(machine_repos, dict) else {},
    ):
        if not isinstance(source, dict) or "profile_assignment" not in source:
            continue
        candidate = source.get("profile_assignment")
        if isinstance(user_assignment_raw, dict) and isinstance(candidate, dict):
            user_assignment_raw = _deep_merge(user_assignment_raw, candidate)
        else:
            user_assignment_raw = candidate
    repository_assignment_raw = inrepo_profile_assignments.get(repo_name)

    return Config(
        srcroot=srcroot,
        machine=machine,
        platform=platform,
        repo_name=repo_name,
        repos=repos,
        knowledge_repo=(
            machine_raw.get("knowledge_repo")
            or global_raw.get("knowledge_repo")
            or ""
        ),
        copilot_profiles=parsed_profiles,
        session_backend=_parse_session_backend(
            machine_raw.get(
                "session_backend",
                global_raw.get("session_backend"),
            )
        ),
        profile_assignment=_parse_profile_assignment(
            user_assignment_raw,
            repository_assignment_raw,
            parsed_profiles,
        ),
        headless=bool(
            machine_raw.get(
                "headless",
                knowledge_raw.get("headless", global_raw.get("headless", False)),
            )
        ),
        auto_fast_forward=bool(
            machine_raw.get(
                "auto_fast_forward",
                knowledge_raw.get(
                    "auto_fast_forward",
                    global_raw.get("auto_fast_forward", True),
                ),
            )
        ),
        new_picker=bool(
            machine_raw.get(
                "new_picker",
                knowledge_raw.get(
                    "new_picker", global_raw.get("new_picker", True)
                ),
            )
        ),
    )


def _parse_session_backend(raw: Any) -> SessionBackendConfig:
    """Parse the user-owned same-machine session backend configuration."""
    if raw in (None, "", {}):
        return SessionBackendConfig()
    if not isinstance(raw, dict):
        raise ValueError("session_backend must be a mapping")
    kind = str(raw.get("kind", "direct")).strip().lower()
    if kind not in {"direct", "ahp"}:
        raise ValueError("session_backend.kind must be 'direct' or 'ahp'")
    if kind == "direct":
        return SessionBackendConfig()

    endpoint_url = str(raw.get("endpoint_url", "")).strip()
    if not endpoint_url:
        raise ValueError(
            "session_backend.endpoint_url is required when kind is 'ahp'"
        )
    github_account = str(raw.get("github_account", "")).strip()
    auth_resource = str(
        raw.get("auth_resource", "https://api.github.com")
    ).strip()
    if not auth_resource:
        raise ValueError("session_backend.auth_resource must be non-empty")

    versions_raw = raw.get("protocol_versions", ["0.7.0"])
    if not isinstance(versions_raw, list) or not versions_raw:
        raise ValueError(
            "session_backend.protocol_versions must be a non-empty list"
        )
    protocol_versions = tuple(
        str(value).strip() for value in versions_raw if str(value).strip()
    )
    if not protocol_versions:
        raise ValueError(
            "session_backend.protocol_versions must contain a version"
        )
    timeout_raw = raw.get("connect_timeout_seconds", 15)
    if isinstance(timeout_raw, bool):
        raise ValueError(
            "session_backend.connect_timeout_seconds must be a number"
        )
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "session_backend.connect_timeout_seconds must be a number"
        ) from exc
    if timeout <= 0 or timeout > 120:
        raise ValueError(
            "session_backend.connect_timeout_seconds must be in (0, 120]"
        )
    return SessionBackendConfig(
        kind="ahp",
        endpoint_url=endpoint_url,
        github_account=github_account,
        protocol_versions=protocol_versions,
        auth_resource=auth_resource,
        connect_timeout_seconds=timeout,
    )


def load_project_config(name: str) -> Config:
    """Load one project's layered config without inheriting caller identity."""
    previous = active_project()
    set_active_project(name)
    try:
        return load_config(project_dir(name) / "config.yaml", project=name)
    finally:
        set_active_project(previous)


def _load_yaml_safe(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dict, returning ``{}`` on any problem.

    Never raises: config loading is on the critical path of every command, so a
    missing, empty, or malformed file degrades to an empty mapping rather than
    breaking the whole CLI.
    """
    try:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _load_config_d(
    config_d: Path,
    *,
    project_name: str = "",
    warn: bool = False,
) -> dict[str, Any]:
    """Deep-merge validated active entries from a ``config.d`` registry.

    A drop-in lets a service register machine-local config without editing the
    shared ``config.yaml`` (e.g. the vault registering
    ``session_env.SUDO_ASKPASS``). Files merge in lexical order (later names win
    among drop-ins); the caller layers the result UNDER ``config.yaml``.

    Direct YAML is operator-owned. Managed plugins use attributed JSON pointers
    to YAML contained by their current identity-verified root. Every entry is an
    independent fault boundary and uncertainty retains only its last-known
    contribution.
    """
    from .config_dropins import (
        scan_config_dropin_registry,
        warn_config_dropin_findings,
    )

    report = scan_config_dropin_registry(
        config_d,
        project_name=project_name,
    )
    if warn:
        warn_config_dropin_findings(report)
    merged: dict[str, Any] = {}
    for contribution in report.active_configs:
        merged = _deep_merge(merged, contribution.raw_config)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` deep-merged with ``override`` (override wins).

    Nested dicts merge recursively; every other value (scalars, lists) is
    replaced wholesale by ``override``. Inputs are not mutated.
    """
    result: dict[str, Any] = dict(base)
    for key, ov in override.items():
        bv = result.get(key)
        if isinstance(bv, dict) and isinstance(ov, dict):
            result[key] = _deep_merge(bv, ov)
        else:
            result[key] = ov
    return result


def _project_name_safe() -> str:
    """Return the active project name, or ``""`` if none is resolved.

    Unlike :func:`project_name`, never raises -- used where an absent project is
    a tolerable condition (e.g. tests that pass an explicit config).
    """
    try:
        return project_name()
    except Exception:
        return ""


def _resolve_anchor_from_registry(name: str, platform: str) -> str | None:
    """Return the anchor path for ``name`` from ``repos.yaml``, or None.

    Lets a convention-adopted repo load with no machine-local config: the
    machine-specific path comes from the registry, the settings from the
    repo's own in-repo config. Never raises.
    """
    try:
        from . import repos as repos_mod

        registry = repos_mod.read_registry()
        entry = registry.repos.get(name)
        if entry is None:
            return None
        return entry.local_path(platform) or None
    except Exception:
        return None


# E1e config.yaml knowledge overlay (#947): the portable operator-preference keys
# that graft from the bound knowledge repo's config.yaml. Deliberately NARROW --
# only machine-independent preferences. Machine-specifics (srcroot / machine /
# platform / repo_name), the machine-local binding (knowledge_repo), and per-repo
# ``repos.<name>`` blocks are intentionally EXCLUDED (a shared knowledge repo must
# not dictate a machine's paths, its own binding, or another repo's settings).
_KNOWLEDGE_OVERLAY_TOP_KEYS: tuple[str, ...] = (
    "copilot_profiles", "profile_assignment", "headless", "auto_fast_forward",
    "new_picker",
)


def _load_knowledge_overlay_config(
    machine_raw: dict[str, Any], global_raw: dict[str, Any],
    repo_name: str, platform: str,
) -> dict[str, Any]:
    """The E1e config.yaml **knowledge overlay** tier (portable operator prefs).

    Returns the bound knowledge repo's ``config.yaml`` filtered to
    :data:`_KNOWLEDGE_OVERLAY_TOP_KEYS` when the launch repo is a **stateless
    harness** bound to a knowledge repo; ``{}`` otherwise. This lets portable
    operator preferences live (versioned) in the knowledge repo as a tier between
    the harness in-repo base and the machine-local config, so machine-local stays
    minimal (just the binding + true machine-specifics).

    This is the config-READ knowledge overlay for ``config.yaml`` itself. Gated +
    fail-open: a non-stateless launch repo, an unbound harness, an unregistered
    knowledge repo, or any error yields ``{}`` -- so the hot config path is
    byte-identical off the stateless track.
    """
    try:
        knowledge_repo = (
            machine_raw.get("knowledge_repo")
            or global_raw.get("knowledge_repo")
            or ""
        ).strip()
        if not knowledge_repo:
            return {}
        # Gate on the LAUNCH repo declaring itself stateless (requires an external
        # state root) -- read from its own in-repo config.
        machine_repos = machine_raw.get("repos")
        launch_anchor = None
        if isinstance(machine_repos, dict):
            block = machine_repos.get(repo_name)
            if isinstance(block, dict):
                launch_anchor = block.get("anchor")
        launch_anchor = launch_anchor or _resolve_anchor_from_registry(
            repo_name, platform
        )
        if not launch_anchor:
            return {}
        inrepo = _load_inrepo_config(launch_anchor)
        if not (inrepo.get("stateless")
                or inrepo.get("requires_external_state_root")):
            return {}
        kpath = _resolve_anchor_from_registry(knowledge_repo, platform)
        if not kpath:
            return {}
        kcfg = _load_inrepo_config(kpath)
        return {k: kcfg[k] for k in _KNOWLEDGE_OVERLAY_TOP_KEYS if k in kcfg}
    except Exception:
        return {}


def _resolve_adoption_defaults_from_registry(
    name: str, platform: str
) -> dict[str, Any]:
    """Registry fallbacks for adoption facts ``load_config`` otherwise reads
    ONLY from the overlay/in-repo, so a minimal overlay need not restate them.

    * ``default_branch`` -- from ``repos.yaml`` (identity registry), else
      ``projects.yaml`` (adoption registry).
    * ``base_repo`` -- from ``projects.yaml`` (its authoritative home).

    Only keys with a usable registry value are returned; the caller fills them
    with ``setdefault`` so overlay/in-repo settings still win. Never raises.
    """
    out: dict[str, Any] = {}
    try:
        from . import repos as repos_mod

        entry = repos_mod.read_registry().repos.get(name)
        if entry is not None and entry.default_branch:
            out["default_branch"] = entry.default_branch
    except Exception:
        pass
    try:
        from . import installer

        proj = (installer.read_projects_registry().get("projects") or {}).get(name)
        if isinstance(proj, dict):
            if "default_branch" not in out and proj.get("default_branch"):
                out["default_branch"] = str(proj["default_branch"])
            if proj.get("base_repo") is not None:
                out["base_repo"] = bool(proj["base_repo"])
    except Exception:
        pass
    return out


def peek_base_repo(project: str | None = None) -> bool | None:
    """Read only the layers that can define ``base_repo``.

    This is the pre-Picker fast path: unlike :func:`load_config`, it never
    resolves knowledge overlays, related-repo PR policy, profiles, or other
    settings. ``None`` means the shallow read was inconclusive and the caller
    must fall back to the full loader.
    """
    try:
        machine_path = default_config_path()
        global_raw = _load_yaml_safe(global_config_path())
        machine_raw = _load_yaml_safe(machine_path)
        name = (
            project
            or machine_raw.get("repo_name")
            or global_raw.get("repo_name")
            or _project_name_safe()
        )
        if not name:
            return None
        dropins = _load_config_d(
            machine_path.parent / "config.d",
            project_name=str(name),
            warn=False,
        )
        if dropins:
            machine_raw = _deep_merge(dropins, machine_raw)
        platform_name = (
            machine_raw.get("platform")
            or global_raw.get("platform")
            or detect_platform()
        )
        machine_repos = machine_raw.get("repos") or {}
        machine_repo = (
            machine_repos.get(name) or {}
            if isinstance(machine_repos, dict)
            else {}
        )
        if not isinstance(machine_repo, dict):
            machine_repo = {}
        anchor = machine_repo.get("anchor") or _resolve_anchor_from_registry(
            name, platform_name
        )
        inrepo = _load_inrepo_config(anchor) if anchor else {}
        merged = _deep_merge(inrepo, machine_repo)
        for key, value in _resolve_adoption_defaults_from_registry(
            name, platform_name
        ).items():
            merged.setdefault(key, value)
        return bool(merged.get("base_repo", False))
    except Exception:
        return None


def _build_repo_config(
    data: dict[str, Any], anchor: str, worktree_root: str
) -> RepoConfig:
    """Build a RepoConfig from a merged repo-settings dict + resolved paths.

    ``data`` is the per-repo settings after the three-tier merge; ``anchor``
    and ``worktree_root`` are the machine paths (resolved separately, since
    they never come from the shared in-repo config).
    """
    launch: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("launch") or {}).items():
        if isinstance(cmd_list, list):
            launch[plat_key] = [str(c) for c in cmd_list]

    launch_recovery: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("launch_recovery") or {}).items():
        if isinstance(cmd_list, list):
            launch_recovery[plat_key] = [str(c) for c in cmd_list]

    copilot_path: dict[str, str] = {}
    for plat_key, executable in (data.get("copilot_path") or {}).items():
        if isinstance(executable, str) and executable.strip():
            copilot_path[plat_key] = executable.strip()

    setup_hook: dict[str, str] = {}
    for plat_key, hook_path in (data.get("setup_hook") or {}).items():
        if isinstance(hook_path, str) and hook_path.strip():
            setup_hook[plat_key] = hook_path.strip()

    env_script: dict[str, str] = {}
    for plat_key, script_path in (data.get("env_script") or {}).items():
        if isinstance(script_path, str) and script_path.strip():
            env_script[plat_key] = script_path.strip()

    session_path: dict[str, list[str]] = {}
    for plat_key, dir_list in (data.get("session_path") or {}).items():
        if isinstance(dir_list, list):
            session_path[plat_key] = [str(d) for d in dir_list]

    session_env: dict[str, str] = {}
    for env_key, env_val in (data.get("session_env") or {}).items():
        if isinstance(env_key, str) and env_key.strip():
            session_env[env_key.strip()] = str(env_val)

    raw_vpaths = data.get("validate_paths", [])
    validate_paths = (
        [str(p) for p in raw_vpaths] if isinstance(raw_vpaths, list) else []
    )

    validate_hook: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("validate_hook") or {}).items():
        if isinstance(cmd_list, list):
            validate_hook[plat_key] = [str(c) for c in cmd_list]

    raw_spaths = data.get("service_paths", [])
    service_paths = (
        [str(p) for p in raw_spaths] if isinstance(raw_spaths, list) else []
    )

    post_install_hook: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("post_install_hook") or {}).items():
        if isinstance(cmd_list, list):
            post_install_hook[plat_key] = [str(c) for c in cmd_list]

    return RepoConfig(
        anchor=str(anchor),
        worktree_root=str(worktree_root or derive_worktree_root(anchor)),
        default_branch=data.get("default_branch", "master"),
        remote=data.get("remote", "origin"),
        launch=launch,
        launch_recovery=launch_recovery,
        copilot_path=copilot_path,
        setup_hook=setup_hook,
        env_script=env_script,
        session_path=session_path,
        session_env=session_env,
        validate_paths=validate_paths,
        validate_hook=validate_hook,
        service_paths=service_paths,
        post_install_hook=post_install_hook,
        pr=_parse_pr(data.get("pr")),
        base_repo=bool(data.get("base_repo", False)),
        stateless=bool(data.get("stateless", False)),
        requires_external_state_root=bool(
            data.get("requires_external_state_root", False)
        ),
    )


def _load_inrepo_config(anchor: str) -> dict[str, Any]:
    """Return the repo's in-repo flat settings dict, or ``{}``.

    Reads ``<anchor>/.agent-worktrees/config.yaml`` (preferred, directory form).
    Falls back to the legacy single-file ``<anchor>/.agent-worktrees.yaml``
    (which carried only a ``pr:`` block -- still a valid, minimal flat
    settings dict). Never raises: a missing or malformed file degrades to an
    empty mapping so config loading cannot be broken by a bad committed file.
    """
    dir_form = _load_yaml_safe(inrepo_config_path(anchor))
    if dir_form:
        return dir_form
    return _load_yaml_safe(Path(anchor) / INREPO_CONFIG_FILENAME)


def _control_plane_related_pr_map() -> dict[str, dict[str, Any]]:
    """Map ``repo-name -> raw pr: block`` from the control plane's related index.

    A checked-in control-plane source may drive a FOREIGN repo's PR workflow --
    without committing harness config into that (often shared) repo -- by
    carrying a ``pr:`` block on the repo's entry in the control plane's
    ``related.yaml``, or via a ``<repo>-harness`` plugin that *contributes* a
    related entry. Both flow through the grafted related index, so this reads it
    once (installed-plugin contributions + the control-plane ``related.yaml``)
    and returns only the entries that carry a ``pr`` block.

    Fail-safe: it resolves the control-plane project without PR grafting, then
    uses the standard config-source seam to include its knowledge overlay. An
    unregistered control-plane anchor falls back to the original anchor-only
    behavior rather than inheriting the caller's active project. Any error
    degrades to the available anchors (or ``{}``) so the hot config path cannot
    be broken by control-plane discovery. ``load_config`` layers each block
    ABOVE the foreign repo's own in-repo ``pr`` and BELOW a machine-local
    ``repos.<name>.pr`` override.
    """
    try:
        from . import related
        from . import repos
        from . import state_root

        cp = related.find_control_plane_anchor()
        anchors: list[str] = list(related.installed_plugin_related_anchors())
        if cp:
            try:
                cp_key = related._anchor_key(cp)
                cp_project = next(
                    (
                        entry.name
                        for entry in repos.list_repos()
                        if entry.local_path()
                        and related._anchor_key(entry.local_path()) == cp_key
                    ),
                    None,
                )
                cp_sources = (
                    state_root.config_source_anchors(
                        load_config(
                            include_control_plane_related_pr=False,
                            project=cp_project,
                        ),
                        base_anchor=cp,
                    )
                    if cp_project
                    else []
                )
            except Exception:
                cp_sources = []
            if cp_sources:
                anchors.extend(
                    related.config_contribution_anchor(source.anchor, source.origin)
                    for source in cp_sources
                    if source.anchor
                )
            else:
                anchors.append(
                    related.config_contribution_anchor(cp, "harness")
                )
        if not anchors:
            return {}
        rc = related.read_related_grafted(anchors)
        return {
            name: entry.pr
            for name, entry in rc.related.items()
            if getattr(entry, "pr", None)
        }
    except Exception:
        return {}


def _parse_pr(raw: Any) -> PRConfig:
    """Parse the optional ``pr:`` block of a repo config into a PRConfig.

    Unknown or missing values fall back to PRConfig defaults (disabled).
    """
    if not isinstance(raw, dict):
        return PRConfig()
    required = bool(raw.get("required", False))
    # ``required`` implies ``enabled``: enforcing PRs only makes sense when
    # PR mode is on, so a lone ``required: true`` turns the mode on too.
    enabled = bool(raw.get("enabled", False)) or required

    def _str_tuple(value: Any) -> tuple[str, ...]:
        """Coerce a scalar-or-list config value into a tuple of strings.

        Accepts a list/tuple (each item stringified), a lone scalar (wrapped),
        or a falsy/missing value (empty tuple). Empty strings are dropped so a
        stray ``""`` in a YAML list can't become a match-everything token.
        """
        if isinstance(value, (list, tuple)):
            return tuple(s for s in (str(x).strip() for x in value) if s)
        if value:
            s = str(value).strip()
            return (s,) if s else ()
        return ()

    labels = _str_tuple(raw.get("labels", ()))

    def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
        """Normalize a config enum to one of ``allowed`` (case-insensitive),
        falling back to ``default`` for a missing or unrecognized value."""
        s = str(value).strip().lower()
        return s if s in allowed else default

    head_scheme = str(raw.get("head_scheme", "refspec")).strip().lower()
    if head_scheme not in ("snapshot", "refspec"):
        # A present-but-garbage value signals misconfiguration -- fall back to
        # the maximally-compatible scheme (snapshot needs no pre-push-hook
        # cooperation), NOT the refspec default, so a typo can't silently break
        # pushes in a repo whose hook isn't refspec-ready.
        head_scheme = "snapshot"
    return PRConfig(
        enabled=enabled,
        required=required,
        provider=str(raw.get("provider", "gitea")),
        strategy=str(raw.get("strategy", "detach")),
        branch_prefix=str(raw.get("branch_prefix", "feature")),
        head_scheme=head_scheme,
        head_pattern=str(raw.get("head_pattern", "")),
        api_base=str(raw.get("api_base", "")),
        token_env=str(raw.get("token_env", "")),
        token_command=str(raw.get("token_command", "")),
        labels=labels,
        auto_open=bool(raw.get("auto_open", False)),
        source_attribution=bool(raw.get("source_attribution", False)),
        required_body_sections=_str_tuple(raw.get("required_body_sections", ())),
        automerge_label=str(raw.get("automerge_label", "")).strip(),
        hold_labels=_str_tuple(raw.get("hold_labels", ())),
        wip_title_prefixes=_str_tuple(raw.get("wip_title_prefixes", ())),
        approval_required=bool(raw.get("approval_required", True)),
        allow_stale_approval=bool(raw.get("allow_stale_approval", False)),
        squash=bool(raw.get("squash", True)),
        delete_source_branch=bool(raw.get("delete_source_branch", True)),
        bypass_policy=bool(raw.get("bypass_policy", False)),
        bypass_reason=str(raw.get("bypass_reason", "")),
        reviewer=str(raw.get("reviewer", "")).strip(),
        review_blocking=bool(raw.get("review_blocking", False)),
        review_latency_hint=str(raw.get("review_latency_hint", "")).strip(),
        self_approve=bool(raw.get("self_approve", False)),
        merge_actor=str(raw.get("merge_actor", "")).strip().lower(),
        conflict_retriggers_review=bool(raw.get("conflict_retriggers_review", True)),
        branch_update_strategy=_choice(
            raw.get("branch_update_strategy", "rebase"), ("rebase", "merge"),
            "rebase"),
        merge_strategy=_choice(
            raw.get("merge_strategy", "squash"), ("squash", "merge", "rebase"),
            "squash"),
        prefer_auto_merge=bool(raw.get("prefer_auto_merge", True)),
    )


def _parse_profiles(raw_list: list[Any]) -> list[CopilotProfile]:
    """Parse and validate copilot_profiles from config YAML."""
    if not isinstance(raw_list, list):
        return []

    profiles: list[CopilotProfile] = []
    seen_names: set[str] = set()

    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)

        env: dict[str, str] = {}
        raw_env = entry.get("env", {})
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                if _ENV_KEY_RE.match(str(k)):
                    env[str(k)] = str(v)

        raw_args = entry.get("copilot_args", [])
        copilot_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

        profiles.append(CopilotProfile(
            name=name,
            label=entry.get("label", name),
            env=env,
            copilot_args=copilot_args,
        ))

    return profiles


def _parse_profile_assignment(
    user_raw: Any,
    repository_raw: Any,
    profiles: list[CopilotProfile],
) -> ProfileAssignmentPolicy | None:
    """Resolve a trust-aware profile-assignment policy.

    The user-owned layers are the only authority for ``armed`` and the profile
    pool. Repository config may provide other defaults and may narrow the pool
    or eligible lanes, but it cannot add names the user did not already own.
    """
    if user_raw in (None, {}) and repository_raw in (None, {}):
        return None
    has_user_config = user_raw not in (None, {})
    has_repository_config = repository_raw not in (None, {})
    user = user_raw if isinstance(user_raw, dict) else {}
    repository = repository_raw if isinstance(repository_raw, dict) else {}
    armed = user.get("armed") is True
    user_errors: list[str] = []
    repository_errors: list[str] = []
    if has_user_config and not isinstance(user_raw, dict):
        user_errors.append("profile_assignment must be a mapping")
    if has_repository_config and not isinstance(repository_raw, dict):
        repository_errors.append(
            "repository profile_assignment must be a mapping"
        )
    if "armed" in user and not isinstance(user.get("armed"), bool):
        user_errors.append("profile_assignment.armed must be a boolean")

    name = str(user.get("name") or repository.get("name") or "").strip()
    if not name or not _ASSIGNMENT_NAME_RE.fullmatch(name):
        target = (
            user_errors
            if user.get("name") or not has_repository_config
            else repository_errors
        )
        target.append(
            "profile_assignment.name must be a non-empty identifier using "
            "letters, digits, '.', '_', or '-'"
        )
    repository_name = str(repository.get("name") or "").strip()
    if armed and repository_name and repository_name != name:
        repository_errors.append(
            "repository profile_assignment.name does not match the user-armed policy"
        )

    mode = str(user.get("mode") or repository.get("mode") or "").strip().lower()
    if mode != "balanced-random":
        target = (
            user_errors
            if user.get("mode") or not has_repository_config
            else repository_errors
        )
        target.append("profile_assignment.mode must be 'balanced-random'")

    raw_pool = user.get("profiles")
    pool: list[str] = []
    if isinstance(raw_pool, list):
        for item in raw_pool:
            value = str(item).strip()
            if value and value not in pool:
                pool.append(value)
    elif raw_pool is not None:
        user_errors.append("profile_assignment.profiles must be a list")
    if armed and not pool:
        user_errors.append(
            "an armed profile_assignment requires a non-empty user-owned profile pool"
        )

    repo_pool = repository.get("profiles")
    if repo_pool is not None:
        if isinstance(repo_pool, list):
            allowed = {str(item).strip() for item in repo_pool if str(item).strip()}
            pool = [name for name in pool if name in allowed]
        else:
            repository_errors.append(
                "repository profile_assignment.profiles must be a list"
            )
    if armed and not pool:
        repository_errors.append(
            "repository profile_assignment narrowing left no eligible user profiles"
        )

    available = {profile.name for profile in profiles}
    missing = [profile_name for profile_name in pool if profile_name not in available]
    if armed and missing:
        user_errors.append(
            "profile_assignment references undefined copilot_profiles: "
            + ", ".join(missing)
        )

    allowed_lanes = ("new", "handoff-cutover")
    raw_lanes = user.get("eligible_lanes", allowed_lanes)
    if isinstance(raw_lanes, list | tuple):
        lanes = [
            str(item).strip()
            for item in raw_lanes
            if str(item).strip() in allowed_lanes
        ]
    else:
        lanes = []
        user_errors.append("profile_assignment.eligible_lanes must be a list")
    repo_lanes = repository.get("eligible_lanes")
    if repo_lanes is not None:
        if isinstance(repo_lanes, list | tuple):
            allowed_by_repo = {
                str(item).strip()
                for item in repo_lanes
                if str(item).strip() in allowed_lanes
            }
            lanes = [lane for lane in lanes if lane in allowed_by_repo]
        else:
            repository_errors.append(
                "repository profile_assignment.eligible_lanes must be a list"
            )
    lanes = list(dict.fromkeys(lanes))
    if armed and not lanes:
        user_errors.append(
            "an armed profile_assignment has no eligible launch lanes"
        )

    label_value = user.get(
        "assignment_label", repository.get("assignment_label", "")
    )
    if label_value is not None and not isinstance(label_value, str):
        target = (
            user_errors
            if "assignment_label" in user
            else repository_errors
        )
        target.append("profile_assignment.assignment_label must be a string")
    assignment_label = str(label_value or "").strip()

    return ProfileAssignmentPolicy(
        name=name,
        mode=mode,
        armed=armed,
        profiles=tuple(pool),
        assignment_label=assignment_label,
        eligible_lanes=tuple(lanes),
        error="; ".join(dict.fromkeys(user_errors)),
        repository_error="; ".join(dict.fromkeys(repository_errors)),
    )


def derive_worktree_root(anchor: str | Path) -> str:
    """Default worktree root for an *anchor*: a sibling ``<anchor>.worktrees``
    directory.

    This mirrors the GitHub Copilot CLI's native ``--worktree`` / ``/worktree``
    layout (worktrees created as a ``<repo>.worktrees`` sibling of the repo),
    so worktrees created by agent-worktrees and by Copilot land in the same
    place and are mutually discoverable.  Used whenever a repo's config omits
    an explicit ``worktree_root`` (the field remains an optional override)."""
    return f"{str(anchor).rstrip('/').rstrip(chr(92))}.worktrees"


def tracking_dir() -> Path:
    """Return the worktree tracking directory path (per-project)."""
    return project_dir() / "worktrees"


def venv_python() -> Path:
    """Path to the active runtime slot's Python interpreter.

    Junction-free: resolve ``current-version`` -> ``last-known-good`` -> newest
    complete slot, matching the canonical hook/binstub resolver. The ``.venv``
    link is retired (#1106).
    """
    root = install_dir()
    win = platform.system() == "Windows"
    sub = ("Scripts", "python.exe") if win else ("bin", "python")
    try:
        ver = (root / "current-version").read_text("utf-8").strip()
    except OSError:
        ver = ""
    if ver:
        p = root / "versions" / ver / sub[0] / sub[1]
        if p.exists():
            return p
    try:
        last_known_good = (root / "last-known-good").read_text("utf-8").strip()
    except OSError:
        last_known_good = ""
    if last_known_good:
        p = root / "versions" / last_known_good / sub[0] / sub[1]
        if p.exists():
            return p
    versions = root / "versions"
    if versions.is_dir():
        for slot in sorted(versions.iterdir(), reverse=True):
            p = slot / sub[0] / sub[1]
            if p.exists():
                return p
    # No installed slot -- return the canonical (marker or empty) slot path.
    return root / "versions" / (ver or last_known_good or "") / sub[0] / sub[1]
