"""Prepared attachment never replaces the active owner until promotion."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fleet_manager.gateway_service_policy import ServicePolicyError
from test_gateway_service_policy import Harness, harness as harness


def candidate(harness: Harness):
    return harness.owner.prepare_service(
        *harness.credentials(), target_uri="https://gateway.example/api", slot="service-a")


def test_unused_or_forged_candidate_preserves_active_generation(harness: Harness) -> None:
    harness.owner.reload()
    active = harness.service()
    prepared = candidate(harness)
    assert harness.owner.revalidate_service(active).name == "service-a"
    assert not harness.owner.release_candidate(replace(prepared))
    with pytest.raises(ServicePolicyError, match="^invalid-candidate$"):
        harness.owner.promote_service(replace(prepared))
    assert harness.owner.release_candidate(prepared)
    assert harness.owner.revalidate_service(active).name == "service-a"
    assert not harness.owner.release_candidate(prepared)


def test_ready_newer_candidate_fences_predecessor_and_older_ticket(harness: Harness) -> None:
    harness.owner.reload()
    active = harness.service()
    older, newer = candidate(harness), candidate(harness)
    current = harness.owner.promote_service(newer)
    with pytest.raises(ServicePolicyError):
        harness.owner.promote_service(older)
    with pytest.raises(ServicePolicyError):
        harness.owner.revalidate_service(active)
    assert not harness.owner.release_service(active)
    assert harness.owner.revalidate_service(current).name == "service-a"


def test_later_prepared_candidate_can_follow_ready_predecessor(harness: Harness) -> None:
    harness.owner.reload()
    first, later = candidate(harness), candidate(harness)
    active = harness.owner.promote_service(first)
    replacement = harness.owner.promote_service(later)
    assert replacement.generation > active.generation
    assert not harness.owner.release_service(active)
    assert harness.owner.revalidate_service(replacement).name == "service-a"


def test_revoked_pending_candidate_never_acquires_slot(harness: Harness) -> None:
    harness.owner.reload()
    pending = candidate(harness)
    harness.document["manifest"]["slots"][0]["principal"] = "replacement"
    harness.update()
    with pytest.raises(ServicePolicyError):
        harness.owner.promote_service(pending)
