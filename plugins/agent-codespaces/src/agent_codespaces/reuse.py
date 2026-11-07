"""Sequential reuse through the existing local claim and shared lease owners."""
from __future__ import annotations

import os
import math
import sys
import time

from . import coordination, lease


def _local_records() -> dict[str, lease.Lease]:
    records = lease._read_records(lease.LEASE_FILE, lease.Lease, fail_closed=True)
    try:
        malformed = any(
            not isinstance(name, str) or not name or record.codespace != name
            or any(not isinstance(value, str) for value in (
                record.effort, record.worktree, record.host, record.lease_token,
            ))
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in (record.acquired_at, record.heartbeat_at))
            for name, record in records.items()
        )
    except (TypeError, ValueError, OverflowError):
        malformed = True
    if malformed:
        raise RuntimeError("Local CodeSpace ownership is malformed")
    return records


def local_snapshot() -> dict[str, lease.Lease]:
    """Keep expired and unknown local holders; expiry is not a return."""
    try:
        with lease._lease_lock():
            return _local_records()
    except OSError as error:
        raise RuntimeError("Local CodeSpace ownership is unavailable") from error


def workstream_mode(name: str, local, shared) -> str:
    """An obligation records responsibility, not a confirmed acquisition."""
    if local is None:
        return "unknown"
    held = local.get(name)
    remote = shared.get(name) if shared is not None else None
    if remote is not None and remote.state == "released":
        return "released" if held is None else "unknown"
    owner = lease.resolve_owner_worktree()
    if (
        held is None or not owner or lease._claim_owner(held) != owner
        or held.host != lease._this_host() or lease._is_stale(held, lease.DEFAULT_TTL)
    ):
        return "unknown"
    if remote is None:
        return "unknown" if held.lease_token else "own"
    holder = coordination.owner_ref()
    return (
        "own" if remote.state == "leased" and remote.live and holder
        and lease._same_holder_ref(remote.holder, holder) else "unknown"
    )


def candidates(members, budget, repo, local, shared, *, new_cores: int, branch=None) -> tuple[str, ...]:
    from .driving_worktrees import _matches_workstream_repo
    from .pool import IN_USE

    account = getattr(budget, "account", None)
    if (
        not account or new_cores <= 0 or not budget.owned_inventory_complete
        or budget.unknown_cores_count or local is None or shared is None
    ):
        return ()
    suitable = [
        member for member in members
        if member.state == "Shutdown" and member.disposition != IN_USE
        and member.name not in local
        and member.account.casefold() == account.casefold()
        and _matches_workstream_repo(repo, member.repository)
        and (branch is None or branch == member.branch)
        and new_cores <= member.cores <= budget.headroom_cores
        and (record := shared.get(member.name)) is not None
        and record.state == "released" and not record.live
    ]
    if not suitable:
        return ()
    owner = lease.resolve_owner_worktree()
    holder = coordination.owner_ref()
    if not owner or not holder or os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        return ()
    readiness = coordination.preflight(holder)
    if (
        not readiness.ready
        or coordination.RELEASED_ONLY_CAPABILITY not in readiness.capabilities
    ):
        return ()
    return tuple(member.name for member in sorted(suitable, key=lambda member: (member.cores, member.name)))


