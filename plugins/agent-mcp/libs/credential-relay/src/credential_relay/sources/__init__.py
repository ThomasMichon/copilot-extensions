"""Pluggable credential resolution sources.

Each source handles a subset of credential requests (by action and/or
host pattern). The relay server routes requests to the first source
whose ``supports()`` returns True.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialSource(Protocol):
    """Protocol for credential resolution backends.

    Implementations proxy credential requests to a local credential
    store (e.g., Git Credential Manager, ``gh auth``, ``az login``).

    All responses use git-credential-protocol key=value format,
    terminated by a blank line. Even non-git sources (like gh-auth)
    return key=value pairs for uniform framing.
    """

    @property
    def name(self) -> str:
        """Human-readable source name for logging/stats."""
        ...

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Whether this source can handle this request.

        Called before ``resolve()`` to determine routing. Sources are
        tried in registration order; first match wins.
        """
        ...

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 30.0,
    ) -> str | None:
        """Resolve a credential request.

        Returns git-credential-protocol response text (key=value lines
        terminated by blank line), or None if resolution fails.

        Implementations must respect the timeout parameter and return
        None (not hang) if the underlying credential store is slow.
        """
        ...
