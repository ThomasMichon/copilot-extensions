"""Provider control of retained portable sessions, without guest-suite repair."""

from __future__ import annotations

import uuid

from venue_copilot.portable_mux import MuxEndpoint


def require_owner() -> None:
    """Require the actual active Owner to preserve the portable endpoint contract."""
    from . import connection_owner as owner

    live = owner._live_snapshot()
    if (
        live is None or live.state != "active" or live.portable_mux_version != 1
        or not owner.verified_owner_beacon(live)
    ):
        raise RuntimeError(
            "The running Connection Owner does not advertise portable mux v1; "
            "retain the original attempt and use its authorized Owner update workflow"
        )


def retained_endpoint(plan: dict, held, row: dict) -> MuxEndpoint | None:
    """Reconcile Owner custody with the provider-authored Bridge venue descriptor."""
    meta = (held.sessions.get(plan["tenant"]) or {}) if held else {}
    venue = row.get("venue") or {}
    candidates = []
    if "mux_endpoint" in meta:
        candidates.append(MuxEndpoint.parse(meta["mux_endpoint"]))
    if venue.get("mux_endpoint") is not None:
        if venue.get("kind") != "codespace" or venue.get("target") != plan["codespace"]:
            raise ValueError("Portable mux venue differs from its retained provider target")
        endpoint = MuxEndpoint.parse(venue["mux_endpoint"])
        if venue.get("mux_session_name") != endpoint.mux_session:
            raise ValueError("Portable mux venue differs from its native session")
        candidates.append(endpoint)
    if not candidates:
        return None
    result = candidates[0]
    for candidate in candidates[1:]:
        if (
            candidate.attempt, candidate.session_id, candidate.mux_socket,
        ) != (result.attempt, result.session_id, result.mux_socket) or (
            result.confirmed and candidate.confirmed and result != candidate
        ):
            raise ValueError("Portable mux records disagree; original custody is retained")
        if candidate.confirmed:
            result = candidate
    return result


def attach(args, endpoint: MuxEndpoint, *, interactive_ssh) -> int:
    """Attach the admitted exact endpoint; never launch a replacement or install."""
    from . import connection_owner as owner
    from .copilot_venue import _BUSY_EXIT, claim_or_exit_code
    from ssh_manager import TargetBusyError, TargetLock

    if getattr(args, "seed", None) or getattr(args, "seed_file", None):
        raise ValueError("Attach cannot seed a retained portable session; use its owned Bridge send lane")
    command = endpoint.command("attach")
    claim_rc = claim_or_exit_code(args, launch_policy=False)
    if claim_rc is not None:
        return claim_rc
    target_lock = TargetLock(args.name, op="copilot")
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        import sys

        print(busy.user_message(), file=sys.stderr)
        return _BUSY_EXIT
    tenant = "portable-attach:" + uuid.uuid4().hex
    try:
        # The detached session tenant owns forwards. This attachment is only a
        # bounded additional consumer and cannot extend that session's lease.
        owner.hold(args.name, tenant)
        return interactive_ssh(args.name, [], remote_command=command)
    finally:
        owner.release(args.name, tenant)
        target_lock.release()
