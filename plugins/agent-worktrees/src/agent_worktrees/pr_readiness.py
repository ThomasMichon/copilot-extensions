"""Explicit-target draft readiness, separate from tracked hold release."""
from __future__ import annotations

from typing import Any

from .config import Config

SCHEMA = "agent-worktrees.pr-ready"


def outcome(repo: str, number: int, *, provider: str | None = None,
            status: str = "not-attempted", error: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA, "version": 1, "repo": repo, "number": number,
        "provider": provider, "ok": status == "confirmed",
        "actions": {"draft": {"status": status}},
    }
    if error:
        result["error"] = error
    return result


def ready(config: Config, repo: str, number: int) -> dict[str, Any]:
    """Resolve the target's policy and invoke only its supported provider owner."""
    from . import pr_config, providers

    if not repo or type(number) is not int or number < 1:
        return outcome(repo, number, error="Readiness needs a target and positive PR number")
    resolution = pr_config.resolve_repo_config_for_slug(config, repo)
    if not resolution.resolved:
        return outcome(repo, number, error=resolution.error or "Readiness needs a registered PR target")
    policy = resolution.repo_config.pr
    if not policy.enabled or policy.provider != "azure-devops":
        return outcome(repo, number, provider=policy.provider,
                       error="Selected target does not support draft-ready")
    provider = providers.get_provider(policy.provider)
    operation = getattr(provider, "ready_pull", None)
    if not callable(operation):
        return outcome(repo, number, provider=policy.provider,
                       error="Selected provider does not support draft-ready")
    token = providers.account_token_for_slug(repo, policy)
    return operation(repo, number, api_base=policy.api_base, token=token)
