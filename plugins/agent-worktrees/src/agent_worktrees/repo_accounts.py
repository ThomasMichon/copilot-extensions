"""Host-qualified repository account lookup through the canonical registry policy."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .repo_urls import remote_host as remote_host

if TYPE_CHECKING:
    from .repos import RepoEntry, ReposRegistry


@dataclass(frozen=True)
class AccountLookupIndex:
    """Per-snapshot first-match indexes for canonical account lookups."""

    slugs: dict[str, RepoEntry]
    owners: dict[str, str]
    account_map: dict[str, str]

    @classmethod
    def from_registry(cls, registry: ReposRegistry) -> AccountLookupIndex:
        from . import repos

        slugs: dict[str, RepoEntry] = {}
        owners: dict[str, str] = {}
        account_map: dict[str, str] = {}
        for owner, login in registry.account_map.items():
            if login:
                account_map.setdefault(owner.casefold(), login)
        for entry in registry.repos.values():
            if slug := repos.github_slug(entry.remote):
                slugs.setdefault(slug.casefold(), entry)
            if entry.account and (owner := repos.github_owner(entry.remote)):
                owners.setdefault(owner.casefold(), entry.account)
        return cls(slugs, owners, account_map)


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
        "resolution_version": 1,
        "unmapped_owner_fallback": True,
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
    for entry in repos.read_registry().repos.values():
        if identity(entry.remote) == selected:
            if selected[0] == "github.com":
                entry = replace(entry, remote="https://github.com/" + selected[1])
            return repos.resolve_account(entry)
    if selected[0] == "github.com":
        return repos.account_for_github_slug(selected[1])
    return None
