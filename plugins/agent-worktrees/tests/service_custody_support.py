"""Synthetic permanent resource metadata; never live adoption evidence."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from agent_worktrees.lease_protocol import LeaseRecord, format_timestamp, resource
from agent_worktrees.service_custody_protocol import ServiceCustodyRecord

BASE = datetime(2026, 8, 6, 20, tzinfo=timezone.utc)
PARENT = "a" * 40


def task_record() -> LeaseRecord:
    item = resource("machine", "service-host")
    return LeaseRecord(
        schema_version=1,
        resource={"identity": item.identity, "kind": item.kind, "key": item.key},
        state="leased",
        event="acquire",
        lease_id="a" * 32,
        holder="task-owner",
        issued_at=format_timestamp(BASE),
        renewed_at=format_timestamp(BASE),
        expires_at=format_timestamp(BASE + timedelta(seconds=60)),
        ttl_seconds=60,
        context={},
    )


def custody_record(**changes) -> ServiceCustodyRecord:
    value = ServiceCustodyRecord(
        schema_version=1,
        resource=task_record().resource,
        state="service-owned",
        event="adopt",
        custody_id="b" * 32,
        fleet_id="example-fleet",
        controller_id="example-controller",
        provider_id="example-provider",
        provider_resource_id="immutable-instance",
        allocation_id="example-allocation",
        adoption_id="example-adoption",
        predecessor=PARENT,
        approved_digest="1" * 64,
        provider_receipt="2" * 64,
        controller_receipt="3" * 64,
        accepted_at=format_timestamp(BASE + timedelta(seconds=10)),
        acceptance_margin_seconds=10,
    )
    return replace(value, **changes)
