"""Transport-neutral replay transactions; no serving/authentication claim."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from agent_procutil import no_window_kwargs

import agent_pull_requests
from agent_pull_requests.watch_contract import MERGED, PRSnapshot
from agent_pull_requests.watch_delivery import Deliveries
from agent_pull_requests.watch_registry import WatchKey, WatchRegistry
from agent_pull_requests.watch_replay import (
    MAX_BATCH_BYTES, MAX_RECORDS, RETENTION_SECONDS, ConsumerScope, WatchReplay,
)
from agent_pull_requests.watch_storage import StateCommitUncertain, _atomic_write_json

SCOPE = ConsumerScope("coordinator-a", "account-a", "example/project")
OTHER = ConsumerScope("coordinator-b", "account-a", "example/project")


@pytest.fixture
def owner(tmp_path):
    now = [time.time()]
    disk = tmp_path / "replay.json"
    core = WatchReplay(
        "fleet/service", state=None, commit=lambda state: _atomic_write_json(disk, state),
        clock=lambda: now[0],
    )
    return core, disk, now


def register(core, now, scope=SCOPE, request="request-1", **kwargs):
    return core.register(scope, request, now, number=1, until=(MERGED,), **kwargs)


def event(core, scope=SCOPE):
    core.observe(scope.account, scope.repo, 1, PRSnapshot(merged=True))
    return core.pending(scope)["events"][0]


def test_lost_register_response_reconciles_without_replacement_or_deadline_reset(owner):
    core, disk, now = owner
    deadline = now[0] + 600
    first = register(core, now[0], deadline_at=deadline)
    pending = event(core)
    restored = WatchReplay("fleet/service", state=json.loads(disk.read_text()),
                          commit=lambda state: _atomic_write_json(disk, state))
    assert register(restored, now[0], deadline_at=deadline) == first
    assert restored.inspect(SCOPE, "request-1", now[0]) == first
    assert restored.pending(SCOPE)["events"] == [pending]
    assert register(restored, now[0], deadline_at=deadline + 1) == {"status": "conflict"}
    assert len(restored.snapshot()["records"]) == 1


def test_read_is_not_ack_and_ack_response_loss_replays_completion(owner):
    core, disk, now = owner
    receipt = register(core, now[0])
    pending = event(core)
    assert core.pending(SCOPE) == core.pending(SCOPE)
    args = dict(registration_id=receipt["registration_id"], event_id=pending["event_id"])
    ack = core.acknowledge(SCOPE, "request-1", now[0], **args)
    assert ack["status"] == "acknowledged"
    assert core.pending(SCOPE) == {"events": [], "more": False}
    restored = WatchReplay("fleet/service", state=json.loads(disk.read_text()),
                          commit=lambda state: _atomic_write_json(disk, state))
    assert restored.acknowledge(SCOPE, "request-1", now[0], **args) == ack
    assert register(restored, now[0]) == ack


def test_known_foreign_identities_cannot_inspect_cancel_or_ack(owner):
    core, _, now = owner
    receipt = register(core, now[0])
    pending = event(core)
    for foreign in (OTHER, ConsumerScope(SCOPE.consumer, "account-b", SCOPE.repo)):
        assert core.inspect(foreign, "request-1", now[0]) == {"status": "uncertain"}
        assert core.pending(foreign) == {"events": [], "more": False}
        assert core.cancel(
            foreign, "request-1", now[0], registration_id=receipt["registration_id"],
        ) == {"status": "uncertain"}
        assert core.acknowledge(
            foreign, "request-1", now[0], registration_id=receipt["registration_id"],
            event_id=pending["event_id"],
        ) == {"status": "uncertain"}
    assert core.pending(SCOPE)["events"] == [pending]


def test_scoped_observation_shares_target_but_not_provider_account(owner):
    core, _, now = owner
    alien = ConsumerScope("coordinator-c", "account-b", SCOPE.repo)
    for scope in (SCOPE, OTHER, alien):
        register(core, now[0], scope)
    event(core)
    assert len(core.pending(SCOPE)["events"]) == len(core.pending(OTHER)["events"]) == 1
    assert core.pending(alien)["events"] == []
    groups = core.snapshot()["registries"]
    assert len(groups) == 2
    assert len(groups[0]["subscriptions"]) == 2
    assert len(groups[1]["subscriptions"]) == 1


def test_stale_ack_and_cancel_cannot_remove_different_request_generation(owner):
    core, _, now = owner
    old = register(core, now[0])
    old_event = event(core)
    current = register(core, now[0], request="request-2")
    assert core.acknowledge(
        SCOPE, "request-2", now[0], registration_id=old["registration_id"],
        event_id=old_event["event_id"],
    ) == {"status": "uncertain"}
    assert core.cancel(
        SCOPE, "request-2", now[0], registration_id=old["registration_id"],
    ) == {"status": "uncertain"}
    assert core.inspect(SCOPE, "request-2", now[0]) == current


def test_cancel_is_durable_idempotent_and_not_an_ack(owner):
    core, _, now = owner
    receipt = register(core, now[0])
    pending = event(core)
    cancelled = core.cancel(
        SCOPE, "request-1", now[0], registration_id=receipt["registration_id"],
    )
    assert cancelled["status"] == "cancelled"
    assert core.cancel(
        SCOPE, "request-1", now[0], registration_id=receipt["registration_id"],
    ) == cancelled
    assert core.acknowledge(
        SCOPE, "request-1", now[0], registration_id=receipt["registration_id"],
        event_id=pending["event_id"],
    ) == {"status": "uncertain"}


def test_retention_expiry_reports_uncertainty_without_creating_second_wait(owner):
    core, _, now = owner
    issued = now[0]
    receipt = register(core, issued)
    now[0] += RETENTION_SECONDS * 2
    assert register(core, issued) == receipt  # Active waits never expire as receipts.
    cancelled = core.cancel(
        SCOPE, "request-1", issued, registration_id=receipt["registration_id"],
    )
    now[0] += RETENTION_SECONDS
    assert core.inspect(SCOPE, "request-1", issued) == {"status": "uncertain"}
    assert register(core, issued) == {
        "status": "uncertain", "reason": "request_outside_retention",
    }
    assert cancelled["status"] == "cancelled"
    assert core.snapshot()["records"] == []


def test_original_expired_deadline_fires_timeout_without_observation(owner):
    core, disk, now = owner
    register(core, now[0], deadline_at=now[0] - 1)
    restored = WatchReplay("fleet/service", state=json.loads(disk.read_text()),
                          commit=lambda state: _atomic_write_json(disk, state))
    restored.sweep_timeouts()
    assert restored.pending(SCOPE)["events"][0]["timed_out"] is True
    assert restored.snapshot()["records"][0]["deadline_at"] == now[0] - 1


@pytest.mark.parametrize("mutation", ["timeout", "retention"])
@pytest.mark.parametrize("failure", [None, "pre-replace", "uncertain"])
def test_timeout_sweep_skips_unchanged_commit_but_preserves_real_changes(mutation, failure):
    now = [time.time()]
    writes = []
    core = WatchReplay("fleet/service", state=None, commit=writes.append, clock=lambda: now[0])
    issued = now[0]
    receipt = register(core, issued, deadline_at=issued + 10)
    if mutation == "retention":
        core.cancel(SCOPE, "request-1", issued, registration_id=receipt["registration_id"])
    before = core.snapshot()
    writes.clear()
    for _ in range(3):
        assert core.sweep_timeouts() == {"status": "swept"}
    assert writes == [] and core.snapshot()["records"] == before["records"]

    def commit(state):
        if failure == "pre-replace":
            raise OSError("synthetic")
        writes.append(state)
        if failure == "uncertain":
            raise StateCommitUncertain("synthetic")

    core._commit = commit
    now[0] += 11 if mutation == "timeout" else RETENTION_SECONDS
    if failure == "pre-replace":
        with pytest.raises(OSError, match="synthetic"):
            core.sweep_timeouts()
        assert writes == [] and core.snapshot()["records"] == before["records"]
        assert core.pending(SCOPE)["events"] == []
        return
    assert core.sweep_timeouts() == (
        {"status": "uncertain", "reason": "state_commit_uncertain"}
        if failure else {"status": "swept"}
    )
    assert len(writes) == 1 and core.snapshot() == writes[0]
    if mutation == "timeout":
        pending = core.pending(SCOPE)["events"][0]
        assert pending["timed_out"] and pending["registration_id"] == receipt["registration_id"]
    else:
        assert core.snapshot()["records"] == []
    assert core.sweep_timeouts() == {"status": "swept"}
    assert len(writes) == 1


@pytest.mark.parametrize("deadline", [2**53 + 1, 10**400])
def test_deadline_precision_refusal_preserves_another_scope_after_reload(owner, deadline):
    core, disk, now = owner
    receipt = register(core, now[0], OTHER, request="other")
    before = disk.read_bytes()
    with pytest.raises(ValueError):
        register(core, now[0], deadline_at=deadline)
    assert disk.read_bytes() == before
    restored = WatchReplay(
        core.namespace, state=json.loads(disk.read_bytes()),
        commit=lambda state: _atomic_write_json(disk, state),
    )
    assert restored.inspect(OTHER, "other", now[0]) == receipt
    assert restored.pending(OTHER) == {"events": [], "more": False}
    assert register(restored, now[0], deadline_at=2**53 + 2)["status"] == "active"
    assert WatchReplay(
        core.namespace, state=json.loads(disk.read_bytes()), commit=lambda _state: None,
    ).inspect(OTHER, "other", now[0]) == receipt


@pytest.mark.parametrize("operation", ["register", "observe", "ack", "cancel"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_write_failure_never_reports_success_or_loses_committed_state(owner, operation, uncertain):
    core, disk, now = owner
    receipt = register(core, now[0])
    pending = event(core)
    before = core.snapshot()

    def fail(state):
        if uncertain:
            _atomic_write_json(disk, state)
            raise StateCommitUncertain("private durability diagnostic")
        raise OSError("private write diagnostic")

    core._commit = fail

    def invoke():
        if operation == "register":
            return register(core, now[0], request="request-2")
        if operation == "observe":
            return core.observe(SCOPE.account, SCOPE.repo, 1, PRSnapshot(merged=True))
        if operation == "ack":
            return core.acknowledge(
                SCOPE, "request-1", now[0], registration_id=receipt["registration_id"],
                event_id=pending["event_id"],
            )
        return core.cancel(SCOPE, "request-1", now[0],
                           registration_id=receipt["registration_id"])

    if uncertain:
        assert invoke() == {"status": "uncertain", "reason": "state_commit_uncertain"}
        assert core.snapshot() == json.loads(disk.read_text())
    else:
        with pytest.raises(OSError, match="private write"):
            invoke()
        assert core.snapshot() == before
        assert json.loads(disk.read_text()) == before


def test_batch_limit_is_consumer_local_and_read_does_not_complete_events(owner):
    core, _, now = owner
    for index in range(20):
        register(core, now[0], request=f"request-{index}")
    register(core, now[0], OTHER)
    event(core)
    first = core.pending(SCOPE)
    assert len(first["events"]) == 16 and first["more"]
    assert len(json.dumps(first).encode()) <= MAX_BATCH_BYTES
    assert len(core.pending(OTHER)["events"]) == 1
    for item in first["events"]:
        record = next(record for record in core.snapshot()["records"]
                      if record["registration_id"] == item["registration_id"])
        core.acknowledge(SCOPE, record["request_id"], record["issued_at"],
                         registration_id=item["registration_id"], event_id=item["event_id"])
    assert len(core.pending(SCOPE)["events"]) == 4
    assert core.pending(SCOPE)["more"] is False


def test_concurrent_retry_commits_one_registration(owner):
    core, _, now = owner
    results = []
    threads = [threading.Thread(target=lambda: results.append(register(core, now[0])))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert len(results) == 4
    assert len({result["registration_id"] for result in results}) == 1


@pytest.mark.parametrize("corruption", ["namespace", "scope", "identity", "callback", "missing"])
def test_corrupt_replay_state_is_refused_without_writes(owner, corruption):
    core, disk, now = owner
    register(core, now[0])
    state = core.snapshot()
    before = disk.read_bytes()
    if corruption == "namespace":
        state["namespace"] = "other/service"
    elif corruption == "scope":
        state["records"][0]["scope"]["account"] = "foreign"
    elif corruption == "identity":
        state["records"][0]["registration_id"] = "foreign"
    elif corruption == "callback":
        state["registries"][0]["subscriptions"][0]["notify"] = {"argv": ["untrusted"]}
    else:
        state["registries"] = []
    with pytest.raises(ValueError, match="persisted watch replay"):
        WatchReplay("fleet/service", state=state, commit=lambda _: pytest.fail("must not write"))
    assert disk.read_bytes() == before


def test_pull_events_never_enter_native_callback_scheduler():
    registry = WatchRegistry()
    key = WatchKey(SCOPE.repo, 1)
    registry.register(key, "pull", until=(MERGED,), notify={},
                      acknowledged=True, delivery_mode="pull")
    registry.register(key, "native", until=(MERGED,), notify={"argv": ["callback"]},
                      acknowledged=True)
    registry.apply_snapshot(key, PRSnapshot(merged=True))
    stop = threading.Event()
    delivered = threading.Event()
    seen = []

    def callback(event):
        seen.append(event.subscriber.subscriber_id)
        delivered.set()
        return 0

    deliveries = Deliveries(registry, callback, lambda *_: None, threading.RLock(), stop)
    assert [item["subscriber_id"] for item in deliveries.status()] == ["native"]
    try:
        deliveries.resume()
        assert delivered.wait(3)
    finally:
        stop.set()
        assert deliveries.close(time.monotonic() + 3) == []
    assert seen == ["native"]
    assert len(registry.pending_events(delivery_mode="pull")) == 1
    restored = WatchRegistry()
    restored.restore_state(registry.snapshot_state(), strict=True)
    assert len(restored.pending_events(delivery_mode="pull")) == 1


@pytest.mark.parametrize("spread", [False, True])
def test_capacity_refuses_new_acceptance_without_evicting_active_requests(owner, monkeypatch, spread):
    core, _, now = owner
    register(core, now[0])
    original = core.snapshot()["records"][0]
    # Synthesize the bounded envelope directly rather than issuing 1,024 fsyncs.
    state = core.snapshot()
    subscription = state["registries"][0]["subscriptions"][0]
    state["records"] = []
    state["registries"] = []
    for index in range(MAX_RECORDS):
        record = copy.deepcopy(original)
        record.update(request_id=f"request-{index}", registration_id=f"generation-{index}")
        if spread:
            record["scope"]["repo"] = f"example/project-{index}"
        state["records"].append(record)
        entry = copy.deepcopy(subscription)
        entry.update(subscriber_id=record["registration_id"],
                     registration_id=record["registration_id"], repo=record["scope"]["repo"])
        if spread or not state["registries"]:
            state["registries"].append({
                "account": SCOPE.account, "repo": record["scope"]["repo"], "subscriptions": [],
            })
        state["registries"][-1]["subscriptions"].append(entry)
    scope_calls = []
    validate = ConsumerScope.__post_init__

    def counted(scope):
        scope_calls.append(1)
        validate(scope)

    monkeypatch.setattr(ConsumerScope, "__post_init__", counted)
    core = WatchReplay("fleet/service", state=state, commit=lambda _: None)
    assert register(core, now[0], request="overflow") == {
        "status": "unavailable", "reason": "replay_capacity",
    }
    assert len(core.snapshot()["records"]) == MAX_RECORDS
    assert len(scope_calls) <= MAX_RECORDS * 3


def test_oversized_observation_never_poison_commits_pending_state(owner):
    core, disk, now = owner
    register(core, now[0])
    before = disk.read_bytes()
    with pytest.raises(ValueError, match="batch byte limit"):
        core.observe(SCOPE.account, SCOPE.repo, 1,
                     PRSnapshot(merged=True, review_decision="x" * MAX_BATCH_BYTES))
    assert disk.read_bytes() == before
    assert core.pending(SCOPE)["events"] == []
    assert event(core)["merged"] is True


@pytest.mark.parametrize("bad", [0, True, float("nan"), float("inf"), "1"])
def test_invalid_request_timestamps_and_deadlines_never_commit(owner, bad):
    core, disk, now = owner
    with pytest.raises(ValueError):
        register(core, bad)
    with pytest.raises(ValueError):
        register(core, now[0], deadline_at=bad)
    assert not disk.exists()


@pytest.mark.parametrize("crash", ["before-ack", "after-ack"])
def test_real_consumer_restart_replays_only_unacknowledged_effect(owner, tmp_path, crash):
    core, disk, now = owner
    register(core, now[0])
    pending = event(core)
    effect = tmp_path / "effects.json"
    code = """
