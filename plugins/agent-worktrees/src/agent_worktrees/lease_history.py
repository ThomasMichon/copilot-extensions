"""Transitions for the shared task-lease/service-custody resource history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .lease_protocol import (
    LeaseRecord, PROOF_SCHEMA_VERSION, ProtocolError, RETURN_PROOF_LIMIT,
    ReturnedGeneration, parse_timestamp,
)
from .service_custody_protocol import CoordinationRecord, ServiceCustodyRecord
from .provider_effect_protocol import ProviderEffectRecord, validate_begin, validate_settle


@dataclass(frozen=True)
class ResourceHistory:
    current: CoordinationRecord
    effects: tuple[tuple[str, ProviderEffectRecord], ...]


def validate_history(chain: list[tuple[str, CoordinationRecord]], *,
                     clock_skew_seconds: int) -> ResourceHistory:
    """Project settled effects without renewing, releasing or replacing custody."""
    previous = None
    parent_oid = ""
    pending = None
    effects = []
    seen = set()
    margins = [clock_skew_seconds] * len(chain)
    margin = clock_skew_seconds
    for index in range(len(chain) - 1, -1, -1):
        record = chain[index][1]
        if isinstance(record, ServiceCustodyRecord):
            margin = record.acceptance_margin_seconds
        elif isinstance(record, ProviderEffectRecord) and record.event == "begin":
            margin = record.admission_margin_seconds
        margins[index] = margin
    for index, (oid, current) in enumerate(chain):
        if isinstance(current, ProviderEffectRecord):
            if current.event == "begin":
                if pending is not None:
                    raise ProtocolError("provider resource has an unsettled effect")
                if current.effect_id in seen:
                    raise ProtocolError("provider effect_id cannot be reused")
                validate_begin(previous, current, parent_oid=parent_oid)
                seen.add(current.effect_id)
                pending = current
            else:
                if pending is None:
                    raise ProtocolError("provider effect settlement requires an unsettled effect")
                validate_settle(pending, current, parent_oid=parent_oid)
                pending = None
            effects.append((oid, current))
        else:
            if pending is not None:
                raise ProtocolError("provider resource has an unsettled effect")
            validate_transition(previous, current, parent_oid=parent_oid,
                                clock_skew_seconds=margins[index])
            previous = current
        parent_oid = oid
    if previous is None:
        raise ProtocolError("resource history has no task custody")
    return ResourceHistory(pending or previous, tuple(effects))


def validate_transition(
    previous: CoordinationRecord | None,
    current: CoordinationRecord,
    *,
    parent_oid: str,
    clock_skew_seconds: int,
) -> None:
    """Validate against the actual Git parent, not an asserted receipt alone."""
    if isinstance(previous, ProviderEffectRecord) or isinstance(current, ProviderEffectRecord):
        raise ProtocolError("provider effects require complete resource-history validation")
    if isinstance(previous, ServiceCustodyRecord):
        raise ProtocolError("service custody cannot be changed by this protocol generation")
    if isinstance(current, ServiceCustodyRecord):
        if previous is None or previous.state != "leased":
            raise ProtocolError("service adoption must follow a live task lease")
        if current.predecessor != parent_oid:
            raise ProtocolError("service adoption predecessor does not match its Git parent")
        accepted = parse_timestamp(current.accepted_at)
        if accepted < parse_timestamp(previous.renewed_at):
            raise ProtocolError("service adoption precedes its task parent")
        if accepted > previous.expires() - timedelta(seconds=current.acceptance_margin_seconds):
            raise ProtocolError("service adoption occurred after the task safe deadline")
        return
    validate_task_transition(previous, current, clock_skew_seconds=clock_skew_seconds)


def validate_task_transition(
    previous: LeaseRecord | None,
    current: LeaseRecord,
    *,
    clock_skew_seconds: int,
) -> None:
    """Retain version-1 task event semantics, including released-history GC."""
    if previous is None:
        if current.event != "acquire":
            raise ProtocolError("the root lease commit must be an acquire event")
        return
    if current.schema_version < previous.schema_version:
        raise ProtocolError("a lease cannot downgrade retained return evidence")
    if current.schema_version != previous.schema_version and current.event != "release":
        raise ProtocolError("only an explicit release can opt into retained return evidence")
    expected_returns = previous.returned_generations
    if current.event == "release" and current.schema_version == PROOF_SCHEMA_VERSION:
        expected_returns = (*expected_returns, ReturnedGeneration(
            previous.lease_id, previous.holder, current.renewed_at,
        ))[-RETURN_PROOF_LIMIT:]
    if current.returned_generations != expected_returns:
        raise ProtocolError("lease transition changed retained return evidence")

    issued = parse_timestamp(current.issued_at)
    renewed = parse_timestamp(current.renewed_at)
    prior_renewed = parse_timestamp(previous.renewed_at)

    if current.event == "acquire":
        if previous.state != "released":
            raise ProtocolError("a non-root acquire must follow a release tombstone")
        if current.lease_id == previous.lease_id:
            raise ProtocolError("a new acquisition must use a new lease_id")
        if issued < prior_renewed:
            raise ProtocolError("acquisition time precedes its release parent")
        return

    if previous.state != "leased":
        raise ProtocolError(f"{current.event} must follow leased state")
    if current.event == "takeover":
        deadline = previous.expires() + timedelta(seconds=clock_skew_seconds)
        if current.lease_id == previous.lease_id:
            raise ProtocolError("a takeover must use a new lease_id")
        if issued <= deadline:
            raise ProtocolError("takeover occurred before expiry plus clock skew")
        return
    if current.event == "transfer":
        deadline = previous.expires() - timedelta(seconds=clock_skew_seconds)
        if current.lease_id == previous.lease_id:
            raise ProtocolError("a transfer must use a new lease_id")
        if current.holder == previous.holder:
            raise ProtocolError("transfer must change the holder")
        if current.issued_at != current.renewed_at:
            raise ProtocolError("transfer must issue and renew together")
        if issued < prior_renewed:
            raise ProtocolError("transfer moved renewed_at backward")
        if issued > previous.expires():
            raise ProtocolError("transfer occurred after lease expiry")
        if issued > deadline:
            raise ProtocolError("transfer occurred after the prior safe deadline")
        return

    if current.lease_id != previous.lease_id:
        raise ProtocolError(f"{current.event} changed the lease_id")
    if current.holder != previous.holder:
        raise ProtocolError(f"{current.event} changed the holder")
    if current.issued_at != previous.issued_at:
        raise ProtocolError(f"{current.event} changed issued_at")
    if renewed < prior_renewed:
        raise ProtocolError(f"{current.event} moved renewed_at backward")
    if current.event == "release" and current.context != previous.context:
        raise ProtocolError("release changed diagnostic context")
