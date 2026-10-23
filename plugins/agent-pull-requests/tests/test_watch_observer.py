"""Finite account-scoped observation, with real native/disk/process boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import replace

import pytest
from agent_procutil import no_window_kwargs

from agent_pull_requests import watch_observer as observer, watch_replay_store as storage
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_observer import NativeObservationBinding, ReplayObserver
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayCallerExpired, ReplayStore
from agent_pull_requests.watch_storage import StateCommitUncertain

SCOPE = ConsumerScope("consumer-a", "account-a", "example/project")
PEER = replace(SCOPE, consumer="consumer-b")
FOREIGN = replace(SCOPE, account="account-b")


@pytest.fixture
def store(tmp_path):
    result = ReplayStore(tmp_path.resolve(), "fleet/service/installation")
    result.provision()
    return result


def execute(store, operation, payload=None, scope=SCOPE):
    return store.execute(scope, operation, payload or {}, expires_at=int(store.clock()) + 60)


def register(store, scope=SCOPE, request="request", number=1, **kwargs):
    payload = dict(request_id=request, issued_at=store.clock(), number=number,
                   until=("merged",), **kwargs)
    return payload, execute(store, "register", payload, scope)


def binding(scope=SCOPE, **kwargs):
    return NativeObservationBinding(scope.account, scope.repo, sys.executable, **kwargs)


def cycle(owner, **kwargs):
    return owner.cycle(expires_at=int(owner.store.clock()) + 60, **kwargs)


def start_cycle(owner, **kwargs):
    result = Future()

    def run():
        try:
            result.set_result(cycle(owner, **kwargs))
        except BaseException as error:
            result.set_exception(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def test_coalesces_consumers_but_not_accounts_and_skips_pending(store, monkeypatch):
    for scope in (SCOPE, PEER, FOREIGN):
        register(store, scope)
    calls = []

    def fetch(repo, number, command, **kwargs):
        calls.append((repo, number, command, kwargs["credential_command"]))
        return PRSnapshot(merged=True)

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(
        binding(credential_command=(sys.executable, "account-a")),
        binding(FOREIGN, credential_command=(sys.executable, "account-b")),
    ))
    assert cycle(owner) == {"status": "observed", "observed": 2, "unavailable": 0, "unbound": 0}
    assert sorted(item[3] for item in calls) == [
        [sys.executable, "account-a"], [sys.executable, "account-b"],
    ]
    events = [execute(store, "pending", scope=scope)["events"][0]
              for scope in (SCOPE, PEER, FOREIGN)]
    assert len({event["event_id"] for event in events}) == 3
    assert len({event["registration_id"] for event in events}) == 3
    assert cycle(owner)["observed"] == 0
    assert len(calls) == 2


def test_unbound_account_or_repository_never_uses_another_binding(store, monkeypatch):
    register(store)
    register(store, FOREIGN)
    register(store, replace(SCOPE, repo="example/other"))
    calls = []
    monkeypatch.setattr(observer, "fetch_snapshot",
                        lambda *args, **kwargs: calls.append(args) or PRSnapshot(merged=True))
    assert cycle(ReplayObserver(store, bindings=(binding(),))) == {
        "status": "observed", "observed": 1, "unavailable": 0, "unbound": 2,
    }
    assert len(calls) == 1
    assert execute(store, "pending", scope=FOREIGN)["events"] == []


def test_fetch_releases_disk_custody_and_does_not_apply_to_new_registration(store, monkeypatch):
    monkeypatch.setattr(observer, "_MAX_READS", 1)
    original, receipt = register(store)
    second = ReplayStore(store.root, store.namespace)
    fresh = []

    def fetch(*args, **kwargs):
        fresh.append(register(second, request="new"))
        execute(second, "cancel", {
            "request_id": original["request_id"], "issued_at": original["issued_at"],
            "registration_id": receipt["registration_id"],
        })
        return PRSnapshot(merged=True)

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    assert cycle(ReplayObserver(store, bindings=(binding(),)))["observed"] == 1
    assert execute(second, "pending")["events"] == []
    assert execute(second, "inspect", {
        "request_id": fresh[0][0]["request_id"], "issued_at": fresh[0][0]["issued_at"],
    }) == fresh[0][1]
    monkeypatch.setattr(observer, "fetch_snapshot", lambda *a, **kw: PRSnapshot(merged=True))
    cycle(ReplayObserver(second, bindings=(binding(),)))
    assert execute(store, "pending")["events"][0]["registration_id"] == fresh[0][1]["registration_id"]


@pytest.mark.parametrize("completion", ["timeout", "replacement"])
def test_earlier_read_skips_completed_target_and_preserves_later_progress(
    store, monkeypatch, completion,
):
    monkeypatch.setattr(observer, "_MAX_READS", 1)
    now = [time.time()]
    store.clock = lambda: now[0]
    register(store)
    original, receipt = register(store, request="second", number=2, deadline_at=now[0] + 10)
    register(store, request="third", number=3)
    calls = []
    replacement = []

    def fetch(repo, number, *args, **kwargs):
        calls.append(number)
        if number == 1:
            if completion == "timeout":
                now[0] += 11
            else:
                execute(store, "cancel", {
                    "request_id": original["request_id"], "issued_at": original["issued_at"],
                    "registration_id": receipt["registration_id"],
                })
                replacement.append(register(store, request="new-second", number=2))
        return PRSnapshot(merged=True)

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(binding(),))
    assert cycle(owner) == {"status": "observed", "observed": 2, "unavailable": 0, "unbound": 0}
    assert calls == [1, 3]
    events = execute(store, "pending")["events"]
    assert {event["number"] for event in events if not event["timed_out"]} == {1, 3}
    if completion == "timeout":
        timeout = next(event for event in events if event["number"] == 2)
        assert timeout["timed_out"] and timeout["registration_id"] == receipt["registration_id"]
    else:
        assert {event["number"] for event in events} == {1, 3}
        assert execute(store, "register", replacement[0][0]) == replacement[0][1]
        assert cycle(owner)["observed"] == 1
        assert calls == [1, 3, 2]


def test_provider_outage_preserves_identity_deadline_and_sweeps_during_read(store, monkeypatch):
    now = [time.time()]
    store.clock = lambda: now[0]
    original, receipt = register(store, deadline_at=now[0] + 10)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        now[0] += 11
        raise ValueError("synthetic provider unavailable")

    monkeypatch.setattr(observer, "fetch_snapshot", unavailable)
    owner = ReplayObserver(store, bindings=(binding(),))
    assert cycle(owner)["unavailable"] == 1
    event = execute(store, "pending")["events"][0]
    assert event["timed_out"] and event["registration_id"] == receipt["registration_id"]
    assert execute(store, "register", original) == receipt
    assert cycle(owner)["observed"] == 0 and len(calls) == 1
    assert execute(store, "pending")["events"] == [event]


def test_preexisting_timeout_needs_no_provider_and_unlimited_wait_survives_loss(store, monkeypatch):
    register(store, deadline_at=store.clock() - 1)
    original, receipt = register(store, request="unlimited", number=2)
    calls = []

    def unavailable(repo, number, *args, **kwargs):
        calls.append(number)
        raise ValueError("unavailable")

    monkeypatch.setattr(observer, "fetch_snapshot", unavailable)
    cycle(ReplayObserver(store, bindings=(binding(),)))
    assert calls == [2]
    assert execute(store, "register", original) == receipt
    assert len(execute(store, "pending")["events"]) == 1


def test_oversized_event_is_target_local_across_repeated_cycles(store, monkeypatch):
    now = [time.time()]
    store.clock = lambda: now[0]
    original, receipt = register(store, deadline_at=now[0] + 10)
    register(store, request="other", number=2)
    large = decode_snapshot({
        "schema": "agent-worktrees.pr-snapshot", "version": 1, "repo": SCOPE.repo, "pr": 1,
        "provider": "github", "pr_state": "closed", "merged": True, "head_sha": "head",
        "base_ref": "main", "mergeable": True, "checks_state": "success",
        "title": "Fixture", "draft": False, "reviews": [{"body": "x" * 30000}],
    }, SCOPE.repo, 1)
    calls = []

    def fetch(repo, number, *args, **kwargs):
        calls.append(number)
        return large if number == 1 else PRSnapshot(merged=True)

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(binding(),))
    assert cycle(owner) == {"status": "observed", "observed": 1, "unavailable": 1, "unbound": 0}
    other_event = execute(store, "pending")["events"][0]
    assert other_event["number"] == 2
    assert execute(store, "register", original) == receipt
    assert cycle(owner) == {"status": "observed", "observed": 0, "unavailable": 1, "unbound": 0}
    assert calls == [1, 2, 1]
    assert execute(store, "pending")["events"] == [other_event]
    now[0] += 11
    assert cycle(owner)["observed"] == 0
    assert calls == [1, 2, 1]
    events = execute(store, "pending")["events"]
    timeout = next(event for event in events if event["number"] == 1)
    assert timeout["timed_out"] and timeout["registration_id"] == receipt["registration_id"]
    assert other_event in events


@pytest.mark.parametrize("error", [
    ValueError("invalid observation"), storage.ReplayStoreInvalid("corrupt store"),
])
def test_other_observation_refusals_stop_cycle(store, monkeypatch, error):
    monkeypatch.setattr(observer, "_MAX_READS", 1)
    register(store)
    register(store, request="other", number=2)
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        return PRSnapshot(merged=True)

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    monkeypatch.setattr(store, "observe", refuse)
    with pytest.raises(type(error), match=str(error)):
        cycle(ReplayObserver(store, bindings=(binding(),)))
    assert len(calls) == 1


@pytest.mark.parametrize("fail_read", [False, True])
def test_authority_loss_in_read_stops_apply_and_later_reads(store, monkeypatch, fail_read):
    monkeypatch.setattr(observer, "_MAX_READS", 1)
    register(store)
    register(store, request="second", number=2)
    lost = [False]
    calls = []

    def check():
        if lost[0]:
            raise PermissionError("synthetic disconnect")

    def fetch(*args, **kwargs):
        calls.append(args)
        lost[0] = True
        if fail_read:
            raise ValueError("unavailable")
        return PRSnapshot(merged=True)

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    with pytest.raises(PermissionError, match="disconnect"):
        cycle(ReplayObserver(store, bindings=(binding(),)), checkpoint=check)
    assert len(calls) == 1
    assert execute(store, "pending")["events"] == []
    assert len(store.observation_targets(expires_at=int(store.clock()) + 60)) == 2


def test_expiry_in_provider_read_does_not_commit_snapshot(store, monkeypatch):
    now = [time.time()]
    store.clock = lambda: now[0]
    register(store)

    def fetch(*args, **kwargs):
        now[0] += 61
        return PRSnapshot(merged=True)

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    with pytest.raises(ReplayCallerExpired):
        cycle(ReplayObserver(store, bindings=(binding(),)))
    assert execute(store, "pending")["events"] == []


@pytest.mark.parametrize("failure", ["pre-replace", "uncertain", "disconnect"])
def test_observation_commit_loss_reconciles_original_event(store, monkeypatch, failure):
    original, receipt = register(store)
    write = storage._atomic_write_json
    lost = [False]

    def check():
        if lost[0]:
            raise PermissionError("disconnected")

    def fail(path, state):
        pending = any(entry["pending"] for group in state["registries"]
                      for entry in group["subscriptions"])
        if not pending:
            return write(path, state)
        if failure == "pre-replace":
            raise OSError("synthetic failure")
        write(path, state)
        if failure == "uncertain":
            raise StateCommitUncertain("synthetic")
        lost[0] = True

    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    monkeypatch.setattr(observer, "fetch_snapshot", lambda *a, **kw: PRSnapshot(merged=True))
    owner = ReplayObserver(store, bindings=(binding(),))
    if failure == "uncertain":
        assert cycle(owner) == {"status": "uncertain", "reason": "state_commit_uncertain"}
    else:
        with pytest.raises(OSError if failure == "pre-replace" else PermissionError):
            cycle(owner, checkpoint=check)
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    restored = ReplayStore(store.root, store.namespace)
    assert execute(restored, "register", original) == receipt
    if failure == "pre-replace":
        assert execute(restored, "pending")["events"] == []
        cycle(ReplayObserver(restored, bindings=(binding(),)))
    event = execute(restored, "pending")["events"][0]
    assert event["registration_id"] == receipt["registration_id"]
    assert cycle(ReplayObserver(restored, bindings=(binding(),)))["observed"] == 0
    assert execute(store, "pending")["events"] == [event]


def test_parallel_cycle_is_refused_without_waiting_for_inflight_provider(store, monkeypatch):
    register(store)
    entered, release = threading.Event(), threading.Event()
    results = []

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return PRSnapshot()

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(binding(),))
    thread = threading.Thread(target=lambda: results.append(cycle(owner)))
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(RuntimeError, match="already running"):
            cycle(owner)
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert results[0]["observed"] == 1


def test_slow_target_does_not_block_bounded_refill_or_shared_delivery(store, monkeypatch):
    for number in range(1, 8):
        register(store, request=f"request-{number}", number=number)
    register(store, PEER, number=2)
    register(store, FOREIGN, number=8)
    slow, release, full, advance, refilled = (threading.Event() for _ in range(5))
    lock = threading.Lock()
    calls, active, peak = [], 0, 0
    observe = store.observe
    applied = []
    delivered = []

    def fetch(repo, number, *args, **kwargs):
        nonlocal active, peak
        with lock:
            calls.append(number)
            active += 1
            peak = max(peak, active)
            if active == 4:
                full.set()
        try:
            if number == 1:
                slow.set()
                assert release.wait(8)
            else:
                assert advance.wait(8)
            return PRSnapshot(merged=True)
        finally:
            with lock:
                active -= 1

    def commit(target, snapshot, **kwargs):
        result = observe(target, snapshot, **kwargs)
        applied.append(target.number)
        if set(applied) == set(range(2, 8)):
            delivered.extend((
                execute(store, "pending")["events"],
                execute(store, "pending", scope=PEER)["events"][0],
            ))
            refilled.set()
        return result

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    monkeypatch.setattr(store, "observe", commit)
    owner = ReplayObserver(store, bindings=(binding(),))
    thread, result = start_cycle(owner)
    try:
        assert slow.wait(3) and full.wait(3)
        assert sorted(calls) == [1, 2, 3, 4]
        advance.set()
        assert refilled.wait(5)
        assert thread.is_alive() and not result.done()
        assert peak == 4 and active == 1
        assert sorted(calls) == list(range(1, 8))
        events, peer_event = delivered
        shared = next(event for event in events if event["number"] == 2)
        assert shared["event_id"] != peer_event["event_id"]
        assert shared["registration_id"] != peer_event["registration_id"]
        with pytest.raises(RuntimeError, match="already running"):
            cycle(owner)
    finally:
        advance.set()
        release.set()
        thread.join(8)
    assert not thread.is_alive()
    assert result.result() == {
        "status": "observed", "observed": 7, "unavailable": 0, "unbound": 1,
    }


@pytest.mark.parametrize("ending", ["disconnect", "uncertain", "invalid"])
def test_parallel_failure_stops_refill_but_joins_actual_reads(store, monkeypatch, ending):
    for number in range(1, 8):
        register(store, request=f"request-{number}", number=number)
    entered, release, lost, failed = (threading.Event() for _ in range(4))
    calls = []
    observe = store.observe

    def checkpoint():
        if lost.is_set():
            failed.set()
            raise PermissionError("disconnected")

    def fetch(repo, number, *args, **kwargs):
        calls.append(number)
        if number == 1:
            entered.set()
            assert release.wait(8)
        else:
            assert entered.wait(3)
        return PRSnapshot(merged=True)

    def commit(target, snapshot, **kwargs):
        if ending == "disconnect":
            lost.set()
            checkpoint()
        if ending == "invalid":
            failed.set()
            raise ValueError("invalid state")
        observe(target, snapshot, **kwargs)
        failed.set()
        return {"status": "uncertain", "reason": "state_commit_uncertain"}

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    monkeypatch.setattr(store, "observe", commit)
    owner = ReplayObserver(store, bindings=(binding(),))
    thread, result = start_cycle(owner, checkpoint=checkpoint)
    try:
        assert failed.wait(5)
        assert thread.is_alive() and not result.done()
        assert set(calls) <= {1, 2, 3, 4}
        with pytest.raises(RuntimeError, match="already running"):
            cycle(owner)
    finally:
        release.set()
        thread.join(8)
    assert not thread.is_alive()
    if ending == "uncertain":
        assert result.result()["status"] == "uncertain"
        events = execute(store, "pending")["events"]
        assert len(events) == 1
        assert cycle(ReplayObserver(store, bindings=()))["observed"] == 0
        assert execute(store, "pending")["events"] == events
    else:
        with pytest.raises(PermissionError if ending == "disconnect" else ValueError):
            result.result()
        assert execute(store, "pending")["events"] == []
    assert set(calls) <= {1, 2, 3, 4}


def test_real_native_slow_child_does_not_delay_another_target(store, tmp_path, monkeypatch):
    register(store)
    _, receipt = register(store, request="second", number=2)
    native, entered, release = (tmp_path / name for name in ("native.py", "entered", "release"))
    native.write_text(
        "import json,pathlib,sys,time\n"
        f"entered=pathlib.Path({str(entered)!r})\n"
        f"release=pathlib.Path({str(release)!r})\n"
        "number=int(sys.argv[4])\n"
        "deadline=time.monotonic()+8\n"
        "if number==1: entered.touch()\n"
        "while not (release.exists() if number==1 else entered.exists()):\n"
        "    assert time.monotonic()<deadline\n"
        "    time.sleep(.01)\n"
        f"print(json.dumps(dict(schema='agent-worktrees.pr-snapshot',version=1,repo={SCOPE.repo!r},"
        "pr=number,provider='github',pr_state='closed',merged=True,head_sha='head',"
        "base_ref='main',mergeable=True,checks_state='success',title='Fixture',draft=False)))\n",
        encoding="utf-8",
    )
    applied = threading.Event()
    events = []
    observe = store.observe

    def commit(target, snapshot, **kwargs):
        result = observe(target, snapshot, **kwargs)
        if target.number == 2:
            events.extend(execute(store, "pending")["events"])
            applied.set()
        return result

    monkeypatch.setattr(store, "observe", commit)
    owner = ReplayObserver(store, bindings=(binding(prefix=(sys.executable, str(native))),))
    thread, result = start_cycle(owner)
    try:
        assert applied.wait(5)
        assert entered.exists() and not release.exists() and thread.is_alive()
        assert len(events) == 1 and events[0]["registration_id"] == receipt["registration_id"]
    finally:
        release.touch()
        thread.join(10)
    assert not thread.is_alive() and result.result()["observed"] == 2
    assert {event["number"] for event in execute(store, "pending")["events"]} == {1, 2}


def test_deadlines_are_swept_while_every_read_slot_is_blocked(store, monkeypatch):
    now = [time.time()]
    store.clock = lambda: now[0]
    for number in range(1, 5):
        register(store, request=f"request-{number}", number=number)
    original, receipt = register(store, request="deadline", number=5, deadline_at=now[0] + 10)
    full, release, swept, idle = (threading.Event() for _ in range(4))
    lock = threading.Lock()
    calls, events = [], []
    writes, polls = [], []
    sweep = store.sweep_timeouts
    write = storage._atomic_write_json

    def commit(path, state):
        writes.append(state)
        return write(path, state)

    def fetch(repo, number, *args, **kwargs):
        with lock:
            calls.append(number)
            if len(calls) == 4:
                full.set()
        assert release.wait(8)
        return PRSnapshot(merged=True)

    def timeouts(**kwargs):
        result = sweep(**kwargs)
        if full.is_set() and now[0] <= original["deadline_at"]:
            polls.append(result)
            if len(polls) >= 3:
                idle.set()
        if now[0] > original["deadline_at"] and not swept.is_set():
            events.extend(execute(store, "pending")["events"])
            swept.set()
        return result

    monkeypatch.setattr(observer, "fetch_snapshot", fetch)
    monkeypatch.setattr(store, "sweep_timeouts", timeouts)
    monkeypatch.setattr(storage, "_atomic_write_json", commit)
    thread, result = start_cycle(ReplayObserver(store, bindings=(binding(),)))
    try:
        assert full.wait(3)
        assert idle.wait(3)
        assert writes == []
        now[0] += 11
        assert swept.wait(3)
        assert thread.is_alive() and sorted(calls) == [1, 2, 3, 4]
        assert len(events) == 1 and events[0]["timed_out"]
        assert events[0]["registration_id"] == receipt["registration_id"]
        assert len(writes) == 1
    finally:
        release.set()
        thread.join(8)
    assert not thread.is_alive() and result.result()["observed"] == 4
    assert sorted(calls) == [1, 2, 3, 4]
    assert execute(store, "register", original) == receipt


@pytest.mark.parametrize("field", ["prefix", "credential_command"])
def test_binding_refuses_mutable_argv(field):
    with pytest.raises(ValueError, match="immutable"):
        binding(**{field: [sys.executable]})


def test_duplicate_scope_is_not_silently_rebound(store):
    with pytest.raises(ValueError, match="duplicate"):
        ReplayObserver(store, bindings=(binding(), binding()))


def test_real_native_process_and_restarted_consumer_preserve_exact_event_and_ack(store, tmp_path):
    original, receipt = register(store)
    register(store, PEER)
    native = tmp_path / "native.py"
    calls = tmp_path / "calls.jsonl"
    credential = (sys.executable, "synthetic-credential-source")
    envelope = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1, "repo": SCOPE.repo, "pr": 1,
        "provider": "github", "pr_state": "closed", "merged": True, "head_sha": "head",
        "base_ref": "main", "mergeable": True, "checks_state": "success",
        "title": "Fixture", "draft": False,
    }
    native.write_text(
        "import json,pathlib,sys\n"
        f"assert sys.argv[1:6]==['pr-watch','snapshot',{SCOPE.repo!r},'1','--json']\n"
        f"assert sys.argv[6]=='--credential-command' and json.loads(sys.argv[7])=="
        f"{list(credential)!r}\n"
        f"with pathlib.Path({str(calls)!r}).open('a') as stream: stream.write('read\\n')\n"
        f"print(json.dumps({envelope!r}))\n", encoding="utf-8",
    )
    owner = ReplayObserver(store, bindings=(binding(
        prefix=(sys.executable, str(native)), credential_command=credential,
    ),))
    assert cycle(owner)["observed"] == 1
    event = execute(store, "pending")["events"][0]
    code = """
import json,sys,time
from pathlib import Path
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore
store=ReplayStore(Path(sys.argv[1]),sys.argv[2])
scope=ConsumerScope(**json.loads(sys.argv[3]))
def execute(operation,payload):
    return store.execute(scope,operation,payload,expires_at=int(time.time())+60)
event=execute('pending',{})['events'][0]
original=json.loads(sys.argv[4])
payload={key:original[key] for key in ('request_id','issued_at')}
payload.update(registration_id=event['registration_id'],event_id=event['event_id'])
ack=execute('acknowledge',payload)
assert execute('acknowledge',payload)==ack
print(json.dumps({'event':event,'ack':ack}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(store.root), store.namespace,
         json.dumps(vars(SCOPE)), json.dumps(original)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["event"] == event and value["ack"]["status"] == "acknowledged"
    assert value["ack"]["registration_id"] == receipt["registration_id"]
    assert execute(store, "pending")["events"] == []
    peer_event = execute(store, "pending", scope=PEER)["events"][0]
    assert peer_event["event_id"] != event["event_id"]
    assert cycle(owner)["observed"] == 0
    assert calls.read_text().splitlines() == ["read"]