import json, os, sys
from pathlib import Path
from agent_pull_requests.watch_replay import ConsumerScope, WatchReplay
from agent_pull_requests.watch_storage import _atomic_write_json
disk, effect = Path(sys.argv[1]), Path(sys.argv[2])
state = json.loads(disk.read_text())
core = WatchReplay("fleet/service", state=state,
                   commit=lambda value: _atomic_write_json(disk, value))
scope = ConsumerScope("coordinator-a", "account-a", "example/project")
events = core.pending(scope)["events"]
effects = json.loads(effect.read_text()) if effect.exists() else {}
for item in events:
    effects.setdefault(item["event_id"], {"commits": 1})
    _atomic_write_json(effect, effects)
    if sys.argv[3] == "before-ack":
        os._exit(0)
    record = next(record for record in state["records"]
                  if record["registration_id"] == item["registration_id"])
    assert core.acknowledge(scope, record["request_id"], record["issued_at"],
                            registration_id=item["registration_id"],
                            event_id=item["event_id"])["status"] == "acknowledged"
os._exit(0)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(next(iter(agent_pull_requests.__path__)).rsplit(os.sep, 1)[0])
    for phase in (crash, "after-ack"):
        subprocess.run(
            [sys.executable, "-c", code, str(disk), str(effect), phase],
            env=env, check=True, timeout=10, **no_window_kwargs(),
        )
        if phase == "before-ack":
            restored = WatchReplay("fleet/service", state=json.loads(disk.read_text()),
                                  commit=lambda _: None)
            assert restored.pending(SCOPE)["events"] == [pending]
    assert json.loads(effect.read_text()) == {pending["event_id"]: {"commits": 1}}
    restored = WatchReplay("fleet/service", state=json.loads(disk.read_text()),
                          commit=lambda _: None)
    assert restored.pending(SCOPE)["events"] == []
    assert restored.inspect(SCOPE, "request-1", now[0])["status"] == "acknowledged"


