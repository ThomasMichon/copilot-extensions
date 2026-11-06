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
    import json
    import os
    import re
    import shutil
    import subprocess

    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import (
        OwnedCleanupError, OutputLimitExceeded, run_caller_owned,
    )

    from .repos_configuration_sources import identity

    if host != "github.com" or identity(login) != login or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository,
    ):
        raise CredentialObservationError("selection", "unsupported-target")
    executable = shutil.which("gh")
    if executable is None:
        raise CredentialObservationError("credential", "missing-command")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
            "GH_HOST", "GH_REPO", "GH_DEBUG", "GH_FORCE_TTY", "GH_HTTP_UNIX_SOCKET",
        }
    }
    environment["GH_PROMPT_DISABLED"] = "1"
    environment["GH_PAGER"] = "cat"
    budget = OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2)
    observations = {
        "credential": {"status": "not-observed"},
        "identity": {"status": "not-observed"},
        "repository_access": {"status": "not-observed"},
    }

    def capture(stage, arguments):
        try:
            result = run_caller_owned(
                [executable, *arguments], timeout=18, budget=budget, env=dict(environment),
                max_stdout_bytes=256 * 1024, max_stderr_bytes=64 * 1024,
            )
        except OwnedCleanupError:
            raise
        except subprocess.TimeoutExpired:
            raise CredentialObservationError(stage, "timeout") from None
        except OutputLimitExceeded:
            raise CredentialObservationError(stage, "output-limit") from None
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            raise CredentialObservationError(stage, "capture-failed") from None
        if result.returncode:
            status = re.match(r"HTTP/\S+ ([1-5][0-9]{2})(?:\s|$)", result.stdout)
            raise CredentialObservationError(
                stage, "credential-unavailable" if stage == "credential" else "access-unconfirmed",
                exit_code=result.returncode,
                http_status=int(status[1]) if status is not None and stage != "credential" else None,
            )
        return result.stdout

    def api(stage, path):
        raw = capture(stage, ["api", "--hostname", host, "--include", "https://api.github.com" + path])
        try:
            header, body = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
            status = re.match(r"HTTP/\S+ ([0-9]{3})(?:\s|$)", header)
            value = json.loads(body)
            if status is None or status[1] != "200" or not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError):
            raise CredentialObservationError(stage, "invalid-response") from None

    try:
        token = capture("credential", ["auth", "token", "--hostname", host, "--user", login]).strip()
        if (
            not token or len(token) > 16384 or not token.isascii()
            or any(character.isspace() or not character.isprintable() for character in token)
        ):
            raise CredentialObservationError("credential", "invalid-response")
        observations["credential"] = {"status": "available", "owner": "github-cli"}
        environment["GH_TOKEN"] = token
        value = api("identity", "/user")
        observed_login = value.get("login")
        try:
            valid_login = identity(observed_login) == observed_login
        except ValueError:
            valid_login = False
        if not valid_login:
            raise CredentialObservationError("identity", "invalid-response")
        if observed_login.casefold() != login.casefold():
            observations["identity"] = {"status": "mismatch"}
            return {**observations, "status": "unavailable"}
        observations["identity"] = {"status": "verified"}
        value = api("repository", "/repos/" + repository)
        observed_repository = value.get("full_name")
        if (
            not isinstance(observed_repository, str)
            or observed_repository.casefold() != repository.casefold()
        ):
            raise CredentialObservationError("repository", "target-mismatch")
        observations["repository_access"] = {"status": "verified", "capability": "repository-metadata-read"}
    except CredentialObservationError as exc:
        failure = {"stage": exc.stage, "code": exc.code}
        if exc.exit_code is not None:
            failure["exit_code"] = exc.exit_code
        if exc.http_status is not None:
            failure["http_status"] = exc.http_status
        return {**observations, "status": "unavailable", "failure": failure}
    return {**observations, "status": "observed"}
