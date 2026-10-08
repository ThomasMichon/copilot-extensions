"""Producer pins are observable and recovery never outraces a renewal."""

from types import SimpleNamespace

import httpx
import pytest

from agent_dispatch import emitter_diagnostics as diagnostics
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.queue import TaskError, TaskQueue


SPEC = {"id": "readiness", "interval_seconds": 300, "timeout_seconds": 90}


@pytest.mark.parametrize(
    "renewed_at,state,recoverable",
    [(990, "remote-lease-fresh", False), (10, "lease-stale", True),
     (None, "lease-renewal-unknown", False), (1100, "lease-renewal-unknown", False)],
)
def test_foreign_pin_does_not_masquerade_as_production(renewed_at, state, recoverable):
    result = diagnostics.diagnose(
        SPEC, holder="example-host-wsl",
        lease={"holder": "example-host", "renewed_at": renewed_at},
        health={"updated_at": 999, "held": False, "ok": True}, now=1000,
    )
    assert result["production_state"] == state
    assert result["recovery_candidate"] is recoverable


def test_local_tick_requires_command_success_and_matching_election():
    lease = {"holder": "example-host-wsl", "renewed_at": 990}
    health = {"updated_at": 999, "held": False, "ok": True, "lease": lease}
    result = diagnostics.diagnose(SPEC, holder=lease["holder"], lease=lease,
                                  health=health, now=1000)
    assert result["production_state"] == "lease-not-held"
    health.update(held=True, returncode=0)
    assert diagnostics.diagnose(SPEC, holder=lease["holder"], lease=lease,
                                health=health, now=1000)["production_state"] == "tick-succeeded"


def test_ineligible_target_never_offers_recovery():
    result = diagnostics.diagnose(
        SPEC, holder="example-host", lease={"holder": "remote", "renewed_at": 1},
        eligible=False, lease_holder_eligible=False, now=1000,
    )
    assert result["findings"] == ["local-holder-ineligible", "lease-holder-ineligible"]
    assert result["recovery_candidate"] is False


def test_recovery_fences_changes_and_does_not_claim_production(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    scope = "emitter:readiness"
    queue.acquire_schedule_lease(scope, "example-host", now=10)
    client = SimpleNamespace(release_schedule_lease=lambda scope, holder, **kw: {
        "released": queue.release_schedule_lease(scope, holder, **kw),
    })
    diagnosis = diagnostics.diagnose(
        SPEC, holder="example-host-wsl",
        lease={"holder": "example-host", "renewed_at": 10}, now=1000,
    )
    with pytest.raises(ValueError):
        diagnostics.recover(client, diagnosis, expected_holder="wrong")
    queue.acquire_schedule_lease(scope, "example-host", now=999)
    with pytest.raises(TaskError, match="renewed"):
        diagnostics.recover(client, diagnosis, expected_holder="example-host")
    assert queue.get_schedule_lease(scope).holder == "example-host"
    queue.release_schedule_lease(scope, "example-host")
    queue.acquire_schedule_lease(scope, "different-host", now=10)
    with pytest.raises(TaskError, match="is held by"):
        diagnostics.recover(client, diagnosis, expected_holder="example-host")
    queue.release_schedule_lease(scope, "different-host")
    queue.acquire_schedule_lease(scope, "example-host", now=10)
    result = diagnostics.recover(client, diagnosis, expected_holder="example-host")
    assert result["production_verified"] is False
    assert queue.get_schedule_lease(scope) is None


def test_old_coordinator_cannot_silently_ignore_the_renewal_fence():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(404, json={"detail": "Not Found"})

    client = DispatchClient("http://example.com", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DispatchError):
            client.release_schedule_lease(
                "emitter:readiness", "example-host", expected_renewed_at=10,
            )
    finally:
        client.close()
    assert requests == ["/schedule-leases/emitter:readiness/release-observed"]


def test_inspection_uses_source_eligibility_not_stale_election_health(tmp_path):
    import json

    spec_path = tmp_path / "emitter.json"
    spec_path.write_text(json.dumps(SPEC), encoding="utf-8")
    spec_path.with_suffix(".health.json").write_text(
        json.dumps({"updated_at": 999, "ok": True, "held": False}), encoding="utf-8",
    )
    declaration = tmp_path / "emitter.yaml"
    declaration.write_text(
        "name: readiness\nkind: emitter\n"
        "spec:\n  id: readiness\n  command: [echo, ready]\n  interval_seconds: 300\n"
        "filters:\n  permit:\n    machine: [example-host-wsl]\n",
        encoding="utf-8",
    )
    client = SimpleNamespace(get_schedule_lease=lambda _scope: {
        "holder": "example-host", "renewed_at": 10,
    })
    guest = diagnostics.inspect(
        client, str(spec_path), SPEC, holder="example-host-wsl",
        declaration_path=str(declaration),
    )
    assert guest["eligible"] is True
    assert guest["lease_holder_eligible"] is False
    assert guest["recovery_candidate"] is True
    native = diagnostics.inspect(
        client, str(spec_path), SPEC, holder="example-host",
        declaration_path=str(declaration),
    )
    assert native["eligible"] is False
    assert native["recovery_candidate"] is False
