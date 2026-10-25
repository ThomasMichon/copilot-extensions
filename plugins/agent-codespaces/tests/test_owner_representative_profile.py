"""Current consumer characterization; no durable inventory/push implementation."""
import json
import threading
from types import SimpleNamespace

import pytest

from agent_codespaces import account_binding, connection_owner as owner, session_forwards as sf
from .test_owner_authority_work import BASELINE, authority_fixture as _authority_fixture

authority_fixture = _authority_fixture

# Representative fixture parameters, never a product/account limit.
PROFILES = (
    ("empty", 0, (), ()),
    ("single-mux", 1, ("a",), (0,)),
    ("balanced-four-mux", 4, ("a", "b", "a", "b"), (0, 1, 2, 3)),
    ("single-account-four-mux", 4, ("a",) * 4, (0, 1, 2, 3)),
    ("balanced-eight-mux", 8, ("a", "b") * 4, tuple(range(8))),
    ("mixed-eight-mux-slice", 8, ("a", "b") * 4, (0, 1, 2, 3)),
)


@pytest.mark.parametrize("profile,active,accounts,mux_indices", PROFILES)
@pytest.mark.parametrize("retained", [0, 12])
@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("bridge_forward", [False, True])
@pytest.mark.parametrize("implementation", ["baseline", "draft"])
async def test_existing_consumer_representative_profile(
    authority_fixture, capsys, profile, active, accounts, mux_indices,
    retained, provider, bridge_forward, implementation,
):
    f = authority_fixture
    binding_lock = threading.RLock()
    f.monkeypatch.setattr(account_binding, "_binding_lock", lambda **kwargs: binding_lock)
    f.state.update(
        accounts=["a", "b"], muxes=1, retained=retained,
        assignments={i: accounts[i] if i < active else "ab"[i % 2]
                     for i in range(active + retained)},
    )
    f.prepare(active, provider)
    for index in range(active):
        if index not in mux_indices:
            owner.release(f"cs-{index}", "t-0")
    module = f.baseline if implementation == "baseline" else sf
    now = [1000]
    forwards = module.SessionForwards(
        lambda *args: None,
        f.production_probe() if implementation == "draft" else module.make_remote_mux_probe(),
        bridge_probe=module.make_remote_bridge_probe() if bridge_forward else None,
        clock=lambda: now[0],
    )
    if bridge_forward:
        for hold in owner.list_holds():
            forwards._channels[hold.codespace] = (12345, SimpleNamespace(is_alive=True))
    previous = {}
    for phase in ("cold-due", "unchanged-not-due", "warm-due"):
        if phase == "warm-due":
            now[0] += 121
        await forwards.probe(owner.list_holds())
        counts = {key: value - previous.get(key, 0) for key, value in f.counts.items()
                  if value != previous.get(key, 0)}
        expected = 0 if phase == "unchanged-not-due" else len(mux_indices)
        complete = counts.get("owner.heartbeat", 0) == expected and not counts.get("authority.scope_error")
        receipt = {
            "profile": profile, "accounts": 2, "active_venues": active,
            "retained_stopped_venues": retained, "sessions_per_active_venue": 1,
            "mode": "mixed-fleet-mux-slice-only" if "mixed" in profile else "mux-only",
            "mux_venues_observed": len(mux_indices),
            "acp_venues_unmeasured": active - len(mux_indices),
            "bridge_forward_health_consumer": bridge_forward,
            "extra_copilot_sessions_for_bridge_health": 0,
            "implementation": implementation, "phase": phase,
            "credential_backend": "provider-keyring" if provider else "gh-cli",
            "counts": counts,
            "process_boundary_requests": sum(v for k, v in counts.items() if k.startswith("process.")),
            "provider_api_calls": counts.get("api.credential_store.get", 0),
            "expected_mux_observations": expected,
            "completed_mux_observations": counts.get("owner.heartbeat", 0),
            "observation_outcome": "complete" if complete else "partial",
            "source_baseline_consumer": BASELINE,
            "cache_push_implementation": "absent-not-measured",
            "authority_contract_equivalent": False,
            "native_cpu_gh_internal_http": "not-measured",
            "registry_locks": "matched-synthetic-with-real-binding-file-reads",
            "acceptance": "characterization-not-performance-qualification",
        }
        with capsys.disabled():
            print("PROFILEWORK " + json.dumps(receipt, sort_keys=True), flush=True)
        assert complete
        assert not f.unknown
        assert counts.get("manager.open", 0) == counts.get("manager.close", 0)
        assert counts.get("process.ssh.exec", 0) == (
            0 if phase == "unchanged-not-due" else expected * (1 + int(bridge_forward))
        )
        assert counts.get("process.inventory.list", 0) == (
            0 if not expected else 2 * expected if implementation == "baseline" else 2
        )
        if not expected:
            assert not counts
        previous = dict(f.counts)


@pytest.mark.parametrize("kind", ["stopped", "page-limit"])
@pytest.mark.parametrize("provider", [False, True])
async def test_representative_retained_inventory_never_grants_wake(authority_fixture, capsys, kind, provider):
    f = authority_fixture
    binding_lock = threading.RLock()
    f.monkeypatch.setattr(account_binding, "_binding_lock", lambda **kwargs: binding_lock)
    f.state.update(accounts=["a", "b"], muxes=1, assignments={i: "ab"[i % 2] for i in range(102)})
    f.prepare(1, provider)
    f.state.update(count=0, retained=1 if kind == "stopped" else 102)
    first = owner.get_hold("cs-0")
    await sf.SessionForwards(lambda *args: None, sf.make_remote_mux_probe()).probe([first])
    assert f.counts["process.inventory.list"] == 2
    assert f.counts["process.ssh.config"] == f.counts["process.ssh.exec"] == 0
    assert f.counts["manager.open"] == f.counts["manager.close"] == 0
    assert not f.unknown
    if kind == "stopped":
        assert owner.get_hold("cs-0") is None
    else:
        assert owner.get_hold("cs-0").sessions == first.sessions
    with capsys.disabled():
        print("PROFILEWORK " + json.dumps({
            "profile": "retained-inventory-control", "case": kind, "accounts": 2,
            "active_venues": 0, "retained_stopped_venues": f.state["retained"],
            "credential_backend": "provider-keyring" if provider else "gh-cli",
            "counts": dict(f.counts), "remote_observation_effects": 0,
            "result": "confirmed-stopped-release" if kind == "stopped" else "incomplete-unknown-retained",
            "acceptance": "correctness-control-not-performance-savings",
        }, sort_keys=True), flush=True)
