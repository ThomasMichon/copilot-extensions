"""Selected-account capacity admission and its side-effect-free decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lifecycle import CodespaceInfo
    from .pool import PoolMember

DEFAULT_MAX_OWNED_CODESPACES = 30
ALLOC_REUSE = "reuse"
ALLOC_CREATE = "create"
ALLOC_RECYCLE = "recycle"
ALLOC_PRESSURE = "pressure"


def _environment_context(ambient: str, *, timeout: float = 10) -> tuple[str, dict]:
    from agent_procutil import no_window_kwargs

    environment = dict(os.environ)
    environment["GH_TOKEN"] = ambient
    environment.pop("GITHUB_TOKEN", None)
    try:
        result = subprocess.run(
            ["gh", "api", "user"], capture_output=True, text=True,
            timeout=timeout, env=environment, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Environment GitHub identity verification failed: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Environment GitHub identity verification failed: {result.stderr.strip()}")
    try:
        profile = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Environment GitHub identity response is malformed") from exc
    login = profile.get("login") if isinstance(profile, dict) else None
    if not isinstance(login, str) or not login.strip():
        raise RuntimeError("Environment GitHub identity response has no account")
    return login.strip(), environment


def verified_account_token(account: str, *, timeout: float = 10) -> str | None:
    """Use an environment credential only after verifying the expected owner."""
    from . import gh_account

    started = time.monotonic()
    token = gh_account.token_for_account(account)
    if token:
        return token
    ambient = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    remaining = timeout - (time.monotonic() - started)
    if ambient and remaining > 0:
        try:
            login, environment = _environment_context(ambient, timeout=remaining)
        except RuntimeError:
            pass
        else:
            if login.casefold() == account.casefold():
                return environment["GH_TOKEN"]
    return None


def allocation_context(repo: str) -> tuple[str, dict]:
    from . import gh_account
    from .driving_worktrees import current_workstream_account
    from .owner_inventory import qualified_environment

    account = gh_account.account_for_repo(repo)
    if account:
        return account, qualified_environment(account)
    bound = current_workstream_account(repo)
    ambient = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if ambient:
        try:
            login, environment = _environment_context(ambient)
        except RuntimeError:
            if not bound:
                raise
            return bound, qualified_environment(bound)
        if bound and bound.casefold() != login.casefold():
            return bound, qualified_environment(bound)
        return login, environment
    account = bound or gh_account.active_account()
    if not account:
        raise RuntimeError("CodeSpace allocation account is unavailable")
    return account, qualified_environment(account)


def list_account_codespaces(
    account: str, *, environment: dict | None = None,
) -> list[CodespaceInfo]:
    """Require a complete, credential-pinned single-account allocation view."""
    from .lifecycle import _list_codespaces_under

    try:
        rows = _list_codespaces_under(
            account, qualified=True, **({"environment": environment} if environment is not None else {}),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"CodeSpace account inventory failed: {exc}") from exc
    if len(rows) >= 50:
        raise RuntimeError("CodeSpace account inventory is truncated at the 50-venue listing limit")
    return rows


def creation_environment(
    repo: str, account: str | None, environment: dict | None = None,
) -> tuple[str | None, dict | None]:
    if environment is not None:
        if not account:
            raise RuntimeError("An admitted environment requires a verified account")
        return account, environment
    from . import gh_account

    account = account or gh_account.account_for_repo(repo)
    if account:
        from .owner_inventory import qualified_environment

        return account, qualified_environment(account)
    return None, None


def pool_inventory(rows, account, environment, *, listing):
    supplied = rows is not None
    if rows is None:
        rows = list_account_codespaces(account, environment=environment) if account else listing()
    if account:
        rows = [row for row in rows if row.account.casefold() == account.casefold()]
    counts: dict[str, int] = {}
    for row in rows:
        key = row.account.casefold() or "ambient"
        counts[key] = counts.get(key, 0) + 1
    return rows, counts, bool(account) or (supplied and len(counts) <= 1)


def stop_context(name, account, token, *, listing, status_lookup):
    """Keep an explicitly selected stop's lookup and mutation on one credential."""
    from . import gh_account
    from .worktrees import ContextRefused

    if account is not None:
        if token is None:
            from .owner_inventory import qualified_environment

            environment = qualified_environment(account)
            token = environment["GH_TOKEN"]
        else:
            environment = dict(os.environ)
            environment["GH_TOKEN"] = token
            environment.pop("GITHUB_TOKEN", None)
        exists, state = status_lookup(name, account, token=token)
        if not exists:
            raise RuntimeError("CodeSpace is absent from the selected account")
        return account, environment, state
    if token is not None:
        raise RuntimeError("A credential-pinned stop requires a selected account")
    state = None
    try:
        for cs in listing():
            if cs.name == name:
                account = cs.account or None
                state = cs.state
                break
    except ContextRefused:
        raise
    except RuntimeError:
        pass
    environment = (
        gh_account.env_for_account(account)
        if account and state != "Shutdown" else None
    )
    return account, environment, state


