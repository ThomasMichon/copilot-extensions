"""Optional admission metadata preserves the strict legacy capability body."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest
from fleet_manager.gateway_service_caller import ServiceCaller, ServiceResponse

from agent_pull_requests.watch_admission import PROFILE, TERMINAL_ADMISSION
from agent_pull_requests.watch_replay_client import ReplayClient, ReplayClientError

CAPABILITIES = {
    "protocol": "agent-pull-requests.watch/v1",
    "operations": ["capabilities", "register", "inspect", "pending", "acknowledge", "cancel"],
    "max_batch": 16, "max_batch_bytes": 24576, "receipt_retention_seconds": 86400,
}


class Caller:
    def __init__(self, content_type: str) -> None:
        self.calls = 0
        self.response = ServiceResponse(
            200, (("Content-Type", content_type),), json.dumps(CAPABILITIES).encode(),
        )

    async def request(self, *args: Any, **kwargs: Any) -> ServiceResponse:
        self.calls += 1
        return self.response


def client(caller: Caller) -> ReplayClient:
    return ReplayClient(
        cast(ServiceCaller, caller), route_prefix="/api", target="project", repo="example/project",
    )


def test_optional_terminal_admission_uses_media_profile_not_strict_capability_json() -> None:
    baseline = Caller("application/json")
    assert asyncio.run(client(baseline).capabilities()).registration_admission is None
    caller = Caller(f'application/json; profile="{PROFILE}"')
    capabilities = asyncio.run(client(caller).capabilities())
    admission = capabilities.registration_admission
    assert admission == TERMINAL_ADMISSION
    assert admission is not None
    admission.validate_registration(until=("merged",), deadline_at=120.0)
    with pytest.raises(ValueError, match="terminal predicates"):
        admission.validate_registration(until=("review_changed",), deadline_at=120.0)
    with pytest.raises(ValueError, match="finite deadline"):
        admission.validate_registration(until=("merged",), deadline_at=None)
    assert json.loads(caller.response.body) == CAPABILITIES
    assert caller.calls == baseline.calls == 1


@pytest.mark.parametrize("profile", [
    'profile="urn:unsupported"', f'profile="{PROFILE}"; profile="{PROFILE}"', "profile",
])
def test_unknown_or_ambiguous_admission_profile_refuses_without_mutation(profile: str) -> None:
    caller = Caller("application/json; " + profile)
    with pytest.raises(ReplayClientError) as caught:
        asyncio.run(client(caller).capabilities())
    assert caught.value.outcome_unknown is False and caller.calls == 1