def test_missing_ack_identity_is_not_a_cancellation(owner):
    core, _, now = owner
    receipt = register(core, now[0])
    pending = event(core)
    for value in (None, "", False):
        with pytest.raises(ValueError, match="acknowledgement identity"):
            core.acknowledge(SCOPE, "request-1", now[0],
                             registration_id=receipt["registration_id"], event_id=value)
    assert core.pending(SCOPE)["events"] == [pending]


def test_batch_byte_limit_stops_before_count_limit(owner):
    core, _, now = owner
    for index in range(20):
        register(core, now[0], request=f"request-{index}")
    core.observe(SCOPE.account, SCOPE.repo, 1,
                 PRSnapshot(merged=True, review_decision="x" * 2000))
    batch = core.pending(SCOPE)
    assert 1 < len(batch["events"]) < 16
    assert batch["more"] and len(json.dumps(batch).encode()) <= MAX_BATCH_BYTES


def test_injected_clock_controls_restored_absolute_deadlines(tmp_path):
    now = [1000.0]
    disk = tmp_path / "replay.json"
    commit = lambda state: _atomic_write_json(disk, state)
    core = WatchReplay("fleet/service", state=None, commit=commit, clock=lambda: now[0])
    receipt = register(core, now[0], deadline_at=1600.0)
    core.sweep_timeouts()
    assert core.pending(SCOPE)["events"] == []
    now[0] = 1599.0
    core = WatchReplay("fleet/service", state=json.loads(disk.read_text()), commit=commit,
                       clock=lambda: now[0])
    core.sweep_timeouts()
    assert core.pending(SCOPE)["events"] == []
    now[0] = 1600.0
    core.sweep_timeouts()
    pending = core.pending(SCOPE)["events"][0]
    assert pending["timed_out"] and pending["registration_id"] == receipt["registration_id"]
    assert core.inspect(SCOPE, "request-1", 1000.0)["deadline_at"] == 1600.0


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("transition,field", [
    ("head_changed", "head"), ("votes_changed", "votes"),
    ("approvals_changed", "approvals"), ("threads_changed", "discussions"),
    ("policies_changed", "policies"), ("gates_changed", "gates"),
    ("ready_for_human", "readiness"), ("review_changed", "votes"),
])
def test_rich_baseline_survives_every_transaction_and_restart(owner, restart, transition, field):
    core, disk, now = owner
    core.register(SCOPE, "request-1", now[0], number=1, until=(transition,))

    def snapshot(value):
        return PRSnapshot(
            head_sha=value if field == "head" else "stable",
            observation={
                "fingerprints": {field: value} if field not in ("head", "readiness") else {},
                "ready_for_human": field == "readiness" and value == "b",
            },
        )

    core.observe(SCOPE.account, SCOPE.repo, 1, snapshot("a"))
    assert core.pending(SCOPE)["events"] == []
    if restart:
        core = WatchReplay("fleet/service", state=json.loads(disk.read_text()),
                           commit=lambda state: _atomic_write_json(disk, state))
    core.observe(SCOPE.account, SCOPE.repo, 1, snapshot("a"))
    assert core.pending(SCOPE)["events"] == []
    core.observe(SCOPE.account, SCOPE.repo, 1, snapshot("b"))
    assert core.pending(SCOPE)["events"][0]["transitions"] == [transition]


@pytest.mark.parametrize("corruption", ["missing", "null", "head", "fingerprints", "field"])
def test_pull_baseline_corruption_cannot_silently_reset_observation(owner, corruption):
    core, _, now = owner
    register(core, now[0])
    core.observe(SCOPE.account, SCOPE.repo, 1,
                 PRSnapshot(head_sha="a", observation={"fingerprints": {"votes": "a"}}))
    state = core.snapshot()
    entry = state["registries"][0]["subscriptions"][0]
    if corruption == "missing":
        del entry["pull_baseline"]
    elif corruption == "null":
        entry["pull_baseline"] = None
    elif corruption == "head":
        entry["pull_baseline"]["head_sha"] = 1
    elif corruption == "fingerprints":
        entry["pull_baseline"]["fingerprints"]["votes"] = []
    else:
        entry["pull_baseline"]["fingerprints"]["unknown"] = "value"
    with pytest.raises(ValueError, match="persisted watch replay"):
        WatchReplay("fleet/service", state=state, commit=lambda _: pytest.fail("must not write"))
