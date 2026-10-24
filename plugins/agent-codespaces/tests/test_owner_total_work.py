"""Complete consumer accounting, not an acceptance of the retained draft's cost."""
import json
import threading

import pytest

from agent_codespaces import account_binding, connection_owner as owner, session_forwards as sf
from .test_owner_authority_work import BASELINE, authority_fixture as _authority_fixture

authority_fixture = _authority_fixture


@pytest.mark.parametrize("count", [0, 1, 10])
@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize("implementation", ["baseline", "draft"])
async def test_complete_consumer_total_work(authority_fixture, capsys, count, provider, bridge, implementation):
    f = authority_fixture
    # Like the matched owner registry backing, keep count qualification apart
    # from native lock contention. The candidate still reads real binding bytes.
    # Actual store-lock/replacement behavior has its separate native controls.
    binding_lock = threading.RLock()
    f.monkeypatch.setattr(account_binding, "_binding_lock", lambda **kwargs: binding_lock)
    f.prepare(count, provider)
    module = f.baseline if implementation == "baseline" else sf
    now = [1000]
    forwards = module.SessionForwards(
        lambda *args: None,
        f.production_probe() if implementation == "draft" else module.make_remote_mux_probe(),
        bridge_probe=module.make_remote_bridge_probe() if bridge else None,
        clock=lambda: now[0],
    )
    if bridge:
        from types import SimpleNamespace

        for hold in owner.list_holds():
            forwards._channels[hold.codespace] = (12345, SimpleNamespace(is_alive=True))
    previous = {}
    for phase in ("cold-due", "unchanged-not-due", "warm-due"):
        if phase == "warm-due":
            now[0] += 121
        await forwards.probe(owner.list_holds())
        counts = {key: value - previous.get(key, 0) for key, value in f.counts.items()
                  if value != previous.get(key, 0)}
        expected = 0 if phase == "unchanged-not-due" else count * 4
        complete = counts.get("owner.heartbeat", 0) == expected and not counts.get("authority.scope_error")
        receipt = {
            "implementation": implementation, "phase": phase, "venues": count,
            "accounts": 3, "muxes": 4, "bridge": bridge,
            "credential_backend": "provider-keyring" if provider else "gh-cli",
            "counts": counts,
            "process_boundary_requests": sum(v for k, v in counts.items() if k.startswith("process.")),
            "provider_api_calls": counts.get("api.credential_store.get", 0),
            "expected_tenant_observations": expected,
            "completed_tenant_observations": counts.get("owner.heartbeat", 0),
            "observation_outcome": "complete" if complete else "partial",
            "source_baseline": BASELINE,
            "native_processes": "not-executed",
            "gh_internal_http": "opaque-not-measured",
            "wallclock_qualified": False,
            "acceptance": "not-qualified",
            "registry_locks": "matched-synthetic-locks-real-binding-file-reads",
        }
        with capsys.disabled():
            print("TOTALWORK " + json.dumps(receipt, sort_keys=True), flush=True)
        assert complete
        assert counts.get("manager.open", 0) == counts.get("manager.close", 0)
        assert counts.get("process.ssh.exec", 0) == (
            0 if phase == "unchanged-not-due"
            else count * ((4 if implementation == "baseline" else 1) + int(bridge))
        )
        assert counts.get("process.inventory.list", 0) == (
            0 if phase == "unchanged-not-due" or count == 0
            else 3 * count if implementation == "baseline" else 3
        )
        if phase == "unchanged-not-due":
            assert not counts
        assert not f.unknown
        previous = dict(f.counts)


def test_selected_contract_one_venue_minimum_exceeds_retained_cli_baseline():
    # Optimistic: one full read at acquisition and publication, one listing per
    # account using its already-read token, only two Windows wake-capable effects,
    # and zero selected-discovery/config/connection/retry overhead.
    accounts = 3
    full_reads = 2 * (2 + accounts)
    listings = accounts
    fresh_selected_credentials = 2
    config_and_direct_ssh = 2
    minimum = full_reads + listings + fresh_selected_credentials + config_and_direct_ssh
    assert minimum == 17
    assert minimum > 13 > 10  # Retained identical-workload cold and warm baselines.
