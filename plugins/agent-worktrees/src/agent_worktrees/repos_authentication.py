"""Standalone provider-owned repository authentication observation, version 1."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import yaml

from . import repos
from .repos_configuration import target
from .repos_configuration_sources import ConfigurationError, identity, resource_name
from .repos_registry_reader import RepositoryResolutionError

CONTRACT = "agent-worktrees.repository-authentication"
UNOBSERVED = ["git_transport", "gcm", "write_permission", "codespaces", "mcp", "copilot"]


def contract() -> dict:
    return json.loads(Path(__file__).with_name("repository-authentication-v1.json").read_text(
        encoding="utf-8",
    ))


def inspect_repository(project: str, name: str) -> dict:
    project, name = resource_name(project), resource_name(name)
    registry = repos.read_registry(strict=True)
    if project not in registry.repos or name not in registry.repos:
        raise ConfigurationError("not-registered", "The selected project and repository must be registered.")
    entry = registry.repos[name]
    slug = repos.github_slug(entry.remote)
    remote = entry.remote.strip()
    host = (
        urlsplit(remote).hostname if "://" in remote
        else remote.split("@", 1)[-1].split(":", 1)[0]
    )
    login = repos.resolve_account(entry, registry=registry, configured_only=True)
    source = "repository" if entry.account else "account-map" if login else "unconfigured"
    if login:
        login = identity(login)
    result = {
        "contract": CONTRACT, "version": 1, "target": target(project, name),
        "registry_revision": registry._baseline[1] if registry._baseline is not None else None,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "selection": {
            "provider": "github" if slug else "unsupported",
            "host": host,
            "repository": slug, "account": login, "account_source": source,
            "configured": bool(login),
        },
        "credential": {"status": "not-observed"},
        "identity": {"status": "not-observed"},
        "repository_access": {"status": "not-observed"},
        "unobserved": UNOBSERVED.copy(),
        "status": "unsupported",
    }
    if not slug or host != "github.com":
        return result
    if not login:
        return {**result, "status": "selection-required"}
    from .github_credentials import CredentialObservationError, inspect_github_account

    try:
        observation = inspect_github_account(host, login, slug)
    except CredentialObservationError as exc:
        observation = {
            "status": "unavailable", "failure": {"stage": exc.stage, "code": exc.code},
        }
    return {**result, **observation}


def command(argv: list[str]) -> int:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise ConfigurationError(
                "invalid-request", "Invalid request; use repos authentication --help.",
            )

    parser = SafeParser(prog="repos authentication", allow_abbrev=False)
    parser.add_argument("operation", choices=("inspect", "contract"))
    parser.add_argument("--context-project")
    parser.add_argument("--repository")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.operation == "contract":
            if args.context_project is not None or args.repository is not None:
                raise ConfigurationError("invalid-request", "Contract inspection takes no repository selection.")
            result = contract()
        else:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = inspect_repository(args.context_project, args.repository)
    except (ValueError, TypeError, RepositoryResolutionError, OSError, yaml.YAMLError) as exc:
        code = exc.code if isinstance(exc, ConfigurationError) else "observation-unavailable"
        result = {
            "contract": CONTRACT, "version": 1, "status": "failed", "code": code,
            "message": "Repository authentication inspection is unavailable; inspect the native owner.",
        }
        print(json.dumps(result))
        print(f"Repository authentication inspection failed ({code}).", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if args.operation == "contract" or result["status"] == "observed" else 1
