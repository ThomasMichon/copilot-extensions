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
    return _require_result(obj, failure)


def require_owner_hosting(
    project: str, worktree_id: str | None, *,
    request: Callable[[str | None, list[str]], dict],
    failure: type[RuntimeError], support_only: bool = False,
) -> None:
    """Probe the selected engine before effectful launch resolution."""
    obj = request(project, [
        "agent-policy", "check",
        *(["--worktree-id", worktree_id] if worktree_id else ["--project-root"]),
        "--json",
    ])
    if support_only and (
        type(obj.get("version")) is int and obj["version"] == 1
        and obj.get("code") in (
            "repository-agent-exposure-denied", "repository-agent-exposure-invalid",
        )
        and isinstance(obj.get("error"), str) and bool(obj["error"].strip())
        and "allowed" not in obj
    ):
        # Remote admission belongs to the receiving host, not a local namesake.
        return
    if isinstance(obj.get("error"), str) and obj["error"].strip():
        raise failure(obj["error"])
    _require_result(obj, failure)


def _require_result(obj: dict, failure: type[RuntimeError]) -> dict:
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
