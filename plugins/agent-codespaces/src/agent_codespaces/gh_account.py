"""Per-repo gh account resolution for host-side ``gh`` operations.

The multi-account seam.  agent-codespaces' host-side ``gh`` calls (``gh
codespace list/create/delete/stop/ssh``, ``gh api``) must run under the ``gh``
account that can access the *target repo's* GitHub org -- not whatever account
happens to be active in the ``gh`` keyring.  With two accounts backing
different orgs (e.g. ``ThomasMichon`` for ``github/*`` and ``example-operator``
for ``example-org/*``), running under the wrong one hides/《403》s the other
org's CodeSpaces entirely (#195, #190).

The owner->login mapping is owned by **agent-worktrees** (its ``repos.yaml``
``account_map`` + ``accounts.yaml`` catalog).  This module shells out to
``agent-worktrees repos account-for <owner/name>`` -- loose coupling, because
each plugin has its own venv and a cross-plugin Python import is fragile -- and
mints a per-account ``GH_TOKEN`` via ``gh auth token --user <login>`` for the
subprocess environment.

Everything degrades to today's **ambient** behavior when agent-worktrees or
``gh`` is unavailable, or when no account maps for the owner -- so wiring this
in is additive and safe: a repo with no mapping behaves exactly as before.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess

from agent_procutil import no_window_flags

log = logging.getLogger("agent-codespaces")


def _creation_flags() -> int:
    return no_window_flags()


def _agent_worktrees_bin() -> str | None:
    """Locate the ``agent-worktrees`` CLI on PATH, or None."""
    return shutil.which("agent-worktrees")


@functools.lru_cache(maxsize=256)
def account_for_repo(slug: str | None) -> str | None:
    """Resolve the gh login for a repo ``owner/name`` slug, or None.

    Shells ``agent-worktrees repos account-for <slug>``.  Returns None when
    agent-worktrees is absent, errors, or reports no preference (the caller
    then falls back to the ambient ``gh`` account -- today's behavior).
    """
    if not slug:
        return None
    aw = _agent_worktrees_bin()
    if not aw:
        return None
    try:
        result = subprocess.run(
            [aw, "repos", "account-for", slug],
            capture_output=True, text=True, timeout=10,
            creationflags=_creation_flags(),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    login = result.stdout.strip()
    return login or None


@functools.lru_cache(maxsize=64)
def token_for_account(login: str | None) -> str | None:
    """Mint a ``gh`` OAuth token for ``login`` via ``gh auth token --user``.

    Returns None when ``gh`` is unavailable or ``login`` is not an
    authenticated ``gh`` account (caller then uses ambient auth).
    """
    if not login or shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--user", login],
            capture_output=True, text=True, timeout=10,
            creationflags=_creation_flags(),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def env_for_account(login: str | None, base: dict | None = None) -> dict:
    """Return an env dict authenticating ``gh`` as ``login``.

    A copy of ``base`` (default ``os.environ``) with ``GH_TOKEN`` set to the
    account's token when one can be minted.  ``gh`` prefers ``GH_TOKEN`` over
    the keyring active account, so this pins the subprocess to ``login``
    without a global ``gh auth switch``.  When no token is available the env is
    returned unchanged (ambient auth).
    """
    env = dict(base if base is not None else os.environ)
    token = token_for_account(login)
    if token:
        # GH_TOKEN wins in gh; drop a stale GITHUB_TOKEN so it can't shadow it.
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)
    return env


def env_for_repo(slug: str | None, base: dict | None = None) -> dict:
    """Return an env dict authenticating ``gh`` as the account for ``slug``."""
    return env_for_account(account_for_repo(slug), base)


@functools.lru_cache(maxsize=1)
def mapped_accounts() -> tuple[str, ...]:
    """Distinct gh logins in the agent-worktrees ``account_map``.

    The candidate set for cross-account CodeSpace discovery: to see a CodeSpace
    owned by a non-active account we must ``gh codespace list`` under each
    mapped account's token and merge.  Returns an empty tuple when no map
    exists (caller then lists under the ambient account only -- today's
    behavior).
    """
    aw = _agent_worktrees_bin()
    if not aw:
        return ()
    try:
        result = subprocess.run(
            [aw, "repos", "account", "list", "--json"],
            capture_output=True, text=True, timeout=10,
            creationflags=_creation_flags(),
        )
    except Exception:
        return ()
    if result.returncode != 0:
        return ()
    try:
        data = json.loads(result.stdout)
    except Exception:
        return ()
    raw = data.get("account_map", {}) if isinstance(data, dict) else {}
    seen: list[str] = []
    if isinstance(raw, dict):
        for login in raw.values():
            if login and login not in seen:
                seen.append(str(login))
    return tuple(seen)


def clear_caches() -> None:
    """Drop memoized lookups (test hook / after an auth change)."""
    account_for_repo.cache_clear()
    token_for_account.cache_clear()
    mapped_accounts.cache_clear()
