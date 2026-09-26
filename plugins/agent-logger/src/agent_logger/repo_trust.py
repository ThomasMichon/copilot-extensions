"""Registered-project trust gate for repo-local config discovery.

Repo-local config lets a *checked-in file* hand this machine directives --
where to sync session data, how to write logs, what schema fields to trust.
Reading that file from an arbitrary local checkout would let anyone who gets
us to `git clone` their repo (e.g. a public repo crafted to look interesting,
with our own config filenames planted in it) redirect a facility machine's
behavior without a human ever having reviewed or approved that repo.

The trust boundary is therefore: repo-local config is honored ONLY for a
checkout of a repo the operator has explicitly *registered* as a known
project (``agent-worktrees repos add`` -- an affirmative, one-time decision,
never silently inferred from "someone cloned this locally"), and only when
that checkout's current branch is the repo's registered ``default_branch``
(never an arbitrary feature/PR branch, which could carry an unreviewed
config change). Both checks fail *safe*: any ambiguity (no remote, no
registry entry, detached HEAD, registry unreadable) is treated as
"untrusted", which simply means repo-local config is treated as absent --
never an error, since plenty of legitimate checkouts (a fresh clone with no
committed config at all) look the same from here.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path

from plugin_activation import normalize_remote

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dependency
    yaml = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

_REPOS_YAML_ENV = "AGENT_WORKTREES_REPOS_YAML"


def _normalize_git_remote(url: str) -> str | None:
    """Normalize a git remote URL for comparison, using the shared,
    already-tested normalizer (``plugin_activation.normalize_remote``) that
    agent-worktrees' own repository-identity resolution relies on --
    handling the ``https://host/owner/repo(.git)``, ``git@host:owner/repo``,
    and ``ssh://`` forms uniformly, folding host case but deliberately
    preserving URL *path* case (a self-hosted Git server, e.g. Gitea, can be
    case-sensitive on its filesystem).

    GitHub itself is known case-insensitive for ``owner/repo``, so -- exactly
    mirroring ``agent_worktrees.project_state._normalized_repository_remote``
    -- a ``github.com`` remote gets a further full casefold on top.
    """
    normalized = normalize_remote(url)
    if normalized is None or normalized.startswith("file-relative:"):
        # A relative/unparseable value has no stable, comparable identity.
        return None
    if normalized.startswith("network:github.com/"):
        normalized = normalized.casefold()
    return normalized


# Ambient Git environment variables that redirect git's notion of "which
# repository" regardless of an explicit `-C <root>` -- a stale/leaked
# GIT_DIR (etc.) in the calling process's environment could otherwise make
# this probe inspect a different repository than `root`, decoupling the
# trust decision from the config actually being loaded. Cleared on every
# invocation below; mirrors the same clearing in aggregate.py's own
# `_run_git`.
_AMBIENT_GIT_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _run_git(root: Path, *args: str) -> str | None:
    """Run a git command in ``root``; return stdout on success, else None.

    Any failure (git missing, not a real git checkout, timeout, non-zero
    exit, detached HEAD for branch queries, etc.) is swallowed to None --
    this is an input to a fail-safe trust decision, never something that
    should raise into a config-loading path.
    """
    env = os.environ.copy()
    for key in _AMBIENT_GIT_ENV_VARS:
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _default_repos_yaml_root() -> Path:
    """Mirror agent-worktrees' own legacy registry-root fallback (see
    ``agent_worktrees.registry_paths._legacy_root``): honors ``$AGENT_HOME``
    so a machine using that override is not silently treated as an
    unregistered install.

    Does NOT replicate agent-worktrees' further "validated installation
    context" resolution (its ``COPILOT_EXTENSIONS_CONTEXT``-gated
    marketplace-isolation payload-root indirection) -- that would require
    importing agent-worktrees' own installed package, which is not a runtime
    dependency plugins take on each other (agent-bridge's own repos.yaml
    resolution has this identical, pre-existing scope; see
    ``agent_bridge.agent_registry_common._REPOS_YAML_DEFAULT``).
    """
    override = os.environ.get("AGENT_HOME", "").strip()
    if override:
        return Path(override) / ".agent-worktrees"
    if platform.system() == "Windows":
        home = Path(os.environ.get("USERPROFILE") or Path.home())
    else:
        home = Path.home()
    return home / ".agent-worktrees"


def _registered_default_branch(remote_url: str) -> str | None:
    """Look up ``remote_url`` in the agent-worktrees repos registry.

    Returns the registered ``default_branch`` (falling back to ``master``,
    matching agent-worktrees' own convention) when the remote matches a
    registered entry, or None when the registry is unreadable/missing or no
    entry matches -- i.e. the repo is not a known, operator-registered
    project.
    """
    if yaml is None:  # pragma: no cover - pyyaml is a hard dependency
        return None
    normalized = _normalize_git_remote(remote_url)
    if normalized is None:
        return None
    override = os.environ.get(_REPOS_YAML_ENV, "").strip()
    path = (
        Path(override).expanduser()
        if override
        else _default_repos_yaml_root() / "repos.yaml"
    )
    if not path.is_file():
        log.debug("agent-worktrees repos registry not found at %s", path)
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        log.warning("failed to parse agent-worktrees repos registry at %s", path)
        return None
    if not isinstance(data, dict):
        # Malformed registry (e.g. a top-level list/scalar) is exactly the
        # same "can't confirm registration" case as unreadable/missing --
        # fail safe to untrusted, never raise into a config-loading path.
        return None
    repos = data.get("repos")
    if not isinstance(repos, dict):
        return None
    for entry in repos.values():
        if not isinstance(entry, dict):
            continue
        entry_remote = entry.get("remote")
        if not isinstance(entry_remote, str):
            continue
        if _normalize_git_remote(entry_remote) == normalized:
            return str(entry.get("default_branch") or "master")
    return None


def _git_remote_urls(root: Path) -> list[str]:
    """Return every configured remote URL in ``root`` (any local name).

    agent-worktrees supports registering a project under a non-``origin``
    local remote name (e.g. ``upstream``), so the trust decision must match
    against ALL of a checkout's remotes, not assume ``origin`` -- a
    registered checkout using a different local name would otherwise be
    silently treated as unregistered.
    """
    output = _run_git(root, "config", "--get-regexp", r"^remote\..*\.url$")
    if output is None:
        return []
    urls = []
    for line in output.splitlines():
        _, _, url = line.partition(" ")
        if url:
            urls.append(url)
    return urls


def repo_config_is_trusted(root: Path) -> bool:
    """Is ``root`` a registered project checked out on its default branch?

    See the module docstring above for the full threat model. Set
    ``$AGENT_LOGGER_TRUST_REPO_CONFIG=1`` to bypass this gate for a single
    machine after the operator has independently confirmed the checkout is
    safe (e.g. local development against an unregistered clone); this is a
    machine-local environment choice, never something a repo can set for
    itself.
    """
    override = os.environ.get("AGENT_LOGGER_TRUST_REPO_CONFIG", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True

    remotes = _git_remote_urls(root)
    if not remotes:
        log.debug("%s: no git remotes -- repo-local config untrusted", root)
        return False
    default_branch = None
    for remote in remotes:
        default_branch = _registered_default_branch(remote)
        if default_branch is not None:
            break
    if default_branch is None:
        log.warning(
            "%s: no remote (%s) is a registered agent-worktrees project -- "
            "ignoring its repo-local config",
            root,
            ", ".join(remotes),
        )
        return False
    # ``symbolic-ref`` (not ``rev-parse --abbrev-ref``) so a fresh checkout
    # with no commits yet still resolves its branch name instead of failing;
    # it still fails (fail-safe -> untrusted) on a genuinely detached HEAD.
    branch = _run_git(root, "symbolic-ref", "--short", "HEAD")
    if branch != default_branch:
        log.warning(
            "%s: checked out on %r, not the registered default branch %r -- "
            "ignoring its repo-local config",
            root,
            branch,
            default_branch,
        )
        return False
    return True
