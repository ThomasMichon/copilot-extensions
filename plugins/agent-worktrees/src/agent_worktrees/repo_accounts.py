"""Host-qualified repository account lookup through the canonical registry policy."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def account_for_remote(remote: str) -> str | None:
    """Resolve exact host/repository identity before public GitHub owner policy."""
    from . import repos

    def identity(value: str) -> tuple[str, int | None, str] | None:
        if "://" not in value:
            scp = re.fullmatch(r"(?:[^@/:\s]+@)?([^/:\s]+):([^\\]+)", value)
            if scp is None:
                return None
            value = f"ssh://{scp[1]}/{scp[2]}"
        parsed = urlsplit(value)
        if not parsed.hostname:
            return None
        return (
            parsed.hostname.casefold(), parsed.port,
            parsed.path.strip("/").removesuffix(".git").casefold(),
        )

    selected = identity(remote)
    if selected is None:
        return None
    for entry in repos.read_registry().repos.values():
        if identity(entry.remote) == selected:
            return repos.resolve_account(entry)
    if selected[0] == "github.com" and selected[1] is None:
        return repos.account_for_github_slug(selected[2])
    return None
