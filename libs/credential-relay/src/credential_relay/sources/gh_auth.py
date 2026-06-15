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


class GhAuthSource:
    """Resolves GitHub auth tokens via ``gh auth token``.

    Supports the ``get-github-token`` action. Returns the token in
    key=value format::

        protocol=https
        host=github.com
        token=gho_xxxxx

    """

    @property
    def name(self) -> str:
        return "gh-auth"

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Supports ``get-github-token`` action only."""
        return action == "get-github-token"

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 10.0,
    ) -> str | None:
        """Resolve a GitHub token via ``gh auth token``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "auth", "token",
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
        host = fields.get("host", "github.com")
        return f"protocol=https\nhost={host}\ntoken={token}\n\n"
