"""Claim admission and rollback within the lease broker's target exclusion."""
from __future__ import annotations

from . import coordination, lease


def _hold(codespace: str):
    return lease._read_live_records(
        lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
    ).get(codespace)


def _held_error(codespace: str, hold) -> lease.ClaimAdmissionRejected:
    return lease.ClaimAdmissionRejected(
        f"CodeSpace '{codespace}' is unavailable while provider "
        f"{hold.operation} is in progress",
    )


def _check_owner(codespace: str, held, owner: str, force: bool, active) -> bool:
    if held is None or lease._claim_owner(held) == owner:
        return False
    from .driving_worktrees import same_worktree_family

    holder = lease._claim_owner(held)
    family = same_worktree_family(holder, owner)
    if lease._worktree_alive(holder, active) and not force and not family:
        raise lease.ClaimConflict(codespace, holder, held.host, held.pid)
    return family


def admit_claim(
    codespace: str,
    owner: str,
    *,
    force: bool,
    ttl: float,
    active: set[str] | None,
    holder_ref: str | None,
    coordinate: bool,
    preflight_result: coordination.PreflightResult | None,
    journal: bool,
) -> lease.Lease:
    from .worktrees import validate_context

    validate_context()
    if not codespace:
        raise RuntimeError("claim requires a CodeSpace name")
    if not owner:
        raise RuntimeError("claim requires an owner worktree")
    with lease._lease_lock():
        hold = _hold(codespace)
        if hold:
            raise _held_error(codespace, hold)
        peek = lease._read_leases().get(codespace)
        live = peek if peek and not lease._is_stale(peek, ttl) else None
        _check_owner(codespace, live, owner, force, active)

    same_owner = bool(peek and lease._claim_owner(peek) == owner)
    lease_token = ""
    acquired_here = False
    if coordinate and holder_ref:
        prior_token = peek.lease_token if same_owner and peek else ""
        lease_token = prior_token
        renewing = bool(same_owner and prior_token)
        if renewing:
            res = coordination.renew(codespace, prior_token)
        else:
            readiness = preflight_result or coordination.preflight(holder_ref)
            if readiness.rejected:
                raise lease.CoordinationRejected(f"{readiness.code}: {readiness.detail}")
            res = coordination.acquire(codespace, holder_ref)
        if res.ok:
            lease_token = res.token
            acquired_here = not renewing
        elif res.rejected:
            raise lease.CoordinationRejected(res.detail)
        elif res.conflict:
            if lease._same_holder_ref(res.holder, holder_ref):
                lease.log.info(
                    "Re-entrant claim on '%s' by its own owner '%s'; adopting the existing lease.",
                    codespace, owner,
                )
                lease_token = res.token or prior_token
            elif not force:
                held_local = lease._read_leases().get(codespace)
                if held_local is not None:
                    raise lease.ClaimConflict(
                        codespace, lease._claim_owner(held_local), held_local.host, held_local.pid,
                    )
                raise lease.ClaimConflict(
                    codespace, res.holder or "(cross-machine)", "(cross-machine)", 0,
                )
            else:
                lease.log.warning(
                    "Forced claim on '%s' over a live cross-machine lease held by '%s'; "
                    "proceeding without the L2 lease.", codespace, res.holder or "?",
                )
                lease_token = ""

    try:
        with lease._lease_lock():
            hold = _hold(codespace)
            records = lease._read_leases()
            if hold:
                # Preserve a rotated original token even if an older writer
                # introduced a hold without joining this target exclusion.
                current = records.get(codespace)
                if (
                    not acquired_here and same_owner and current == peek
                    and current is not None and lease_token
                    and current.lease_token != lease_token
                ):
                    current.lease_token = lease_token
                    lease._write_leases(records)
                raise _held_error(codespace, hold)
            records = lease._prune(records, ttl)
            held = records.get(codespace)
            family = _check_owner(codespace, held, owner, force, active)
            if held and lease._claim_owner(held) != owner:
                lease.log.info(
                    "Taking CodeSpace '%s' claim from '%s' for '%s' (%s)",
                    codespace, lease._claim_owner(held), owner,
                    "forced" if force else "same worktree family" if family else "prior owner gone",
                )
            now = lease.time.time()
            claimed = lease.Lease(
                codespace=codespace, effort="", pid=lease.os.getpid(), host=lease._this_host(),
                acquired_at=held.acquired_at if held and lease._claim_owner(held) == owner else now,
                heartbeat_at=now, worktree=owner, lease_token=lease_token,
            )
            records[codespace] = claimed
            lease._write_leases(records)
    except (lease.ProviderAdmissionError, lease.ClaimConflict) as error:
        if acquired_here and lease_token:
            try:
                returned = coordination.release(codespace, lease_token)
                confirmed = returned is not None and returned.ok
            except Exception:
                confirmed = False
            if not confirmed:
                raise lease.ClaimAdmissionRejected(
                    f"Admission of '{codespace}' was rejected and return of its newly acquired "
                    "shared lease is unconfirmed; reconcile the original acquisition",
                ) from error
        raise
    if journal and holder_ref:
        coordination.journal_obligation(codespace, holder_ref)
    return claimed