def reserve(
    name: str, repo: str, config, account: str, environment: dict, *, branch=None,
) -> lease.Lease:
    """Reserve without waking, resetting, provisioning or taking over a holder."""
    from .account_binding import bind
    from .allocation import intended_cores, list_account_codespaces
    from .live_users import codespaces_in_use
    from .lifecycle_lock import lifecycle_lock
    from .pool import DEFAULT_BUDGET_CORES, build_pool
    from .status import clear_status
    from .worktrees import ContextRefused, validate_context

    validate_context()
    owner = lease.resolve_owner_worktree()
    holder = coordination.owner_ref()
    if not owner or not holder or os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        raise RuntimeError("Sequential reuse requires an owning worktree and exclusive claims")
    if name in local_snapshot():
        raise RuntimeError(f"CodeSpace '{name}' still has a local holder")
    released = coordination.inspect(name)
    if not released or released.get("state") != "released" or released.get("live") is not False:
        raise RuntimeError(f"CodeSpace '{name}' has no confirmed shared release")
    readiness = coordination.preflight(holder)
    if not readiness.ready or coordination.RELEASED_ONLY_CAPABILITY not in readiness.capabilities:
        raise RuntimeError("Selected lease receiver does not support released-only acquisition")
    bind(name, account, repo=repo, strict=True)
    if not coordination.journal_obligation(name, holder, confirmed=True):
        raise RuntimeError(f"Could not record responsibility for reusing '{name}'")
    acquired = coordination.acquire(
        name, holder, released_only=True, preflight_result=readiness,
    )
    if not acquired.ok or not acquired.token:
        raise RuntimeError(
            f"Reservation of '{name}' was not confirmed; its obligation is retained. "
            f"{acquired.detail or acquired.status}"
        )
    try:
        with lifecycle_lock(name):
            rows = list_account_codespaces(account, environment=environment)
            members, budget = build_pool(
                codespaces=rows, account=account, leases=[], markers={}, l2_leases={},
                clean_records={}, live={}, budget_cores=DEFAULT_BUDGET_CORES,
                max_owned_codespaces=getattr(config, "max_owned_codespaces", 30),
            )
            matches = [item for item in members if item.name == name]
            member = matches[0] if len(matches) == 1 else None
            from .driving_worktrees import _matches_workstream_repo

            requested_cores = intended_cores(config, repo)
            if (
                member is None or member.state != "Shutdown"
                or not _matches_workstream_repo(repo, member.repository)
                or (branch is not None and member.branch != branch)
                or not budget.owned_inventory_complete or budget.unknown_cores_count
                or requested_cores <= 0 or member.cores < requested_cores
                or member.cores > budget.headroom_cores
            ):
                raise RuntimeError("Selected venue or its wake headroom changed before reservation")
            users = codespaces_in_use([name])
            if users is None or users.get(name):
                raise RuntimeError("Selected venue has live or unknown local users")
            with lease._lease_lock():
                records = _local_records()
                holds = lease._read_live_records(
                    lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
                )
                if name in records or name in holds:
                    raise RuntimeError("Selected venue acquired a local holder or lifecycle hold")
                now = time.time()
                reservation = lease.Lease(
                    codespace=name, effort="", worktree=owner, host=lease._this_host(),
                    pid=os.getpid(), acquired_at=now, heartbeat_at=now,
                    lease_token=acquired.token,
                )
                records[name] = reservation
                lease._write_leases(records)
            bind(name, account, repo=member.repository, strict=True)
            clear_status(name)
            return reservation
    except Exception as error:
        # The native shared lease and obligation retain custody, even if the
        # local write/binding failed. Never blindly replay acquisition or create.
        message = (
            f"Reservation of '{name}' needs reconciliation; its shared lease and "
            f"obligation are retained: {error}"
        )
        if isinstance(error, ContextRefused):
            raise ContextRefused(message) from error
        raise RuntimeError(message) from error


def reuse_for_create(decision, args, config, account, environment) -> int:
    from .worktrees import ContextRefused

    if decision.requires_reservation:
        try:
            reserve(
                decision.codespace, args.repo, config, account, environment,
                branch=getattr(args, "branch", None),
            )
        except ContextRefused:
            raise
        except RuntimeError as error:
            print(f"[pressure] {error}", file=sys.stderr)
            return 4
        print(f"[reuse] Reserved '{decision.codespace}'; retained branch and files are unchanged.")
    else:
        print(f"[reuse] {decision.reason}")
        print(
            f"This workstream already has a CodeSpace -- reuse '{decision.codespace}' "
            "(ssh/borrow) instead of creating a new box, or pass --force-create to create anyway.",
            file=sys.stderr,
        )
    print(decision.codespace)
    return 0
