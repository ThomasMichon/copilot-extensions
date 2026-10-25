"""Fail-closed credential acquisition for explicit repository account choices."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountSelection:
    login: str | None
    configured: bool


def account_selection(target: str | None) -> AccountSelection:
    """Resolve identity and fallback policy from one canonical registry snapshot."""
    from . import repos

    registry = repos.read_registry(strict=True)
    login = repos.account_for_github_slug(target, registry=registry)
    configured = repos.account_for_github_slug(
        target, registry=registry, configured_only=True,
    )
    return AccountSelection(login, bool(configured))


class GitAuthArgs(list[str]):
    """Git arguments carrying the policy that selected their credential."""

    def __init__(self, args: list[str], *, allow_ambient_retry: bool) -> None:
        super().__init__(args)
        self.allow_ambient_retry = allow_ambient_retry


class AccountCredentialError(RuntimeError):
    """A configured repository identity has no usable account credential."""

    def __init__(self, account: str) -> None:
        super().__init__(
            f"GitHub credential unavailable for selected account '{account}'; "
            "refusing ambient authentication. Check that account's gh login "
            "and credential store, then retry."
        )


def gh_token_for_target(selection: AccountSelection) -> str | None:
    """Resolve a credential without substituting an explicitly selected identity."""
    from . import git_ops

    if not selection.login:
        return None
    token = git_ops.gh_token_for_account(selection.login)
    if not token and selection.configured:
        raise AccountCredentialError(selection.login)
    return token
