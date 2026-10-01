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

log = logging.getLogger("agent-codespaces.relay.gh-auth")

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_DEFAULT_GITHUB_HOSTS = frozenset({"github.com"})


def _normalize_host(host: str | None) -> str:
    return (host or "github.com").strip().lower()


class GhAuthSource:
    """Resolves GitHub auth tokens via ``gh auth token``.

    Supports the ``get-github-token`` action for allowed GitHub hosts. Returns
    the token in key=value format::

        protocol=https
        host=github.com
        token=gho_xxxxx

    """

    def __init__(
        self,
        *,
        account: str | None = None,
        github_hosts: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._account = (account or "").strip() or None
        self._github_hosts = frozenset(
            _normalize_host(host) for host in (github_hosts or _DEFAULT_GITHUB_HOSTS)
        )

    @property
    def name(self) -> str:
        return "gh-auth"

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Supports GitHub token requests only."""
        return (
            action == "get-github-token"
            and _normalize_host(fields.get("host")) in self._github_hosts
        )

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 10.0,
    ) -> str | None:
        """Resolve a GitHub token via ``gh auth token``."""
        if not self.supports(action, fields):
            return None
        host = _normalize_host(fields.get("host"))
        argv = ["gh", "auth", "token", "--hostname", host]
        if self._account:
            argv += ["--user", self._account]
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

        username = f"username={self._account}\n" if self._account else ""
        return f"protocol=https\nhost={host}\n{username}token={token}\n\n"
