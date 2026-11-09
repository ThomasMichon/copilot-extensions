"""Host-qualified repository account lookup through the canonical registry policy."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .repo_urls import remote_host as remote_host

if TYPE_CHECKING:
    from .repos import RepoEntry, ReposRegistry

DEFAULT_ACCOUNT_OWNER = "github.com:*"


@dataclass(frozen=True)
class AccountLookupIndex:
    """Per-snapshot first-match indexes for canonical account lookups."""

    slugs: dict[str, RepoEntry]
    account_map: dict[str, str]
    default_account: str

    @classmethod
    def from_registry(cls, registry: ReposRegistry) -> AccountLookupIndex:
        from . import repos

        slugs: dict[str, RepoEntry] = {}
        account_map: dict[str, str] = {}
        for owner, login in registry.account_map.items():
            if login:
                account_map.setdefault(owner.casefold(), login)
        for entry in registry.repos.values():
            if slug := repos.github_slug(entry.remote):
                slugs.setdefault(slug.casefold(), entry)
        return cls(slugs, account_map, registry.default_account)


def resolution_snapshot() -> dict:
    """Resolve registered targets and known owners in one peer invocation."""
    from . import repos

    registry = repos.read_registry(strict=True)
    index = AccountLookupIndex.from_registry(registry)
    targets = set(registry.repos)
    owners = set(registry.account_map)
    for entry in registry.repos.values():
        if slug := repos.github_slug(entry.remote):
            targets.add(slug)
            owners.add(slug.split("/", 1)[0])
    return {
        "account_map": dict(registry.account_map),
        "default_account": registry.default_account or None,
        "resolution_version": 1,
        "unmapped_owner_fallback": False,
        "repository_accounts": {
            (target.casefold() if "/" in target else target): repos.account_for_github_slug(
                target, registry=registry, index=index,
            )
            for target in sorted(targets)
        },
        "owner_accounts": {
            owner.casefold(): repos.account_for_github_owner(owner, registry=registry, index=index)
            for owner in sorted(owners)
        },
    }


def account_for_remote(remote: str) -> str | None:
    """Resolve exact host/repository identity before public GitHub owner policy."""
    from . import repos

    def identity(value: str) -> tuple[str, str] | None:
        if "://" not in value:
            scp = re.fullmatch(r"(?:[^@/:\s]+@)?([^/:\s]+):([^\\]+)", value)
            if scp is None:
                return None
            value = f"ssh://{scp[1]}/{scp[2]}"
        parsed = urlsplit(value)
        if not parsed.hostname:
            return None
        return (
            remote_host(parsed.netloc.rsplit("@", 1)[-1], parsed.scheme),
            parsed.path.strip("/").removesuffix(".git").casefold(),
        )

    selected = identity(remote)
    if selected is None:
        return None
    registry = repos.read_registry(strict=True)
    for entry in registry.repos.values():
        if identity(entry.remote) == selected:
            if selected[0] == "github.com":
                entry = replace(entry, remote="https://github.com/" + selected[1])
            return repos.resolve_account(entry, registry=registry)
    if selected[0] == "github.com":
        return repos.account_for_github_slug(selected[1], registry=registry)
    return None


def command_account(args: list[str]) -> int:
    """Keep account policy commands beside their canonical projection."""
    from . import output, repos

    action, rest = (args[0], args[1:]) if args else ("list", [])
    if action == "default":
        if rest in (["--help"], ["-h"]):
            print("Usage: repos account default [<login>|--unset]")
            return 0
        registry = repos.read_registry(strict=True)
        if not rest:
            print(registry.default_account)
            return 0
        if len(rest) != 1 or (
            rest[0] != "--unset" and (
                not rest[0] or rest[0].startswith("-")
                or any(char.isspace() for char in rest[0])
            )
        ):
            output.err("Usage: repos account default [<login>|--unset]")
            return 1
        registry.default_account = "" if rest[0] == "--unset" else rest[0]
        repos.write_registry(registry)
        output.ok("Preferred GitHub account updated.")
        return 0
    if action == "list":
        if "--json" in rest:
            if "--with-resolutions" in rest:
                try:
                    payload = resolution_snapshot()
                except repos.RepositoryResolutionError as exc:
                    return output._json_error(
                        f"Account resolution metadata could not be read: {exc}", exit_code=2,
                    )
            else:
                registry = repos.read_registry(strict=True)
                payload = {
                    "account_map": dict(registry.account_map),
                    "default_account": registry.default_account or None,
                }
            output._json_output(payload)
            return 0
        registry = repos.read_registry(strict=True)
        if registry.default_account:
            print(f"Preferred GitHub account: {registry.default_account}")
        if not registry.account_map:
            print("No account_map entries.")
            print("Add one with: repos account set <owner> <login>")
            return 0
        output.header("Account map (owner -> gh login)")
        for owner in sorted(registry.account_map):
            print(f"  {owner:<24} -> {registry.account_map[owner]}")
        return 0
    if action == "set":
        if len(rest) < 2:
            output.err("Usage: repos account set <owner> <login>")
            return 1
        repos.set_account_map(rest[0], rest[1])
        return 0
    if action in ("unset", "remove", "rm"):
        if not rest:
            output.err("Usage: repos account unset <owner>")
            return 1
        if repos.unset_account_map(rest[0]):
            return 0
        output.err(f"No account_map entry for '{rest[0]}'")
        return 1
    output.err(f"Unknown 'repos account' subcommand: {action}")
    output.info("Usage: repos account [list|default [<login>|--unset]|set <owner> <login>|unset <owner>]")
    return 1
