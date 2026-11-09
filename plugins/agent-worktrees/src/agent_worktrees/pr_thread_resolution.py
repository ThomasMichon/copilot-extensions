"""Explicit selected-thread admission and its additive native wire contract."""

from __future__ import annotations

import re
from typing import Any

from .config import Config

SCHEMA = "agent-worktrees.pr-thread-resolve"


def outcome(repo: str, number: int, thread: int, operation_id: str,
            expected_candidate: str, expected_thread: str, *, provider: str | None = None,
            actor: str | None = None, status: str = "not-attempted", reason: str = "admission-refused",
            error: str = "", observed_candidate: dict | None = None,
            observed_thread: dict | None = None, reopening: bool = False) -> dict[str, Any]:
    """Keep the original selection and independent evidence on every outcome."""
    return {
        "schema": "agent-worktrees.pr-thread-reopen" if reopening else SCHEMA,
        "version": 1, "repo": repo, "number": number,
        "thread": thread, "operation_id": operation_id, "provider": provider,
        "actor": actor, "ok": status == "confirmed", "reason": reason, "error": error,
        "expected_candidate": expected_candidate, "expected_thread": expected_thread,
        "observed_candidate": observed_candidate, "observed_thread": observed_thread,
        "actions": {"reopening" if reopening else "resolution": {"status": status}},
    }


def validate(repo: str, number: int, thread: int, operation_id: str,
             expected_candidate: str, expected_thread: str) -> None:
    """Reject bulk selection, display indexes and unbounded operation identities."""
    if not isinstance(repo, str) or not repo.strip() or any(
        type(value) is not int or not 0 < value <= 2**63 - 1 for value in (number, thread)
    ):
        raise ValueError("Selected resolution requires a target and positive native PR/thread IDs")
    if (
        not isinstance(operation_id, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id) is None
        or any(not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
               for value in (expected_candidate, expected_thread))
    ):
        raise ValueError("Selected resolution requires exact fingerprints and a bounded operation ID")


def resolve(config: Config, repo: str, number: int, thread: int, *, operation_id: str,
            expected_candidate: str, expected_thread: str,
            reopening: bool = False) -> dict[str, Any]:
    """Use only the selected enabled target's optional provider operation."""
    from . import pr_config, providers

    fields = (repo, number, thread, operation_id, expected_candidate, expected_thread)
    def result(**kwargs: Any) -> dict[str, Any]:
        return outcome(*fields, reopening=reopening, **kwargs)

    name = "thread-reopen" if reopening else "thread-resolve"
    try:
        validate(*fields)
    except ValueError as exc:
        return result(reason="invalid-input", error=str(exc))
    resolution = pr_config.resolve_repo_config_for_slug(config, repo)
    if not resolution.resolved:
        return result(reason="target-unavailable",
                       error=resolution.error or "Selected resolution needs a registered PR target")
    policy = resolution.repo_config.pr
    if not policy.enabled or policy.provider != "azure-devops":
        return result(provider=policy.provider, reason="unsupported",
                      error=f"Selected target does not support {name}")
    provider = providers.get_provider(policy.provider)
    operation = getattr(provider, "reopen_selected_thread" if reopening else "resolve_selected_thread", None)
    if not callable(operation):
        return result(provider=policy.provider, reason="unsupported",
                      error=f"Selected provider does not support {name}")
    token = providers.account_token_for_slug(repo, policy)
    return operation(
        repo, number, thread, operation_id=operation_id,
        expected_candidate=expected_candidate, expected_thread=expected_thread,
        api_base=policy.api_base, token=token,
    )
