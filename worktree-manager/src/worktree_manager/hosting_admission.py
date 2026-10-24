"""Process-boundary repository hosting admission without plugin imports."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def require_agent_hosting(
    path: str, *, operation: str,
    request: Callable[[str | None, list[str]], dict],
    failure: type[RuntimeError],
) -> dict:
    """Require the versioned negative-policy answer from the selected engine."""
    obj = request(None, [
        "agent-policy", "check", "--path", path, "--operation", operation, "--json",
    ])
    if (
        not {"version", "repository", "expose_agent", "sources", "denied_by", "allowed"}.issubset(obj)
        or type(obj.get("version")) is not int or obj["version"] != 1
        or obj.get("allowed") is not True
        or "error" in obj or "code" in obj
        or not isinstance(obj.get("repository"), str) or not obj["repository"].strip()
        or not Path(obj["repository"]).is_absolute()
        or (obj.get("expose_agent") is not None and obj.get("expose_agent") is not True)
        or not isinstance(obj.get("sources"), list)
        or any(
            not isinstance(source, str) or not source.strip() or not Path(source).is_absolute()
            for source in obj.get("sources", [])
        )
        or obj.get("denied_by") != []
    ):
        raise failure("Engine could not establish fresh repository hosting admission")
    return obj
