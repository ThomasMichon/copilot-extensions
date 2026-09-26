"""Repos registry -- catalog of known repositories and source roots.

Manages ``~/.agent-worktrees/repos.yaml``, the canonical multi-repo
registry.  Each repo is tagged with a management *class* describing how
the multi-machine system interacts with its local checkout:

- **reference** -- read-only; tracked only for path resolution, cloning,
  and indexing.  Never edited locally.
- **singleton** -- editable as a single anchor checkout, no worktree
  isolation; one flow at a time.
- **worktree** -- full agent-worktrees lifecycle; concurrent-flow safe,
  with edits/stages/commits isolated in per-task worktrees until push.
  These are also adopted as ``projects.yaml`` projects.
- **knowledge** -- same worktree-capable mechanics as ``worktree`` (eligible
  for the ``-k`` paired-knowledge carve, see ``__main__._carve_paired_
  knowledge``), but exists only to be carved as another project's paired
  knowledge companion, never driven directly (no standalone ``create``, no
  binstub, hidden from the Picker's top-level project list -- enforced via
  the repo's own committed ``RepoConfig.knowledge_only`` flag in
  ``config.py``; set both together).

The registry also stores per-platform source roots (``srcroot``) so that
adopt, WSL provision, and clone operations know where to put repos.

This registry supersedes the legacy ``~/.git-repos`` file; use
``repos migrate`` to import an existing ``~/.git-repos`` into it.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from . import git_ops, output, registry_paths

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# See the module docstring above for what each management class means.
VALID_CLASSES = ("reference", "singleton", "worktree", "knowledge")

# Legacy ``type`` values mapped onto the new class taxonomy.
_LEGACY_TYPE_MAP = {"project": "worktree", "repo": "reference"}


def normalize_class(value: str | None) -> str:
    """Coerce a raw class/type string to a valid management class.

    Accepts the new class names (reference/singleton/worktree) and the
    legacy ``type`` values (project/repo).  Unknown values fall back to
    ``reference`` (the safest -- read-only) default.
    """
    if not value:
        return "reference"
    v = str(value).strip().lower()
    if v in VALID_CLASSES:
        return v
    return _LEGACY_TYPE_MAP.get(v, "reference")


@dataclass
class RepoEntry:
    """A single repo in the registry."""

    name: str
    repo_class: str = "reference"  # reference | singleton | worktree
    remote: str = ""
    default_branch: str = ""
    tags: list[str] = field(default_factory=list)
    contributing: str = ""
    # Optional preferred GitHub identity (a ``gh`` account login) this repo's
    # git/gh operations run under. Absent => derived from the remote owner for
    # github.com remotes, else no account (today's ambient-auth behavior). An
    # explicit value overrides the derived owner (an EMU account can span orgs,
    # so owner != account isn't guaranteed). See :func:`resolve_account`.
    account: str = ""
    # Optional preferred Copilot CLI identity (a login already cached in
    # Copilot's own credential store, distinct from ``account`` above) for
    # this repo. Repo-keyed rather than owner-keyed: unlike ``account``/
    # ``account_map`` (github-owner-derived), this must also work for repos
    # with no GitHub owner at all (e.g. a personal repo on a private Gitea
    # instance) where Copilot's own inference identity still needs to be
    # pinned. Absent => falls back to the machine's ``default_copilot_account``
    # (see ``config.Config.default_copilot_account``), then to no preference
    # (ambient Copilot login, today's behavior). See
    # :func:`resolve_copilot_account` / :func:`copilot_account_for` and
    # ThomasMichon/copilot-extensions#3296.
    copilot_account: str = ""
    # Whether this repo backs a same-machine agent in agent-bridge. Defaults
    # ON for worktree/singleton repos (you adopt them to work in them); OFF for
    # reference repos (read-only). `register`/`add --no-agent` forces it off.
    agent: bool = True
    paths: dict[str, str] = field(default_factory=dict)
    # paths keys: "windows", "wsl", "linux"

    def local_path(self, plat: str | None = None) -> str | None:
        """Return the path for the given (or current) platform.

        Expands a leading ``~`` so every consumer gets a usable absolute path.
        Registry entries may store a home-relative path (e.g. the WSL
        ``~/src/test-chamber``); ``pathlib.Path`` does NOT treat ``~`` as
        special, so a raw return breaks every ``Path(local_path()).is_dir()`` /
        normalize consumer -- ``_anchor_for_project``,
        ``cfg._resolve_anchor_from_registry``, and ``_reverse_lookup_project``'s
        repos fallback -- making CWD->project discovery fail for that repo
        (#4190). ``expanduser`` is a no-op on already-absolute entries.
        """
        plat = plat or _current_platform()
        p = self.paths.get(plat)
        return os.path.expanduser(p) if p else p


@dataclass
class ReposRegistry:
    """The full repos.yaml content."""

    srcroot: dict[str, str] = field(default_factory=dict)
    # srcroot keys: "windows", "wsl", "linux"
    repos: dict[str, RepoEntry] = field(default_factory=dict)
    # Decoupled GitHub owner -> account login map. The org-level identity layer:
    # any repo hosted under ``owner`` uses the mapped ``gh`` account login, even
    # when the owner is an *org* (no gh account is named after it) or when no
    # repo under that owner is registered. Complements the per-repo ``account:``
    # override (which stays the finest grain). See :func:`resolve_account` /
    # :func:`account_for_github_owner`. Keys are compared case-insensitively.
    account_map: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _current_platform() -> str:
    """Return 'windows', 'wsl', or 'linux'."""
    if platform.system() == "Windows":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _repos_yaml_path() -> Path:
    """Path to the repos registry file."""
    return registry_paths.registry_path("repos.yaml")


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def read_registry() -> ReposRegistry:
    """Load repos.yaml, returning an empty registry if missing."""
    path = _repos_yaml_path()
    if not path.exists():
        return ReposRegistry()

    try:
        from . import config_migrations

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ReposRegistry()

        # Lazy schema migration (in memory, never persists / never raises).
        data = config_migrations.migrate_loaded(data, config_migrations.SCHEMA_REPOS)

        srcroot = data.get("srcroot", {})
        if not isinstance(srcroot, dict):
            srcroot = {}

        raw_map = data.get("account_map", {})
        account_map: dict[str, str] = {}
        if isinstance(raw_map, dict):
            for owner, login in raw_map.items():
                if owner and login:
                    account_map[str(owner)] = str(login)

        repos: dict[str, RepoEntry] = {}
        raw_repos = data.get("repos", {})
        if isinstance(raw_repos, dict):
            for name, entry in raw_repos.items():
                if not isinstance(entry, dict):
                    continue
                paths = {}
                for plat in ("windows", "wsl", "linux"):
                    if plat in entry:
                        paths[plat] = str(entry[plat])
                raw_tags = entry.get("tags", [])
                tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
                # Prefer the new "class" field; fall back to legacy "type".
                raw_class = entry.get("class", entry.get("type"))
                norm_class = normalize_class(raw_class)
                # Agent exposure defaults ON for worktree/singleton, OFF for
                # reference; an explicit `agent:` always wins.
                raw_agent = entry.get("agent")
                agent = (
                    bool(raw_agent) if raw_agent is not None
                    else norm_class != "reference"
                )
                repos[name] = RepoEntry(
                    name=name,
                    repo_class=norm_class,
                    remote=entry.get("remote", ""),
                    default_branch=entry.get("default_branch", ""),
                    tags=tags,
                    contributing=entry.get("contributing", ""),
                    account=str(entry.get("account", "") or ""),
                    copilot_account=str(entry.get("copilot_account", "") or ""),
                    agent=agent,
                    paths=paths,
                )

        return ReposRegistry(srcroot=srcroot, repos=repos, account_map=account_map)
    except Exception:
        return ReposRegistry()


def write_registry(registry: ReposRegistry) -> None:
    """Write repos.yaml with hand-formatted YAML."""
    path = _repos_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# ~/.agent-worktrees/repos.yaml",
        "# Registry of known repositories and source roots.",
        "",
    ]

    # srcroot section
    if registry.srcroot:
        lines.append("srcroot:")
        for plat in ("windows", "wsl", "linux"):
            if plat in registry.srcroot:
                lines.append(f"  {plat}: {_quote(registry.srcroot[plat])}")
        lines.append("")

    # account_map section (GitHub owner/org -> gh account login)
    if registry.account_map:
        lines.append("account_map:")
        for owner in sorted(registry.account_map.keys()):
            lines.append(f"  {_quote(owner)}: {_quote(registry.account_map[owner])}")
        lines.append("")

    # repos section
    if registry.repos:
        lines.append("repos:")
        for name in sorted(registry.repos.keys()):
            entry = registry.repos[name]
            lines.append(f"  {name}:")
            lines.append(f"    class: {entry.repo_class}")
            # Emit `agent` only when it deviates from the class default
            # (worktree/singleton => on, reference => off) to keep files minimal.
            class_default_agent = entry.repo_class != "reference"
            if entry.agent != class_default_agent:
                lines.append(f"    agent: {'true' if entry.agent else 'false'}")
            if entry.remote:
                lines.append(f"    remote: {_quote(entry.remote)}")
            if entry.account:
                lines.append(f"    account: {_quote(entry.account)}")
            if entry.copilot_account:
                lines.append(f"    copilot_account: {_quote(entry.copilot_account)}")
            if entry.default_branch:
                lines.append(f"    default_branch: {_quote(entry.default_branch)}")
            if entry.tags:
                rendered = ", ".join(_quote(t) for t in entry.tags)
                lines.append(f"    tags: [{rendered}]")
            if entry.contributing:
                lines.append(f"    contributing: {_quote(entry.contributing)}")
            for plat in ("windows", "wsl", "linux"):
                if plat in entry.paths:
                    lines.append(f"    {plat}: {_quote(entry.paths[plat])}")
            lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote(v: str) -> str:
    """Quote a YAML string value if it contains special chars."""
    if any(c in v for c in (":", "#", "'", '"', "\\", "{", "}", "[", "]")):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def get_srcroot(plat: str | None = None) -> str | None:
    """Return the source root for the given (or current) platform."""
    plat = plat or _current_platform()
    registry = read_registry()
    return registry.srcroot.get(plat)


def set_srcroot(path: str, plat: str | None = None) -> None:
    """Set the source root for the given (or current) platform."""
    plat = plat or _current_platform()
    registry = read_registry()
    registry.srcroot[plat] = path
    write_registry(registry)
    output.ok(f"Source root for {plat} set to {path}")


def list_repos(class_filter: str | None = None) -> list[RepoEntry]:
    """Return all repos, optionally filtered by management class.

    The filter accepts new class names (reference/singleton/worktree) and
    legacy type values (project/repo), normalizing both.
    """
    registry = read_registry()
    entries = list(registry.repos.values())
    if class_filter:
        wanted = normalize_class(class_filter)
        entries = [e for e in entries if e.repo_class == wanted]
    return sorted(entries, key=lambda e: e.name)


def find_repo(name: str) -> RepoEntry | None:
    """Find a repo by name."""
    registry = read_registry()
    return registry.repos.get(name)


def inrepo_declared_default_branch(path: str) -> str:
    """Return the branch a checked-out repo declares as its own via its
    in-repo ``.agent-worktrees/config.yaml`` (or legacy equivalents), or
    ``""`` when it declares none / the path is unusable.

    A repo's own ``default_branch`` (e.g. copilot-extensions' ``dev`` --
    a contribution/integration branch distinct from a GitHub-side release
    branch) is authoritative over anything a machine's ``repos.yaml`` or an
    external manifest separately records for it (see ``config.py``'s
    ``_resolve_adoption_defaults_from_registry``, which already treats the
    registry value as a mere fallback for repos that declare none). Callers
    use this to keep clone/add/sync self-deriving that value instead of
    requiring an operator to duplicate it by hand. Never raises.
    """
    try:
        from .config import _load_inrepo_config

        value = _load_inrepo_config(path).get("default_branch")
        return str(value) if value else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Account resolution (repo -> preferred GitHub identity)
# ---------------------------------------------------------------------------
#
# The repo-scoped identity layer: a repo has a preferred ``account`` (a ``gh``
# account login) its git/gh operations run under, so agents never hand-switch
# ``gh auth switch``.  ``resolve_account`` is the single source of truth --
# explicit ``account:`` wins, else the owner is derived from a github.com
# remote, else None (a non-GitHub or underivable remote keeps today's ambient
# behavior; nothing switches).  GitHub-only in v1.

def github_owner(remote: str) -> str | None:
    """Extract the owner from a github.com remote URL (https or ssh form).

    Returns None for non-GitHub remotes (so ADO/gitea derive no account).
    Host matching is boundary-aware: the host must actually *be*
    ``github.com`` (optionally ``www.``, optionally with userinfo on HTTPS),
    not merely contain that substring -- a remote on ``notgithub.com`` or
    ``evilgithub.com`` must not be mistaken for GitHub.
    """
    if not remote:
        return None
    url = remote.strip()
    m = re.match(r"https?://(?:[^@/]+@)?(?:www\.)?github\.com/([^/]+)/", url)
    if m:
        return m.group(1)
    m = re.match(r"(?:ssh://)?git@github\.com[:/]([^/]+)/", url)
    if m:
        return m.group(1)
    return None


def is_https_remote(remote: str) -> bool:
    """True when ``remote`` is specifically an ``https://`` URL.

    Both the persisted credential pin (:func:`git_ops.pin_git_credential`,
    which only ever writes ``credential.https://<host>.*``) and the
    clone-time auth override (:func:`git_ops._auth_config_args_for_url`,
    which only ever produces an ``http.extraheader``) affect HTTPS
    transport exclusively. An SSH remote (``ssh://``/``git@host:owner/repo``)
    uses the ambient SSH key instead and is untouched by either. A plain
    ``http://`` remote is excluded too, for the same reason: git's
    credential config is scheme-specific (``credential.https://...`` never
    matches an ``http://`` fetch), and injecting the OAuth bearer token as an
    ``http.extraheader`` onto plaintext HTTP would additionally send it over
    an unencrypted connection. Treating either as "pinned"/"authed" would be
    a false positive that reports success while changing nothing (or
    changing the wrong thing).
    """
    return remote.strip().lower().startswith("https://")


def derive_https_host(remote: str) -> str | None:
    """Extract the exact hostname from an ``https://`` remote, or None.

    Git's credential store is host-specific (``credential.https://<host>``
    matches by literal hostname), so a checkout on ``www.github.com`` needs
    ``credential.https://www.github.com`` -- a pin hardcoded to
    ``github.com`` never matches it, even though :func:`github_owner`
    accepts the ``www.`` form when *resolving the account*. Callers should
    pass this (falling back to ``pin_git_credential``'s ``"github.com"``
    default only when it returns ``None``) so the pinned host always
    matches the checkout's actual remote.
    """
    if not is_https_remote(remote):
        return None
    m = re.match(r"https://(?:[^@/]+@)?([^/:]+)", remote.strip())
    return m.group(1) if m else None


def github_slug(remote: str) -> str | None:
    """Extract the ``owner/name`` slug from a github.com remote URL.

    Unlike the registry key used to name a repo entry (which can be an
    arbitrary alias, e.g. ``ce`` for ``github.com/example-org/proj``), this is
    the canonical slug `gh`/the GitHub API actually expect as a repo target
    (e.g. for ``repos gh <target> -- ...``). Returns None for a non-GitHub or
    unparseable remote. Same host-boundary-aware matching as
    :func:`github_owner` -- see its docstring.
    """
    if not remote:
        return None
    url = remote.strip()
    m = re.match(
        r"https?://(?:[^@/]+@)?(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        url,
    )
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.match(
        r"(?:ssh://)?git@github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url
    )
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def account_from_map(owner: str | None) -> str | None:
    """Return the ``account_map`` login for a GitHub ``owner``, or None.

    The decoupled org->account layer: a top-level ``account_map:`` in
    repos.yaml maps a GitHub owner/org to the ``gh`` account login its repos
    authenticate as. Case-insensitive on the owner key. Returns None when no
    map entry matches (callers then fall back to the owner itself).
    """
    if not owner:
        return None
    try:
        registry = read_registry()
    except Exception:
        return None
    for key, login in registry.account_map.items():
        if key.casefold() == owner.casefold():
            return login or None
    return None


def resolve_account(entry: RepoEntry | None) -> str | None:
    """Resolve the preferred GitHub account for a repo entry.

    Order: explicit ``account:`` -> ``account_map`` (owner->login) -> owner
    derived from a github.com remote -> None.  None means "no account
    preference" -- git/gh operations use the ambient ``gh`` account exactly as
    before (additive + safe).
    """
    if entry is None:
        return None
    if entry.account:
        return entry.account
    owner = github_owner(entry.remote)
    mapped = account_from_map(owner)
    if mapped:
        return mapped
    return owner


def resolve_copilot_account(entry: RepoEntry | None) -> str | None:
    """Resolve this repo's explicit **Copilot CLI identity** override, or None.

    Repo-keyed only -- unlike :func:`resolve_account`, there is no
    owner-derivation fallback, because a repo with no GitHub owner at all
    (e.g. a private Gitea remote) can still need a pinned Copilot identity.
    Callers wanting the full precedence chain (explicit -> machine default ->
    none) should use :func:`copilot_account_for`, which also consults
    ``config.Config.default_copilot_account``.
    """
    if entry is None:
        return None
    return entry.copilot_account or None


def copilot_account_for(name: str) -> str | None:
    """Resolve the intended Copilot CLI login for a **registered repo name**.

    Order: this repo's explicit ``copilot_account:`` override ->
    the machine's ``default_copilot_account`` (``config.yaml``, machine-local
    or global tier) -> None (no preference; leave Copilot's ambient login
    alone). Unlike the ``account``/``account_map`` chain this never derives
    from a GitHub owner: Copilot's own inference identity is a per-repo/
    per-machine policy independent of where (or whether) a repo is hosted on
    GitHub. See ThomasMichon/copilot-extensions#3296.
    """
    entry = find_repo(name)
    explicit = resolve_copilot_account(entry)
    if explicit:
        return explicit
    try:
        from . import config as _config

        return _config.load_config().default_copilot_account or None
    except Exception:
        return None


def set_copilot_account(name: str, login: str) -> bool:
    """Set the explicit ``copilot_account:`` override for a registered repo.

    Returns False (no-op) if ``name`` isn't a registered repo.
    """
    registry = read_registry()
    entry = registry.repos.get(name)
    if entry is None:
        return False
    registry.repos[name] = replace(entry, copilot_account=login)
    write_registry(registry)
    output.ok(f"{name}: copilot_account -> {login}")
    return True


def unset_copilot_account(name: str) -> bool:
    """Remove a registered repo's explicit ``copilot_account:`` override."""
    registry = read_registry()
    entry = registry.repos.get(name)
    if entry is None or not entry.copilot_account:
        return False
    registry.repos[name] = replace(entry, copilot_account="")
    write_registry(registry)
    output.ok(f"{name}: copilot_account removed")
    return True


def account_for_github_owner(owner: str | None) -> str | None:
    """Resolve the effective account for a github ``owner`` (from a repo slug).

    Order (highest wins):

    1. an explicit ``account:`` override on any registered repo whose github
       remote is owned by ``owner`` (finest grain; owner != account is possible
       for EMU accounts spanning orgs);
    2. the decoupled ``account_map`` (owner->login) in repos.yaml -- the org
       identity layer, which resolves org-owned repos (``github/...``,
       ``example-org/...``) to the correct login even when no gh account is
       named after the org and no repo under it is registered;
    3. the owner itself (correct when owner == login, e.g. a personal/EMU
       user's own repos).

    Returns None only for an empty owner.  Used at gh/git touch-sites that know
    the hosting ``owner/name`` slug but not the registry name.
    """
    if not owner:
        return None
    try:
        registry = read_registry()
    except Exception:
        registry = ReposRegistry()
    for entry in registry.repos.values():
        if not entry.account:
            continue
        eowner = github_owner(entry.remote)
        if eowner and eowner.casefold() == owner.casefold():
            return entry.account
    for key, login in registry.account_map.items():
        if key.casefold() == owner.casefold() and login:
            return login
    return owner


def resolve_slug_owner(target: str | None) -> str | None:
    """Resolve the github ``owner`` a *target* (owner, ``owner/name`` slug, or
    bare **registered repo name**) actually refers to.

    A bare name is ambiguous with a bare owner: resolve it through the same
    registry -> remote -> owner chain ``repos find`` uses, instead of treating
    the literal string as the owner (silently minted under the wrong identity
    -- see #3032). Falls back to the literal string only when it isn't a
    registered repo name, so a bare personal/org owner keeps resolving as
    before. None when a registered repo's remote has no derivable owner.
    """
    if not target:
        return None
    if "/" in target:
        return target.split("/", 1)[0]
    entry = find_repo(target)
    if entry is not None:
        return github_owner(entry.remote)
    return target


def is_unresolved_registered_target(target: str | None) -> bool:
    """True when *target* names a **registered** repo that has no resolvable
    account: no explicit per-repo ``account:`` override, and no derivable
    github owner (e.g. a non-github/Azure DevOps remote) either.

    This is the identity-known-but-unresolvable case #3032 flags as a hazard:
    a caller explicitly named a repo this tool knows about, so silently
    falling back to ambient ``gh`` auth would still risk acting under the
    wrong account. Distinct from an unregistered/ambiguous bare name, where no
    preference exists and ambient auth remains the documented, safe default.
    """
    if not target or "/" in target:
        return False
    entry = find_repo(target)
    if entry is None:
        return False
    return resolve_account(entry) is None


def account_for_github_slug(slug: str | None) -> str | None:
    """Resolve the effective account for a github ``owner/name`` slug, a bare
    ``owner``, or a bare registered repo *name*.

    A bare registered repo name resolves via its own matched entry's
    :func:`resolve_account` (explicit ``account:`` -> ``account_map[owner]`` ->
    the remote owner itself -> None) -- never by handing the owner alone to
    the owner-*wide* resolver (:func:`account_for_github_owner`), which scans
    every registered repo and can return a *different, sibling* repo's
    explicit account for the same owner (see #3032 follow-up: with `proj-a`
    unoverridden and `proj-b` -> `account-b` under the same owner,
    `account-for proj-a` must not resolve to `account-b`).
    """
    if not slug:
        return None
    if "/" not in slug:
        entry = find_repo(slug)
        if entry is not None:
            return resolve_account(entry)
    owner = resolve_slug_owner(slug)
    return account_for_github_owner(owner)


def set_account_map(owner: str, login: str) -> None:
    """Set the ``account_map`` entry for a GitHub owner (case-preserving key).

    Replaces any existing entry for the same owner case-insensitively so a
    re-set with different casing does not leave a duplicate.
    """
    registry = read_registry()
    for key in list(registry.account_map.keys()):
        if key.casefold() == owner.casefold():
            del registry.account_map[key]
    registry.account_map[owner] = login
    write_registry(registry)
    output.ok(f"account_map: {owner} -> {login}")


def unset_account_map(owner: str) -> bool:
    """Remove the ``account_map`` entry for a GitHub owner. Case-insensitive."""
    registry = read_registry()
    removed = False
    for key in list(registry.account_map.keys()):
        if key.casefold() == owner.casefold():
            del registry.account_map[key]
            removed = True
    if removed:
        write_registry(registry)
        output.ok(f"account_map: removed {owner}")
    return removed


@dataclass
class AccountResolution:
    """How a repo's gh account resolves at registration time.

    ``needs_clarify`` is the actionable signal: it is True only for the
    **owner-fallback** case where the owner is a github org that is *not* an
    authenticated ``gh`` account (so the derived login can't actually auth) --
    the moment a register/adopt flow should ask the operator to pin an account.
    """

    owner: str | None
    login: str | None
    source: str  # "explicit" | "account_map" | "sibling" | "owner-fallback" | "none"
    authenticated: bool
    needs_clarify: bool


def resolve_registration_account(
    remote: str, explicit_account: str = "",
) -> AccountResolution:
    """Resolve the account for a repo being registered, flagging ambiguity.

    Mirrors :func:`account_for_github_owner`'s order (explicit → account_map →
    sibling repo's explicit → owner) but additionally reports the resolution
    *source* and whether the owner-fallback landed on a login that isn't an
    authenticated ``gh`` account. GitHub remotes only; non-GitHub/underivable
    remotes resolve to ``none`` (ambient auth, never clarified). When ``gh`` is
    unavailable, authentication can't be checked -- we assume authenticated so a
    register never nags on a box without ``gh``.
    """
    owner = github_owner(remote)

    def _authed(login: str | None) -> bool:
        if not login:
            return False
        try:
            from . import git_ops
            if shutil.which("gh") is None:
                return True  # can't verify -> don't nag
            return git_ops.gh_token_for_account(login) is not None
        except Exception:
            return True

    if explicit_account:
        return AccountResolution(
            owner, explicit_account, "explicit", _authed(explicit_account), False,
        )
    if not owner:
        return AccountResolution(None, None, "none", False, False)

    mapped = account_from_map(owner)
    if mapped:
        return AccountResolution(owner, mapped, "account_map", _authed(mapped), False)

    # A sibling repo under the same owner with an explicit account: counts as
    # resolved (account_for_github_owner returns it when != owner).
    effective = account_for_github_owner(owner)
    if effective and effective.casefold() != owner.casefold():
        return AccountResolution(owner, effective, "sibling", _authed(effective), False)

    # Owner-fallback: the login *is* the owner. Fine when the owner is itself a
    # gh account (personal/EMU repo); needs clarification when it is not (org).
    authed = _authed(owner)
    return AccountResolution(owner, owner, "owner-fallback", authed, not authed)


def add_repo(
    name: str,
    path: str,
    *,
    repo_class: str = "reference",
    remote: str = "",
    default_branch: str = "",
    tags: list[str] | None = None,
    contributing: str = "",
    account: str = "",
    agent: bool | None = None,
    plat: str | None = None,
) -> RepoEntry:
    """Register a repo at a known path.  Merges with existing entry."""
    plat = plat or _current_platform()
    registry = read_registry()
    repo_class = normalize_class(repo_class)

    existing = registry.repos.get(name)
    if existing:
        existing.paths[plat] = path
        if remote:
            existing.remote = remote
        # Only override class when explicitly upgraded away from the
        # default; this lets `add` re-register a path without clobbering
        # a deliberate classification.
        if repo_class != "reference":
            existing.repo_class = repo_class
        if default_branch:
            existing.default_branch = default_branch
        if tags:
            existing.tags = list(tags)
        if contributing:
            existing.contributing = contributing
        if account:
            existing.account = account
        if agent is not None:
            existing.agent = agent
        entry = existing
    else:
        entry = RepoEntry(
            name=name,
            repo_class=repo_class,
            remote=remote,
            default_branch=default_branch,
            tags=list(tags) if tags else [],
            contributing=contributing,
            account=account,
            agent=agent if agent is not None else (repo_class != "reference"),
            paths={plat: path},
        )
        registry.repos[name] = entry

    write_registry(registry)
    agent_note = "" if entry.agent else " no-agent"
    output.ok(
        f"Repo '{name}' registered at {path} ({plat}) [{entry.repo_class}{agent_note}]"
    )
    _best_effort_pin_credential(entry, plat)
    return entry


def _best_effort_pin_credential(entry: RepoEntry, plat: str) -> None:
    """Pin the repo-local git credential for ``entry`` if the account already
    resolves unambiguously (never prompts, never raises).

    Called from every ``add_repo`` caller -- ``repos add``, ``repos clone``,
    plugin install/adoption, project-entry registration -- so the pin is a
    default outcome of *any* registration path, not only the CLI ``add``/
    ``clone`` commands that separately call ``_clarify_registration_account``
    (which additionally prompts to resolve an org-owned remote's ambiguous
    account; once it does, it re-pins with the newly chosen login).

    Uses ``entry.local_path(plat)`` (not a raw caller-supplied path) so a
    home-relative registration (e.g. ``~/src/repo``) still resolves to a real
    directory -- ``Path("~/src/repo").is_dir()`` is always False, which would
    otherwise make ``pin_git_credential`` silently no-op. Skips SSH remotes
    (see :func:`is_https_remote`): the pin only ever writes
    ``credential.https://<host>.*``, which an SSH transport never consults.
    """
    if not is_https_remote(entry.remote):
        return
    try:
        res = resolve_registration_account(entry.remote, entry.account)
        if res.needs_clarify or not res.login or not res.owner:
            return
        path = entry.local_path(plat)
        if not path:
            return
        from . import git_ops
        host = derive_https_host(entry.remote) or "github.com"
        git_ops.pin_git_credential(path, res.login, host=host)
    except Exception:
        pass


def remove_repo(name: str) -> bool:
    """Remove a repo from the registry.  Returns True if it existed."""
    registry = read_registry()
    if name not in registry.repos:
        return False
    del registry.repos[name]
    write_registry(registry)
    output.ok(f"Repo '{name}' removed from registry")
    return True


def clone_repo(
    remote: str,
    name: str | None = None,
    target: str | None = None,
) -> RepoEntry | None:
    """Clone a repo to the srcroot (or target) and register it.

    Returns the new RepoEntry, or None on failure.
    """
    plat = _current_platform()

    # Infer name from remote URL if not provided
    if not name:
        name = _name_from_remote(remote)
    if not name:
        output.err("Cannot infer repo name from remote URL")
        return None

    # Determine target directory
    if not target:
        root = get_srcroot(plat)
        if not root:
            output.err(
                f"No srcroot configured for {plat}. "
                f"Set one with: agent-worktrees repos srcroot --set <path>"
            )
            return None
        target = str(Path(root).expanduser() / name)

    target_path = Path(target)
    if target_path.exists():
        output.warn(f"Directory already exists: {target}")
        # Still register it
        return add_repo(name, target, remote=remote)

    # Clone. A private repo owned by a different account than gh's currently
    # active one would otherwise 403/404 on this very first fetch -- the
    # repo-local credential pin add_repo installs below only exists *after*
    # a successful clone, so inject the same one-shot cross-account auth
    # override the fetch/push paths use, resolved directly from the clone
    # URL (there is no checked-out remote yet to resolve a name against).
    # HTTPS-only: the override is an http.extraheader, which an SSH
    # transport (git@host:owner/repo, ssh://...) never consults -- for an
    # SSH remote the ambient SSH key is what actually authenticates, so
    # injecting this would be a no-op that falsely implies auth was handled.
    from . import git_ops
    extra_args: list[str] = []
    if is_https_remote(remote):
        try:
            extra_args = git_ops._auth_config_args_for_url(remote)
        except Exception:
            extra_args = []
    argv = ["git", *extra_args, "clone", remote, str(target_path)]

    def _redact(text: str) -> str:
        # Git itself can echo the injected -c argument (e.g. with tracing
        # enabled) into stdout/stderr, and some exceptions (subprocess.
        # TimeoutExpired) embed the full argv in their own __str__ -- strip
        # the token wherever it could surface, not only in our own messages.
        for arg in extra_args:
            if arg.startswith("http.extraheader="):
                text = text.replace(arg, "http.extraheader=<redacted>")
        # With GIT_TRACE_CURL/GIT_CURL_VERBOSE set in the ambient
        # environment, git can additionally echo the resolved request
        # header itself (e.g. "=> Send header: Authorization: Basic
        # <base64>") straight into stderr, independent of the -c argv
        # form above -- redact that pattern too.
        text = re.sub(
            r"(Authorization:\s*(?:Basic|Bearer)\s+)\S+", r"\1<redacted>",
            text, flags=re.IGNORECASE,
        )
        # A remote URL with embedded userinfo (https://user:token@host/...)
        # is a second, independent credential surface -- e.g. a caller that
        # named such a URL as `remote`. Strip it wherever a URL appears,
        # including inside the argv this function also redacts.
        text = re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", text)
        return text

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            output.err(f"git clone failed: {_redact(result.stderr.strip())}")
            return None
    except Exception as e:
        redacted_argv = _redact(str(git_ops._redact_args(argv)))
        output.err(f"Clone failed running {redacted_argv}: {_redact(str(e))}")
        return None

    output.ok(f"Cloned {remote} to {target}")

    # A repo that declares its own default_branch (e.g. copilot-extensions'
    # `dev` -- a contribution branch distinct from GitHub's advertised HEAD,
    # which is what a plain `git clone` always checks out) is authoritative:
    # switch the fresh checkout onto it now, and register that value instead
    # of requiring an operator to separately know and pass --default-branch.
    declared_branch = inrepo_declared_default_branch(str(target_path))
    if declared_branch:
        try:
            current = subprocess.run(
                ["git", "-C", str(target_path), "branch", "--show-current"],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
            if current != declared_branch:
                checkout = subprocess.run(
                    ["git", "-C", str(target_path), "checkout", declared_branch],
                    capture_output=True, text=True, timeout=30,
                )
                if checkout.returncode == 0:
                    output.ok(f"Checked out declared default branch '{declared_branch}'")
                else:
                    output.warn(
                        f"Could not check out declared default branch "
                        f"'{declared_branch}': {_redact(checkout.stderr.strip())}"
                    )
        except Exception:
            pass

    return add_repo(name, target, remote=remote, default_branch=declared_branch)


def _name_from_remote(remote: str) -> str | None:
    """Extract a repo name from a git remote URL."""
    # Handle SSH: git@github.com:user/repo.git
    # Handle HTTPS: https://github.com/user/repo.git
    # Handle ADO: https://org.visualstudio.com/proj/_git/repo
    name = remote.rstrip("/")
    if name.endswith(".git"):
        name = name[:-4]
    # Take the last path segment
    name = name.rsplit("/", 1)[-1]
    # Handle SSH colon syntax
    if ":" in name:
        name = name.rsplit(":", 1)[-1]
    return name if name else None


def resolve_path(name: str, plat: str | None = None) -> str | None:
    """Resolve a repo name to its local path.

    Checks the registry first, then tries srcroot + name as a fallback.
    """
    plat = plat or _current_platform()
    entry = find_repo(name)
    if entry:
        p = entry.local_path(plat)
        if p:
            return p

    # Fallback: srcroot / name
    root = get_srcroot(plat)
    if root:
        candidate = Path(root).expanduser() / name
        if candidate.exists():
            return str(candidate)

    return None


@dataclass
class CredentialPinResult:
    """Outcome of one repo's credential-pin backfill attempt."""

    name: str
    # "pinned" | "skipped" | "needs_clarify" | "no_path" | "not_github"
    # | "not_registered" | "ssh_remote" | "not_https"
    status: str
    login: str | None
    detail: str


def backfill_credential_pins(
    name: str | None = None, *, plat: str | None = None,
) -> list[CredentialPinResult]:
    """Retrofit the repo-local git credential pin onto already-registered repos.

    New registrations (``repos add``/``repos clone``/adopt) pin automatically
    at registration time (see ``_clarify_registration_account`` in
    ``__main__``); this covers repos registered *before* that existed, or
    whose checkout predates a machine's account_map entry.

    Restricts to ``name`` when given, else every registered repo -- an
    unrecognized ``name`` reports a single ``not_registered`` result rather
    than silently falling back to "every repo" (a typo must never expand
    scope). Skips (rather than errors) a repo with no resolvable local path,
    a non-GitHub remote, or an account that still needs interactive
    clarification (an org-owned remote with no account_map/explicit
    override) -- those cases require ``repos account set``/an operator
    choice, not a silent guess.
    """
    from . import git_ops

    registry = read_registry()
    if name is not None:
        if name not in registry.repos:
            return [
                CredentialPinResult(name, "not_registered", None, "no such repo in the registry")
            ]
        entries = [registry.repos[name]]
    else:
        entries = list(registry.repos.values())
    results: list[CredentialPinResult] = []
    for entry in entries:
        if not entry.remote:
            results.append(
                CredentialPinResult(entry.name, "not_github", None, "no remote configured")
            )
            continue
        if not is_https_remote(entry.remote):
            # An SSH remote (git@host:owner/repo, ssh://...) and a plain
            # http:// remote both fail is_https_remote (see its docstring),
            # but for different reasons -- distinguish them so the report
            # never claims "SSH remote" for a checkout that is, in fact,
            # unencrypted HTTP.
            is_ssh = bool(re.match(r"^(?:ssh://|git@)", entry.remote.strip(), re.IGNORECASE))
            if is_ssh:
                results.append(
                    CredentialPinResult(
                        entry.name, "ssh_remote", None,
                        "SSH remote -- the pin only affects HTTPS transport",
                    )
                )
            else:
                results.append(
                    CredentialPinResult(
                        entry.name, "not_https", None,
                        "non-HTTPS remote -- the pin only affects HTTPS transport",
                    )
                )
            continue
        path = entry.local_path(plat or _current_platform())
        if not path or not Path(path).is_dir():
            results.append(
                CredentialPinResult(
                    entry.name, "no_path", None, "no local checkout on this machine"
                )
            )
            continue
        res = resolve_registration_account(entry.remote, entry.account)
        if res.owner is None:
            results.append(
                CredentialPinResult(entry.name, "not_github", None, "non-GitHub remote")
            )
            continue
        if res.needs_clarify or not res.login:
            results.append(
                CredentialPinResult(
                    entry.name, "needs_clarify", res.login,
                    f"owner '{res.owner}' has no resolvable account -- "
                    f"run: repos account set {res.owner} <login>",
                )
            )
            continue
        host = derive_https_host(entry.remote) or "github.com"
        if git_ops.pin_git_credential(path, res.login, host=host):
            results.append(
                CredentialPinResult(entry.name, "pinned", res.login, f"pinned to {res.login}")
            )
        else:
            results.append(
                CredentialPinResult(
                    entry.name, "skipped", res.login,
                    "pin not applied (already the active gh account, gh "
                    "unavailable, or not a git checkout)",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Migration from the legacy ~/.git-repos registry
# ---------------------------------------------------------------------------

def _git_repos_path() -> Path:
    """Path to the legacy ~/.git-repos registry file."""
    return Path.home() / ".git-repos"


def _adopted_project_names() -> set[str]:
    """Names of repos adopted as agent-worktrees projects (projects.yaml).

    Used by migration classification and cross-project tracking lookup.
    """
    projects_path = registry_paths.registry_path("projects.yaml")
    if not projects_path.exists():
        return set()
    try:
        data = yaml.safe_load(projects_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("projects"), dict):
            return set(data["projects"].keys())
    except Exception:
        pass
    return set()


def migrate_git_repos(
    *,
    default_class: str = "singleton",
    plat: str | None = None,
) -> tuple[int, int]:
    """Import the legacy ``~/.git-repos`` registry into ``repos.yaml``.

    The legacy file uses a single ``srcroot`` string and per-repo
    ``{remote, default_branch, tags, path, contributing}``.  Each entry is
    mapped onto the current platform in ``repos.yaml``:

    - ``srcroot`` -> ``srcroot[<platform>]``
    - per-repo ``path`` (or ``srcroot/<name>``) -> ``paths[<platform>]``
    - ``remote`` / ``default_branch`` / ``tags`` / ``contributing`` copied

    Management class is inferred: repos adopted as agent-worktrees
    projects become ``worktree``; everything else uses ``default_class``
    (``singleton`` by default -- a tracked, editable anchor checkout).

    Existing ``repos.yaml`` entries are merged, never clobbered: an
    already-set class is preserved.  ``~/.git-repos`` itself is left
    untouched.  Returns ``(migrated, skipped)`` counts.
    """
    plat = plat or _current_platform()
    src = _git_repos_path()
    if not src.exists():
        output.warn(f"No legacy registry found at {src}")
        return (0, 0)

    try:
        legacy = yaml.safe_load(src.read_text(encoding="utf-8"))
    except Exception as e:
        output.err(f"Could not parse {src}: {e}")
        return (0, 0)
    if not isinstance(legacy, dict):
        output.err(f"{src} is not a valid registry")
        return (0, 0)

    registry = read_registry()
    adopted = _adopted_project_names()

    # Map the legacy single srcroot onto the current platform.
    legacy_srcroot = legacy.get("srcroot")
    if isinstance(legacy_srcroot, str) and legacy_srcroot:
        registry.srcroot.setdefault(plat, legacy_srcroot)

    migrated = 0
    skipped = 0
    raw_repos = legacy.get("repos", {})
    if not isinstance(raw_repos, dict):
        raw_repos = {}

    for name, entry in raw_repos.items():
        if not isinstance(entry, dict):
            skipped += 1
            continue

        # Resolve the local path: explicit "path" wins, else srcroot/name.
        path = entry.get("path")
        if not path and isinstance(legacy_srcroot, str) and legacy_srcroot:
            path = str(Path(legacy_srcroot) / name)
        if not path:
            skipped += 1
            output.warn(f"  {name}: no path and no srcroot -- skipped")
            continue

        raw_tags = entry.get("tags", [])
        tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []

        # Infer class: adopted projects are worktree-managed; otherwise
        # the caller-provided default (singleton).
        inferred = "worktree" if name in adopted else default_class

        existing = registry.repos.get(name)
        if existing:
            existing.paths.setdefault(plat, str(path))
            if not existing.remote:
                existing.remote = entry.get("remote", "")
            if not existing.default_branch:
                existing.default_branch = entry.get("default_branch", "")
            if not existing.tags:
                existing.tags = tags
            if not existing.contributing:
                existing.contributing = entry.get("contributing", "")
            # Preserve an already-deliberate class; only fill if unset
            # (defaults to reference on a bare entry).
            if existing.repo_class == "reference" and inferred != "reference":
                existing.repo_class = normalize_class(inferred)
        else:
            registry.repos[name] = RepoEntry(
                name=name,
                repo_class=normalize_class(inferred),
                remote=entry.get("remote", ""),
                default_branch=entry.get("default_branch", ""),
                tags=tags,
                contributing=entry.get("contributing", ""),
                paths={plat: str(path)},
            )
        migrated += 1

    write_registry(registry)
    return (migrated, skipped)


# ---------------------------------------------------------------------------
# Multi-repo git hygiene (status / sync)
# ---------------------------------------------------------------------------

@dataclass
class RepoStatus:
    """Working-tree status for a single repo checkout."""

    name: str
    repo_class: str
    path: str | None = None
    present: bool = False
    branch: str = ""
    dirty: bool = False
    ahead: int = 0
    behind: int = 0
    error: str = ""


def _git(path: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def repo_status(entry: RepoEntry, plat: str | None = None) -> RepoStatus:
    """Compute working-tree status for one repo entry."""
    plat = plat or _current_platform()
    st = RepoStatus(name=entry.name, repo_class=entry.repo_class)
    path = entry.local_path(plat)
    st.path = path
    if not path or not (Path(path) / ".git").exists():
        return st
    st.present = True
    try:
        st.branch = _git(path, "branch", "--show-current").stdout.strip()
        st.dirty = bool(_git(path, "status", "--porcelain").stdout.strip())
        branch = entry.default_branch or st.branch
        if branch:
            counts = _git(
                path, "rev-list", "--left-right", "--count",
                f"origin/{branch}...HEAD",
            )
            if counts.returncode == 0:
                parts = counts.stdout.split()
                if len(parts) == 2:
                    st.behind, st.ahead = int(parts[0]), int(parts[1])
    except Exception as e:  # pragma: no cover - defensive
        st.error = str(e)
    return st


def _filter_repos(
    entries: list[RepoEntry],
    *,
    tag: str | None,
    class_filter: str | None,
    names: tuple[str, ...] | None = None,
) -> list[RepoEntry]:
    if class_filter:
        wanted = normalize_class(class_filter)
        entries = [e for e in entries if e.repo_class == wanted]
    if tag:
        entries = [e for e in entries if tag in e.tags]
    if names:
        entries = [e for e in entries if e.name in set(names)]
    return entries


def status_all(
    *,
    tag: str | None = None,
    class_filter: str | None = None,
    plat: str | None = None,
) -> list[RepoStatus]:
    """Return status for all registered repos (optionally filtered)."""
    entries = _filter_repos(
        list(read_registry().repos.values()),
        tag=tag, class_filter=class_filter,
    )
    entries.sort(key=lambda e: e.name)
    return [repo_status(e, plat) for e in entries]


def sync_repo(entry: RepoEntry, plat: str | None = None) -> tuple[str, str]:
    """Fetch and fast-forward one repo's default branch.

    Returns ``(state, detail)`` where state is one of: ``synced``,
    ``skipped``, ``missing``, ``error``.  Dirty trees and detached HEADs are
    skipped (never force-updated). A repo's own in-repo-declared
    ``default_branch`` (via ``inrepo_declared_default_branch``) is
    authoritative over the registry's ``entry.default_branch`` -- when the
    checkout is clean but sitting on a *different* branch than the declared
    one (e.g. a stale clone left on GitHub's advertised HEAD instead of the
    repo's actual contribution branch), this switches it back rather than
    permanently skipping every future sync.
    """
    plat = plat or _current_platform()
    path = entry.local_path(plat)
    if not path or not (Path(path) / ".git").exists():
        return ("missing", "not checked out")
    branch = inrepo_declared_default_branch(path) or entry.default_branch
    try:
        if _git(path, "status", "--porcelain").stdout.strip():
            return ("skipped", "working tree dirty")
        current = _git(path, "branch", "--show-current").stdout.strip()
        if not current:
            # Detached HEAD (e.g. a reference repo pinned at a tag/commit):
            # never fast-forward it.
            return ("skipped", "detached HEAD")
        if branch and current != branch:
            checkout = _git(path, "checkout", branch)
            if checkout.returncode != 0:
                return (
                    "skipped",
                    f"on '{current}', not declared default '{branch}' "
                    f"(checkout failed: {checkout.stderr.strip() or 'unknown error'})",
                )
            current = branch
        target = branch or current
        # Use git_ops.fetch (not the plain local _git helper) so a cross-account
        # remote authenticates the same way every other agent-worktrees git flow
        # does -- resolving the repo's configured account and injecting a scoped
        # credential override when it differs from the active `gh` account.
        # `sync_repo`'s ambient-credential fetch previously bypassed that,
        # failing private repos owned by a non-active account (dotfiles#2069).
        git_ops.fetch("origin", cwd=path, timeout=180)
        if not target:
            return ("skipped", "no branch to fast-forward")
        ff = _git(path, "merge", "--ff-only", f"origin/{target}")
        if ff.returncode != 0:
            return ("skipped", "not fast-forwardable (diverged)")
        return ("synced", target)
    except git_ops.GitError as e:
        return ("error", e.stderr or str(e))
    except Exception as e:
        return ("error", str(e))


def sync_all(
    *,
    tag: str | None = None,
    class_filter: str | None = None,
    names: tuple[str, ...] | None = None,
    plat: str | None = None,
) -> list[tuple[str, str, str]]:
    """Fetch + ff-merge registered repos, optionally filtered by ``names``
    (an unregistered name reports its own not-registered result)."""
    all_entries = list(read_registry().repos.values())
    entries = _filter_repos(all_entries, tag=tag, class_filter=class_filter, names=names)
    entries.sort(key=lambda e: e.name)
    results = [(e.name, *sync_repo(e, plat)) for e in entries]
    if names:
        known = {e.name for e in all_entries}
        results += [(n, "error", "not registered") for n in sorted(set(names) - known)]
    return results
