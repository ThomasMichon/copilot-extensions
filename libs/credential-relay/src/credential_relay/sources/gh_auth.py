"""GitHub CLI auth token source.

Returns ``gh auth token`` output for GitHub hosts. Handles the
``get-github-token`` relay action, returning the token in
git-credential-protocol key=value format for uniform framing.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from collections.abc import Callable

log = logging.getLogger("agent-codespaces.relay.gh-auth")

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_DEFAULT_GITHUB_HOSTS = frozenset({"github.com"})


def _normalize_host(host: str | None) -> str:
    return (host or "github.com").strip().lower()


class GhAuthSource:
    """Resolves GitHub auth tokens via ``gh auth token``.

    Supports the ``get-github-token`` action and, for allowed GitHub hosts,
    git-credential ``get``/``fill`` fallback. Returns the token in key=value
    format::

        protocol=https
        host=github.com
        token=gho_xxxxx

    Git-credential fallback returns ``username``/``password`` instead, so git
    can consume it directly after Git Credential Manager fails.

    """

    def __init__(
        self,
        *,
        account: str | None = None,
        account_resolver: Callable[[dict[str, str]], str | None] | None = None,
        github_hosts: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._account = (account or "").strip() or None
        self._account_resolver = account_resolver
        self._github_hosts = frozenset(
            _normalize_host(host) for host in (github_hosts or _DEFAULT_GITHUB_HOSTS)
        )

    @property
    def name(self) -> str:
        return "gh-auth"

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Supports GitHub token requests and GitHub git-credential fallback."""
        if action == "get-github-token":
            return _normalize_host(fields.get("host")) in self._github_hosts
        if action in ("get", "fill"):
            return (
                fields.get("protocol", "https").lower() == "https"
                and _normalize_host(fields.get("host")) in self._github_hosts
            )
        return False

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 10.0,
    ) -> str | None:
        """Resolve a GitHub token via ``gh auth token``."""
        if not self.supports(action, fields):
            return None
        host = _normalize_host(fields.get("host"))
        account = self._account_for_request(action, fields)
        if not account and action in ("get", "fill"):
            return None
        argv = ["gh", "auth", "token", "--hostname", host]
        if account:
            argv += ["--user", account]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("gh auth token timed out (%.0fs)", timeout)
            return None
        except FileNotFoundError:
            log.error("gh CLI not found on PATH")
            return None

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            log.error("gh auth token failed (exit %d): %s", proc.returncode, err)
            return None

        token = stdout.decode(errors="replace").strip()
        if not token:
            log.error("gh auth token returned empty output")
            return None

        # Return in key=value format for uniform framing
        if action in ("get", "fill"):
            return (
                f"protocol=https\nhost={host}\nusername={account}\n"
                f"password={token}\n\n"
            )
        username = f"username={account}\n" if account else ""
        return f"protocol=https\nhost={host}\n{username}token={token}\n\n"

    def _account_for_request(
        self,
        action: str,
        fields: dict[str, str],
    ) -> str | None:
        """Resolve the account whose token should serve this request."""
        candidates = [
            fields.get("username"),
            self._account,
            (self._account_resolver(fields) if self._account_resolver else None),
        ]
        for candidate in candidates:
            login = (candidate or "").strip()
            if login:
                return login
        if action in ("get", "fill"):
            log.info("No username supplied for GitHub git credential fallback")
        return None
