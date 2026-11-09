"""Explicit-target reviewer requests without tracked-PR selection or replay."""
from __future__ import annotations

from typing import Any

from .config import Config

SCHEMA = "agent-worktrees.pr-review-request"


def outcome(repo: str, number: int, *, provider: str | None = None,
            status: str = "not-attempted", reviewer: str = "", error: str = "",
            detail: str = "") -> dict[str, Any]:
    """Keep admission, known no-op, acceptance and uncertain effects distinct."""
    result: dict[str, Any] = {
        "schema": SCHEMA, "version": 1, "repo": repo, "number": number,
        "provider": provider, "ok": status in ("accepted", "no-op"),
        "supported": status in ("accepted", "failed", "unknown"), "requested": status == "accepted",
        "reviewer": reviewer, "actions": {"review_request": {"status": status}},
    }
    if error:
        result["error"] = error
    if detail:
        result["detail"] = detail
    return result


def nudge(config: Config, repo: str, number: int) -> dict[str, Any]:
    """Resolve actual target policy before using its existing provider owner."""
    from . import pr_config, providers

    if not repo or type(number) is not int or number < 1:
        return outcome(repo, number, error="Reviewer request needs a target and positive PR number")
    resolution = pr_config.resolve_repo_config_for_slug(config, repo)
    if not resolution.resolved:
        return outcome(repo, number, error=resolution.error or "Reviewer request needs a registered PR target")
    policy = resolution.repo_config.pr
    if not policy.enabled or policy.provider != "github":
        return outcome(repo, number, provider=policy.provider,
                       error="Selected target does not support reviewer-request")
    provider = providers.get_provider(policy.provider)
    operation = getattr(provider, "request_review_target", None)
    binding = getattr(provider, "reviewer_login", None)
    if not callable(operation) or not callable(binding):
        return outcome(repo, number, provider=policy.provider,
                       error="Selected provider does not support reviewer-request")
    if not binding(policy.reviewer):
        return outcome(repo, number, provider=policy.provider, status="no-op",
                       detail="No configured mapped automated reviewer; no request was made")
    token = providers.account_token_for_slug(repo, policy)
    return operation(repo, number, reviewer=policy.reviewer, api_base=policy.api_base, token=token)
