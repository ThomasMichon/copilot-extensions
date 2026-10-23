"""Wave-local, completeness-qualified owner inventory; never a durable cache."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InventoryScope:
    source: str
    configuration: str
    accounts: tuple[str | None, ...]
    credentials: tuple[str, ...]


@dataclass(frozen=True)
class InventoryRow:
    name: str
    state: str
    account: str | None


@dataclass(frozen=True)
class AccountListing:
    account: str | None
    rows: tuple[InventoryRow, ...]
    observed_at: float
    complete: bool
    error: str | None = None


@dataclass(frozen=True)
class OwnerInventory:
    wave: int
    scope: InventoryScope
    listings: tuple[AccountListing, ...]

    def lookup(
        self, name: str, *, wave: int, scope: InventoryScope,
        now: float, max_age: float,
    ) -> InventoryRow | None:
        if wave != self.wave or scope != self.scope:
            return None
        if scope.accounts and tuple(item.account for item in self.listings) != scope.accounts:
            return None
        # An incomplete account could hide a duplicate owner. Neither its
        # absence nor another account's same-name row grants connection authority.
        if not self.listings or any(
            not item.complete or item.error or not 0 <= now - item.observed_at <= max_age
            for item in self.listings
        ):
            return None
        rows = [row for item in self.listings for row in item.rows if row.name == name]
        return rows[0] if len(rows) == 1 else None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def credential(account: str | None) -> str:
    """Resolve a credential without silently falling back to another account."""
    from agent_procutil import no_window_kwargs

    from . import gh_account

    if account:
        from .picker_auth import credential_token

        token = credential_token(account)
        if token is None:
            # An observation wave must see keyring rotation, unlike the legacy
            # process-lifetime convenience cache used by ordinary callers.
            token = gh_account._token_for_account.__wrapped__(account)
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True,
                timeout=10, **no_window_kwargs(),
            )
            token = result.stdout.strip() if result.returncode == 0 else None
    if not token:
        raise RuntimeError("Owner inventory credential unavailable")
    return token


def capture_scope() -> InventoryScope:
    """Revalidate selected installation, effective config, accounts and credentials.

    This performs no CodeSpace listing. Rechecking the authority at admission
    deliberately costs metadata reads rather than granting a stale connection.
    Tokens themselves never enter the returned snapshot.
    """
    from .account_candidates import candidates
    from .config import RUNTIME_DIR, load_merged_config
    from .worktrees import validate_context

    context = validate_context()
    accounts = candidates()
    source = json.dumps(
        [str(RUNTIME_DIR), context, os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")],
        sort_keys=True,
    )
    configuration = repr(load_merged_config(include_cwd=False))
    return InventoryScope(
        _digest(source), _digest(configuration), accounts,
        tuple(_digest(credential(account)) for account in accounts),
    )


def credential_matches(scope: InventoryScope, account: str | None, token: str) -> bool:
    try:
        return scope.credentials[scope.accounts.index(account)] == _digest(token)
    except (ValueError, IndexError):
        return False


def validate_listing_payload(entries) -> None:
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
        or not entry["name"] or not isinstance(entry.get("state"), str) or not entry["state"]
        for entry in entries
    ):
        raise RuntimeError("Malformed CodeSpace listing")


def decode_listing(stdout, *, qualified=False):
    try:
        if qualified and not stdout.strip():
            raise ValueError("Empty response")
        entries = json.loads(stdout) if stdout.strip() else []
    except (ValueError, TypeError):
        if qualified:
            raise RuntimeError("Malformed CodeSpace listing") from None
        return []
    if qualified:
        validate_listing_payload(entries)
    return entries


def qualified_environment(account) -> dict[str, str]:
    env = dict(os.environ)
    env["GH_TOKEN"] = credential(account)
    env.pop("GITHUB_TOKEN", None)
    return env


async def joined_thread(function: Callable[..., Any], *args: Any) -> Any:
    """Cancellation drains our bounded synchronous work instead of orphaning it."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await drain_tasks((task,))
        raise


async def drain_tasks(tasks) -> None:
    """Join owned cleanup even when the caller receives another cancellation."""
    joined = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


async def bounded_operation(operation, timeout):
    """Own timeout cancellation exactly once, then drain the actual operation."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    finally:
        if not task.done():
            task.cancel()
        await drain_tasks((task,))


async def finish_cleanup(operation):
    task = asyncio.create_task(operation)
    await drain_tasks((task,))
    return task.result()


def collect_inventory(
    wave: int, *, scope_reader: Callable[[], InventoryScope] = capture_scope,
    clock: Callable[[], float] = time.monotonic,
) -> OwnerInventory:
    from concurrent.futures import ThreadPoolExecutor

    scope = scope_reader()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(qualified_account_listing, account, clock=clock)
            for account in scope.accounts
        ]
        listings = tuple(future.result() for future in futures)
    if scope_reader() != scope:
        raise RuntimeError("Owner inventory authority changed during listing")
    return OwnerInventory(wave, scope, listings)


def qualified_account_listing(account, *, clock=time.monotonic):
    from .lifecycle import _list_codespaces_under
    from .worktrees import ContextRefused

    observed = clock()
    try:
        rows = _list_codespaces_under(account, qualified=True)
        return AccountListing(
            account, tuple(InventoryRow(row.name, row.state, account) for row in rows),
            observed, len(rows) < 50,
        )
    except ContextRefused:
        raise
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return AccountListing(account, (), observed, False, type(exc).__name__)
