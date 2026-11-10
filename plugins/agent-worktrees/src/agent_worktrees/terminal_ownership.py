"""Creator-ownership proofs for terminal worktree conclusion policies."""

from __future__ import annotations

from . import tracking

DISPOSABLE_CLI_POLICY = "disposable-cli"  # Historical wire name; interface-neutral.
DISPATCH_ATTEMPT_POLICY = "dispatch-attempt"
# A worker workspace created by agent-bridge (which holds no dispatch
# authority, so the record carries no ``dispatch_attempt``) is proven by the
# exact creation nonce the creator retained from that creation.
BRIDGE_CREATION_POLICY = "bridge-creation"
CONCLUSION_POLICIES = (DISPOSABLE_CLI_POLICY, DISPATCH_ATTEMPT_POLICY, BRIDGE_CREATION_POLICY)


def _bridge_creation_reason(
    record: tracking.WorktreeRecord, creation_nonce: str | None,
) -> str | None:
    """Skip reason unless ``record`` is the exact Bridge-created incarnation."""
    if record.dispatch_attempt is not None:
        return "dispatch-provenance-present"
    if not creation_nonce:
        return "creation-identity-missing"
    if not record.creation_nonce or record.creation_nonce != creation_nonce:
        return "creation-nonce-mismatch"
    return None


def _dispatch_ownership_confirmed(
    record: tracking.WorktreeRecord,
    *,
    reservation_key: str | None,
    owner: str,
) -> bool:
    """Whether ``record``'s own ``dispatch_attempt`` provenance exactly
    matches the caller's claimed reservation/owner identity -- the same
    match the ``dispatch-attempt`` policy gate already requires before
    priming. Reused right before releasing this worktree's own ``session``
    claim(s) (see :func:`_release_dispatch_session_claims`) so that release
    is never granted on provenance the two-phase lock's unlocked
    git-inspection window could have let drift underneath -- defense in
    depth; the policy gate's own earlier check already enforces this for
    the ordinary path, this just avoids trusting a now-stale in-memory
    ``record`` a second time.
    """
    allocation = record.dispatch_attempt
    return (
        allocation is not None
        and reservation_key is not None
        and allocation.reservation_key == reservation_key
        and allocation.creator_machine.casefold() == record.execution_machine.casefold()
        and allocation.driver == owner
    )


def _creator_ownership_confirmed(
    record: tracking.WorktreeRecord,
    *,
    policy: str,
    reservation_key: str | None,
    owner: str,
    creation_nonce: str | None = None,
) -> bool:
    """Whether the creator concluding ``record`` is exactly proven: dispatch
    provenance for ``dispatch-attempt``, or the retained Bridge creation
    nonce for ``bridge-creation``. Other policies never prove creator
    ownership."""
    if policy == DISPATCH_ATTEMPT_POLICY:
        return _dispatch_ownership_confirmed(record, reservation_key=reservation_key, owner=owner)
    if policy == BRIDGE_CREATION_POLICY:
        return _bridge_creation_reason(record, creation_nonce) is None
    return False