@dataclass
class Budget:
    """Independent owned-venue and concurrent-core accounting."""

    total_cores: int
    spent_cores: int
    headroom_cores: int
    running_count: int
    total_count: int
    unknown_cores_count: int
    max_owned_codespaces: int = DEFAULT_MAX_OWNED_CODESPACES
    account: str | None = None
    owned_inventory_complete: bool = True
    owned_by_account: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_cores": self.total_cores,
            "spent_cores": self.spent_cores,
            "headroom_cores": self.headroom_cores,
            "running_count": self.running_count,
            "total_count": self.total_count,
            "unknown_cores_count": self.unknown_cores_count,
            "max_owned_codespaces": self.max_owned_codespaces,
            "account": self.account,
            "owned_inventory_complete": self.owned_inventory_complete,
            "owned_by_account": self.owned_by_account,
            "owned_headroom": (
                self.max_owned_codespaces - self.total_count
                if self.owned_inventory_complete else None
            ),
        }


@dataclass
class AllocationDecision:
    """The resolved venue-request decision (see :func:`plan_allocation`)."""

    action: str
    codespace: str | None
    reason: str
    then: str | None = None
    then_codespace: str | None = None
    headroom_cores: int = 0
    needed_cores: int = 0

    def to_dict(self) -> dict:
        d: dict = {
            "action": self.action,
            "codespace": self.codespace,
            "reason": self.reason,
            "headroom_cores": self.headroom_cores,
            "needed_cores": self.needed_cores,
        }
        if self.then is not None:
            d["then"] = self.then
            d["then_codespace"] = self.then_codespace
        return d


def plan_allocation(
    members: list[PoolMember],
    budget: Budget,
    *,
    repo: str,
    new_cores: int = 0,
    workstream_box: str | None = None,
) -> AllocationDecision:
    """Plan within selected-account owned-slot and running-core capacity.

    A caller-resolved workstream venue is reused first; waking it needs known
    available cores but no new owned slot. New creation requires complete
    inventory, a free owned slot and known compute costs. Only core pressure
    can suggest recycling a stale running member; it never treats stopping
    as freeing an owned slot. All acquisition, rescue and reclaim safety gates
    remain with the executor. This function does not acquire a venue.
    """
    from .pool import STALE

    needed = new_cores if new_cores > 0 else 1
    headroom = budget.headroom_cores
    if workstream_box:
        mine = next((m for m in members if m.name == workstream_box), None)
        if mine is not None:
            cost = 0 if mine.running else mine.cores
            if not mine.running and (
                cost <= 0 or budget.unknown_cores_count or headroom < cost
            ):
                return AllocationDecision(
                    action=ALLOC_PRESSURE, codespace=None,
                    reason=f"cannot wake '{mine.name}': insufficient or unknown running-core headroom",
                    headroom_cores=headroom, needed_cores=cost,
                )
            return AllocationDecision(
                action=ALLOC_REUSE, codespace=mine.name,
                reason=(
                    f"resuming this workstream's own CodeSpace '{mine.name}' "
                    f"for {repo}" + (
                        " (already running; no extra budget)" if mine.running
                        else f" (stopped; boots on connect, {cost} core(s))"
                    )
                ),
                headroom_cores=headroom, needed_cores=cost,
            )
    if not budget.owned_inventory_complete:
        return AllocationDecision(
            action=ALLOC_PRESSURE, codespace=None,
            reason="owned CodeSpace inventory is incomplete; refresh the selected account before creating",
            headroom_cores=headroom, needed_cores=needed,
        )
    if budget.total_count >= budget.max_owned_codespaces:
        return AllocationDecision(
            action=ALLOC_PRESSURE, codespace=None,
            reason=(
                f"owned CodeSpace limit reached: {budget.total_count}/"
                f"{budget.max_owned_codespaces}; stopped venues still consume owned slots"
            ),
            headroom_cores=headroom, needed_cores=needed,
        )
    if new_cores <= 0 or budget.unknown_cores_count:
        return AllocationDecision(
            action=ALLOC_PRESSURE, codespace=None,
            reason="running-core capacity is unknown; resolve machine costs before creating",
            headroom_cores=headroom, needed_cores=new_cores,
        )
    if headroom >= needed:
        return AllocationDecision(
            action=ALLOC_CREATE, codespace=None,
            reason=(f"no box claimed by this workstream for {repo}; creating "
                    f"a fresh one ({needed} core(s) fit the {headroom}-core "
                    f"headroom)"),
            headroom_cores=headroom, needed_cores=needed,
        )
    stale = [m for m in members if m.disposition == STALE and m.running]
    stale.sort(key=lambda m: (-(m.idle_age or 0.0), -m.cores, m.name))
    for m in stale:
        if headroom + m.cores >= needed:
            return AllocationDecision(
                action=ALLOC_RECYCLE, codespace=m.name,
                reason=(f"pool full ({budget.spent_cores}/{budget.total_cores} "
                        f"cores); recycle stale '{m.name}' (+{m.cores} cores) "
                        f"then create a fresh box for {repo}"),
                then="create", then_codespace=None,
                headroom_cores=headroom, needed_cores=needed,
            )
    return AllocationDecision(
        action=ALLOC_PRESSURE, codespace=None,
        reason=(f"pool full: {budget.spent_cores}/{budget.total_cores} cores in "
                f"use, {headroom} free; no box claimed by this workstream and "
                f"no stale box to recycle for {repo}"),
        headroom_cores=headroom, needed_cores=needed,
    )
