"""Post-connect remote-domain auth verification.

After a CodeSpace SSH session is established, the relay forwards git-credential
requests back to the host's Git Credential Manager. If the host has no local
auth for a remote's domain, a ``git fetch`` inside the CodeSpace would fail --
ideally fast (the relay now returns ``quit=1``), but the failure is far more
useful if surfaced *up front* rather than discovered mid-fetch.

This module lists the remote workspace's git remotes, extracts their domains,
and verifies the host has local auth for each domain by probing the same
``GitCredentialSource`` the relay uses (non-interactive, fail-fast). Domains
with no resolvable local credential are reported so the caller can fix auth
(e.g. ``az login`` / GCM sign-in) before the agent starts working.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from credential_relay.sources.git_credential import GitCredentialSource

from .provision import DOTFILES_DIR

log = logging.getLogger("agent-codespaces.auth-preflight")

# Remote command that prints the git remotes of both repos a session touches:
# the workspace/product checkout (preferring the reliable $VM_REPO_PATH set by
# many codespaces devcontainers, falling back to $PWD) AND the account dotfiles
# checkout (so its host -- usually github.com -- is auth-verified too). Bounded;
# never prompts; missing checkouts contribute nothing.
REMOTE_LIST_COMMAND = (
    "{ "
    'git -C "${VM_REPO_PATH:-$PWD}" remote -v 2>/dev/null; '
    f'git -C "{DOTFILES_DIR}" remote -v 2>/dev/null; '
    "git remote -v 2>/dev/null; "
    "} || true"
)


def host_from_url(url: str) -> str | None:
    """Extract the host from a git remote URL.

    Handles ``https://host/path``, ``ssh://git@host/path`` and the scp-like
    ``git@host:path`` form. Returns ``None`` for unparseable / local paths.
    """
    url = url.strip()
    if not url:
        return None

    # scp-like syntax: [user@]host:path (no scheme, has ':' before any '/')
    if "://" not in url:
        if "@" in url:
            url = url.split("@", 1)[1]
        if ":" in url:
            host = url.split(":", 1)[0]
            return host or None
        return None

    parts = urlsplit(url)
    host = parts.hostname
    return host or None


def parse_remote_hosts(remote_output: str) -> list[str]:
    """Parse unique remote hosts from ``git remote -v`` output.

    Output lines look like ``origin\\thttps://host/org/repo (fetch)``.
    Returns hosts in first-seen order, de-duplicated.
    """
    hosts: list[str] = []
    for line in remote_output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        host = host_from_url(fields[1])
        if host and host not in hosts:
            hosts.append(host)
    return hosts


async def host_has_auth(
    host: str,
    *,
    source: GitCredentialSource | None = None,
    timeout: float = 15.0,
) -> bool:
    """Whether the host has resolvable local auth for ``host``.

    Probes the local credential store the same way the relay does. With the
    non-interactive env baked into :class:`GitCredentialSource`, a missing
    credential fails fast (no prompt) rather than hanging.
    """
    src = source or GitCredentialSource()
    try:
        response = await src.resolve(
            "get", {"protocol": "https", "host": host}, timeout=timeout,
        )
    except Exception:
        log.debug("Auth probe for %s raised", host, exc_info=True)
        return False
    return bool(response and "password=" in response)


async def verify_remote_auth(
    run_remote: Callable[[str], Awaitable[str]],
    *,
    source: GitCredentialSource | None = None,
    timeout: float = 15.0,
    extra_hosts: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Verify host auth for every domain the session's git remotes use.

    Probes the union of: the **workspace/product** checkout's remotes, the
    **dotfiles** checkout's remotes (both via ``REMOTE_LIST_COMMAND``), and any
    ``extra_hosts`` the caller guarantees (e.g. the configured dotfiles repo's
    host, so github.com is verified even before the dotfiles clone exists).

    ``run_remote`` runs a shell command on the CodeSpace and returns its stdout
    (used to fetch ``git remote -v``). Returns ``(hosts, missing)`` where
    ``hosts`` is every distinct domain checked and ``missing`` is the subset
    with no resolvable local auth. An empty ``hosts`` list means nothing to
    verify (a no-op). A failure listing remotes does not suppress ``extra_hosts``.
    """
    try:
        remote_output = await run_remote(REMOTE_LIST_COMMAND)
    except Exception:
        log.debug("Could not list remote git remotes", exc_info=True)
        remote_output = ""

    hosts = parse_remote_hosts(remote_output or "")
    for host in extra_hosts or []:
        if host and host not in hosts:
            hosts.append(host)
    if not hosts:
        return [], []

    src = source or GitCredentialSource()
    missing: list[str] = []
    for host in hosts:
        if not await host_has_auth(host, source=src, timeout=timeout):
            missing.append(host)
    return hosts, missing
