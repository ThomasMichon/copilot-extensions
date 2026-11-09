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


class CredentialObservationError(RuntimeError):
    """Sanitized failure of a bounded read, without credential or child output."""

    def __init__(
        self, stage: str, code: str, *, exit_code: int | None = None,
        http_status: int | None = None,
    ):
        super().__init__(f"Repository authentication observation failed ({stage}: {code}).")
        self.stage, self.code, self.exit_code = stage, code, exit_code
        self.http_status = http_status


def inspect_github_account(host: str, login: str, repository: str) -> dict:
    """Observe the selected gh store and API, never acquisition or ambient auth."""
    from agent_procutil.owned_budget import OwnedExecutionBudget

    from .github_account_observation import SelectedGitHubAccount

    budget = OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2)
    selected = SelectedGitHubAccount(
        host, login, repository, budget=budget, checkpoint=lambda: None,
    )
    observations = {
        "credential": {"status": "not-observed"},
        "identity": {"status": "not-observed"},
        "repository_access": {"status": "not-observed"},
    }

    try:
        selected.verify()
        observations["credential"] = {"status": "available", "owner": "github-cli"}
        observations["identity"] = {"status": "verified"}
        value = selected.json("repository", "/repos/" + repository)
        observed_repository = value.get("full_name")
        if (
            not isinstance(observed_repository, str)
            or observed_repository.casefold() != repository.casefold()
        ):
            raise CredentialObservationError("repository", "target-mismatch")
        owner = value.get("owner")
        kind = owner.get("type") if isinstance(owner, dict) else None
        observations["repository_access"] = {"status": "verified", "capability": "repository-metadata-read"}
        if kind in ("User", "Organization"):
            observations["repository_access"]["owner_kind"] = (
                "user" if kind == "User" else "organization"
            )
    except CredentialObservationError as exc:
        if exc.stage != "credential":
            observations["credential"] = {"status": "available", "owner": "github-cli"}
        if exc.code == "account-mismatch":
            observations["identity"] = {"status": "mismatch"}
            return {**observations, "status": "unavailable"}
        failure = {"stage": exc.stage, "code": exc.code}
        if exc.exit_code is not None:
            failure["exit_code"] = exc.exit_code
        if exc.http_status is not None:
            failure["http_status"] = exc.http_status
        return {**observations, "status": "unavailable", "failure": failure}
    return {**observations, "status": "observed"}
