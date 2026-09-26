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
import re
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dependency
    yaml = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

_REPOS_YAML_ENV = "AGENT_WORKTREES_REPOS_YAML"
_REPOS_YAML_DEFAULT = "~/.agent-worktrees/repos.yaml"
_GIT_REMOTE_RE = re.compile(
    r"^(?:https?://|git@|ssh://(?:git@)?)"
    r"(?P<host>[^/:]+)[:/]+(?P<path>.+?)(?:\.git)?/?$"
)


def _normalize_git_remote(url: str) -> str | None:
    """Normalize a git remote URL to ``host/owner/repo`` for comparison.

    Matches both the ``https://host/owner/repo(.git)`` and
    ``git@host:owner/repo(.git)`` forms so a registry entry authored with
    either style still matches a locally-configured remote in the other.
    """
    text = url.strip()
    match = _GIT_REMOTE_RE.match(text)
    if not match:
        return None
    return f"{match.group('host').lower()}/{match.group('path').lower()}"


def _run_git(root: Path, *args: str) -> str | None:
    """Run a git command in ``root``; return stdout on success, else None.

    Any failure (git missing, not a real git checkout, timeout, non-zero
    exit, detached HEAD for branch queries, etc.) is swallowed to None --
    this is an input to a fail-safe trust decision, never something that
    should raise into a config-loading path.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


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
    path = Path(os.environ.get(_REPOS_YAML_ENV, _REPOS_YAML_DEFAULT)).expanduser()
    if not path.is_file():
        log.debug("agent-worktrees repos registry not found at %s", path)
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        log.warning("failed to parse agent-worktrees repos registry at %s", path)
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

    remote = _run_git(root, "remote", "get-url", "origin")
    if remote is None:
        log.debug("%s: no git remote -- repo-local config untrusted", root)
        return False
    default_branch = _registered_default_branch(remote)
    if default_branch is None:
        log.warning(
            "%s: remote %r is not a registered agent-worktrees project -- "
            "ignoring its repo-local config",
            root,
            remote,
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
