"""Sustained real cryptographic admission under the shared replay fence."""

from types import SimpleNamespace

from fleet_manager import gateway_service_server as server
from fleet_manager.gateway_service_channel import REQUEST_BURST, REQUEST_RATE, TransportError
from fleet_manager.gateway_service_listener import Listener
from fleet_manager.runtime_identity import CLOCK_SKEW, MAX_REPLAYS, PROOF_AGE
from test_gateway_service_policy import Harness, TARGET


def test_sustained_admitted_proofs_fit_the_full_future_inclusive_replay_window(monkeypatch):
    horizon = PROOF_AGE + 2 * CLOCK_SKEW + 1
    assert REQUEST_BURST + REQUEST_RATE * horizon <= MAX_REPLAYS
    elapsed = [0.0]
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    harness = Harness()
    harness.owner.reload()
    started = harness.clock.value
    listener = Listener(
        "fleet-a", "gateway-a", "127.0.0.1", 0, "https://localhost:0", "unused", "unused")
    state = server.ServiceRuntime(harness.owner, listener, 1)
    accepted = refused = 0
    offered_rate = 64
    for index in range((horizon + 10) * offered_rate):
        elapsed[0] = index / offered_rate
        harness.clock.value = started + int(elapsed[0])
        try:
            state.admit_request()
        except TransportError as exc:
            assert exc.code == "request-rate-limit" and exc.status == 429
            refused += 1
            continue
        credentials = harness.credentials(
            subject="caller-a", key=3, method="GET",
            proof={"iat": harness.clock.value + CLOCK_SKEW})
        grant = harness.owner.authenticate_caller(
            *credentials, method="GET", request_method="GET",
            target_uri=TARGET, slot="service-a")
        assert harness.owner.release_caller(grant)
        accepted += 1
    assert accepted > MAX_REPLAYS
    assert refused > 0
