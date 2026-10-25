"""Fail-closed credential acquisition for explicit repository account choices."""

from __future__ import annotations


class AccountCredentialError(RuntimeError):
    """A configured repository identity has no usable account credential."""

    def __init__(self, account: str) -> None:
        super().__init__(
            f"GitHub credential unavailable for selected account '{account}'; "
            "refusing ambient authentication. Check that account's gh login "
            "and credential store, then retry."
        )


def gh_token_for_target(account: str, target: str | None) -> str | None:
    """Resolve a credential without substituting an explicitly selected identity."""
    from . import git_ops, repos

    token = git_ops.gh_token_for_account(account)
    if not token and repos.account_for_github_slug(target, configured_only=True):
        raise AccountCredentialError(account)
    return token
