"""Shared read-only registration, provider identity and server policy observation."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from agent_procutil.owned_budget import OwnedExecutionBudget
import yaml

from . import repos
from .github_credentials import CredentialObservationError
from .repos_configuration_sources import ConfigurationError, identity, resource_name


CONTRACT = "agent-worktrees.repository-registration-preflight"
VERSION = 1


def _provider(remote: str) -> dict:
    if not remote:
        return {"kind": "local-only", "host": "", "repository": ""}
    parsed = urlsplit(remote if "://" in remote else "ssh://" + remote.replace(":", "/", 1))
    if (
        parsed.password or parsed.query or parsed.fragment
        or parsed.scheme in {"http", "https"} and parsed.username
    ):
        raise ConfigurationError("unsafe-origin", "The registered origin needs a nonsecret canonical identity.")
    host = parsed.hostname or ""
    slug = repos.github_slug(remote)
    if slug and host == "github.com":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug) or any(
            part in {".", ".."} for part in slug.split("/")
        ):
            raise ConfigurationError("invalid-origin", "The registered origin needs an exact repository identity.")
        return {"kind": "github", "host": host, "repository": slug}
    kind = "azure-devops" if (
        host in {"dev.azure.com", "ssh.dev.azure.com"}
        or host.endswith((".visualstudio.com", ".vs-ssh.visualstudio.com"))
    ) else "unsupported"
    return {"kind": kind, "host": host, "repository": ""}


def inspect_repository(name: str) -> dict:
    if name != resource_name(name):
        raise ConfigurationError("invalid-repository", "Select an exact registered repository name.")
    budget = OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2)
    registry = repos.read_registry(strict=True)
    result = {
        "contract": CONTRACT, "version": VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registry_revision": registry._baseline[1] if registry._baseline else None,
        "registration": {"name": name, "source": "unresolved", "class": "", "path": ""},
        "provider": {"kind": "unverified", "host": "", "repository": ""},
        "account": {"login": "", "source": "unconfigured", "status": "not-observed"},
        "landing": {
            "status": "not-observed", "mode": "unknown", "branch": "",
            "classic": "unknown", "rulesets": "unknown", "observed_pr_requirement": None,
        },
        "status": "partial",
        "next_step": "Inspect canonical registration through the native repository owner before choosing authentication.",
        "unobserved": ["git_transport", "candidate_admission", "contribution_policy", "binding", "pairing"],
    }
    entry = registry.repos.get(name)
    if entry is None:
        discovered = repos.resolve_path(name, registry=registry)
        if discovered:
            result["registration"].update(source="fallback-discovered", path=discovered)
        return result
    result["registration"].update(source="canonical", **{"class": entry.repo_class})
    raw_path = entry.local_path()
    if raw_path:
        root = Path(raw_path)
        if root.is_absolute() and root.is_dir():
            result["registration"]["path"] = str(root.resolve(strict=True))
    result["provider"] = _provider(entry.remote)
    if result["provider"]["kind"] != "github":
        result["landing"]["status"] = (
            "not-applicable" if result["provider"]["kind"] == "local-only" else "unsupported"
        )
        result["next_step"] = "Use the selected provider's publication policy; independently valid local binding is unchanged."
        return result
    login = repos.resolve_account(entry, registry=registry, configured_only=True)
    if not login:
        result["account"]["status"] = "selection-required"
        result["next_step"] = "Select the repository account through the native owner, then refresh; no login was started."
        return result
    login = identity(login)
    source = (
        "repository" if entry.account else
        "account-map" if repos.account_from_map(repos.github_owner(entry.remote), registry=registry) else
        "default-account"
    )
    result["account"].update(login=login, source=source)

    def checkpoint():
        if time.monotonic() >= budget.execution_deadline:
            raise CredentialObservationError("observation", "timeout")
        from .repos_registry_transaction import read_bytes, revision

        try:
            path = repos._repos_yaml_path()
            current = (str(path.resolve()), revision(read_bytes(path)))
        except OSError:
            raise CredentialObservationError("registration", "source-unavailable") from None
        if current != registry._baseline:
            raise CredentialObservationError("registration", "source-changed")

    from .github_account_observation import SelectedGitHubAccount
    from .github_landing_observation import observe

    try:
        selected = SelectedGitHubAccount(
            "github.com", login, result["provider"]["repository"], budget=budget, checkpoint=checkpoint,
        )
        selected.verify()
        result["account"]["status"] = "verified"
        observe(selected, result["landing"])
        checkpoint()
    except CredentialObservationError as error:
        failure = {"stage": error.stage, "code": error.code}
        if error.http_status is not None:
            failure["http_status"] = error.http_status
        result["failure"] = failure
        if result["account"]["status"] != "verified":
            result["account"]["status"] = "mismatch" if error.code == "account-mismatch" else "unavailable"
        result["landing"]["status"] = (
            "insufficient-permission"
            if error.http_status == 403 or error.code == "insufficient-permission" else "unknown"
        )
        result["landing"]["mode"] = "unknown"
        if (
            result["landing"]["observed_pr_requirement"] is True
            and error.stage == "ruleset-policy"
        ):
            result["landing"]["mode"] = "pull-request"
    result["status"] = "observed" if result["landing"]["status"] == "observed" else "partial"
    result["next_step"] = (
        "Follow the repository's contribution policy and revalidate before any later publication; this read grants no effects."
        if result["status"] == "observed" else
        "Inspect the named native observation or policy limitation, then refresh; preserve existing checkout and commits."
    )
    return result


def command(argv: list[str]) -> int:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ConfigurationError("invalid-request", "Use repos registration preflight --repository NAME.")

    parser = Parser(prog="repos registration preflight", allow_abbrev=False)
    parser.add_argument("--repository", required=True)
    try:
        args = parser.parse_args(argv)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = inspect_repository(args.repository)
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError) as error:
        from agent_procutil.owned_process import OwnedCleanupError

        if isinstance(error, OwnedCleanupError):
            raise
        result = {
            "contract": CONTRACT, "version": VERSION, "status": "failed",
            "code": error.code if isinstance(error, ConfigurationError) else "observation-unavailable",
        }
        print(json.dumps(result))
        return 2
    print(json.dumps(result))
    return 0
