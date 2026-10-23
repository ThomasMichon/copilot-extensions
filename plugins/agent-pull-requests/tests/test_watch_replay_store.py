"""Real disk/custody/restart boundary for the optional replay adapter."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import replace

import pytest
from agent_procutil import no_window_kwargs
from single_instance_lease import OperationLock, OperationLockedError

import agent_pull_requests.watch_replay_store as storage
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import (
    ReplayCallerExpired, ReplayServiceIdentity, ReplayStore, ReplayStoreInvalid, json_object,
)
from agent_pull_requests.watch_storage import StateCommitUncertain

SCOPE = ConsumerScope("consumer-a", "account-a", "example/project")


@pytest.fixture
def store(tmp_path):
    result = ReplayStore(tmp_path.resolve(), "fleet/service/installation")
    result.provision()
    return result


def payload(**kwargs):
    return dict(request_id="request-a", issued_at=time.time(), number=1,
                until=("merged",), **kwargs)


def execute(store, operation, value=None, scope=SCOPE):
    return store.execute(scope, operation, value or {}, expires_at=int(time.time()) + 60)


@pytest.mark.parametrize("value", [None, True, "", "x" * 257, "line\nbreak"])
@pytest.mark.parametrize("field", ["fleet", "gateway", "slot"])
def test_service_identity_requires_bounded_nonempty_text(field, value):
    identity = dict(fleet="fleet-a", gateway="gateway-a", slot="service-a")
    identity[field] = value
    with pytest.raises(ValueError, match="service identity"):
        ReplayServiceIdentity(**identity)


def test_service_identity_document_does_not_mutate_the_frozen_binding():
    identity = ReplayServiceIdentity("fleet-a", "gateway-a", "service-a")
    identity.document()["slot"] = "other"
    assert identity.document() == {
        "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
    }
    with pytest.raises(ValueError, match="typed replay service"):
        ReplayStore(None, "namespace", service_identity=identity.document())


def test_first_provision_is_explicit_and_never_replaces_existing_state(store):
    before = store.path.read_bytes()
    with pytest.raises(FileExistsError):
        store.provision()
    assert store.path.read_bytes() == before
    store.path.unlink()
    with pytest.raises(FileNotFoundError):
        execute(store, "pending")
    assert not store.path.exists()


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"not-json", b'{"schema":1,"schema":2}'])
def test_corrupt_state_does_not_become_an_empty_registry(store, raw):
    store.path.write_bytes(raw)
    with pytest.raises(ReplayStoreInvalid):
        execute(store, "pending")
    assert store.path.read_bytes() == raw
    with pytest.raises(FileExistsError):
        store.provision()


def test_namespace_is_bound_on_every_transaction(store):
    other = ReplayStore(store.root, "foreign")
    with pytest.raises(ReplayStoreInvalid):
        execute(other, "pending")
    assert execute(store, "pending") == {"events": [], "more": False}


@pytest.mark.parametrize("field", ["fleet", "gateway", "slot"])
def test_service_identity_is_bound_on_every_transaction_and_foreign_bytes_are_preserved(
    tmp_path, field,
):
    identity = ReplayServiceIdentity("fleet-a", "gateway-a", "service-a")
    store = ReplayStore(tmp_path.resolve(), "installation-a", service_identity=identity)
    store.provision()
    original = payload()
    receipt = execute(store, "register", original)
    before = store.path.read_bytes()
    other = ReplayStore(
        store.root, store.namespace, service_identity=replace(identity, **{field: "other"}),
    )
    with pytest.raises(ReplayStoreInvalid):
        execute(other, "pending")
    with pytest.raises(ReplayStoreInvalid):
        execute(other, "register", payload())
    with pytest.raises(FileExistsError):
        other.provision()
    assert store.path.read_bytes() == before
    assert execute(store, "inspect", {
        key: original[key] for key in ("request_id", "issued_at")
    }) == receipt


@pytest.mark.parametrize("bound", [False, True])
def test_bound_and_unbound_stores_never_implicitly_adopt_each_other(tmp_path, bound):
    identity = ReplayServiceIdentity("fleet-a", "gateway-a", "service-a")
    store = ReplayStore(
        tmp_path.resolve(), "installation-a", service_identity=identity if bound else None,
    )
    store.provision()
    original = payload(deadline_at=time.time() - 1)
    execute(store, "register", original)
    before = store.path.read_bytes()
    other = ReplayStore(
        store.root, store.namespace, service_identity=None if bound else identity,
    )
    with pytest.raises(ReplayStoreInvalid):
        execute(other, "pending")
    assert store.path.read_bytes() == before
    assert execute(store, "pending")["events"][0]["timed_out"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(schema="unknown"),
    lambda value: value.update(extra=True),
    lambda value: value.pop("service"),
    lambda value: value["service"].update(extra=True),
    lambda value: value.update(replay=None),
    lambda value: value.update(replay=[]),
    lambda value: value.update(replay="invalid"),
    lambda value: value.update(replay=1),
    lambda value: value["replay"].update(namespace="foreign"),
    lambda value: value["replay"].update(records=[{}]),
])
def test_bound_store_refuses_invalid_outer_and_inner_envelopes(tmp_path, change):
    store = ReplayStore(
        tmp_path.resolve(), "installation-a",
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"),
    )
    store.provision()
    value = json.loads(store.path.read_bytes())
    change(value)
    store.path.write_text(json.dumps(value))
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        execute(store, "pending")
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_bound_store_restart_and_uncertain_commit_reconcile_original_identity(
    tmp_path, monkeypatch, uncertain,
):
    identity = ReplayServiceIdentity("fleet-a", "gateway-a", "service-a")
    store = ReplayStore(tmp_path.resolve(), "installation-a", service_identity=identity)
    store.provision()
    original = payload(deadline_at=time.time() - 1)
    write = storage._atomic_write_json

    def fail(path, state):
        if uncertain:
            write(path, state)
            raise StateCommitUncertain("synthetic")
        raise OSError("synthetic")

    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    if uncertain:
        assert execute(store, "register", original)["status"] == "uncertain"
    else:
        with pytest.raises(OSError):
            execute(store, "register", original)
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    restarted = ReplayStore(store.root, store.namespace, service_identity=identity)
    receipt = execute(restarted, "register", original)
    assert execute(store, "register", original) == receipt
    event = execute(store, "pending")["events"][0]
    assert execute(restarted, "pending")["events"][0] == event
    completion = {
        **{key: original[key] for key in ("request_id", "issued_at")},
        "registration_id": receipt["registration_id"], "event_id": event["event_id"],
    }
    ack = execute(restarted, "acknowledge", completion)
    assert ack["status"] == "acknowledged"
    assert execute(store, "acknowledge", completion) == ack
    persisted = json.loads(store.path.read_bytes())
    assert persisted["service"] == identity.document()
    assert len(persisted["replay"]["records"]) == 1
    assert execute(restarted, "pending") == {"events": [], "more": False}


def test_separate_instances_reload_exact_receipt_and_pending_timeout(store):
    original = payload(deadline_at=time.time() - 1)
    receipt = execute(store, "register", original)
    other = ReplayStore(store.root, store.namespace)
    assert execute(other, "register", original) == receipt
    event = execute(other, "pending")["events"][0]
    assert event["timed_out"] is True
    completion = {key: original[key] for key in ("request_id", "issued_at")}
    completion.update(registration_id=receipt["registration_id"], event_id=event["event_id"])
    ack = execute(store, "acknowledge", completion)
    assert ack["status"] == "acknowledged"
    assert execute(other, "acknowledge", completion) == ack
    assert execute(store, "pending") == {"events": [], "more": False}


@pytest.mark.parametrize("uncertain", [False, True])
def test_pre_and_post_replace_failures_require_original_identity(store, monkeypatch, uncertain):
    original = payload()
    before = store.path.read_bytes()
    write = storage._atomic_write_json

    def fail(path, state):
        if uncertain:
            write(path, state)
            raise StateCommitUncertain("synthetic")
        raise OSError("synthetic")

    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    if uncertain:
        assert execute(store, "register", original) == {
            "status": "uncertain", "reason": "state_commit_uncertain",
        }
    else:
        with pytest.raises(OSError):
            execute(store, "register", original)
        assert store.path.read_bytes() == before
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    retry = execute(store, "register", original)
    assert retry["status"] == "active"
    assert len(json.loads(store.path.read_bytes())["records"]) == 1


def test_same_process_and_real_child_cannot_write_during_another_transaction(store):
    code = """
import json, sys, time
from pathlib import Path
from single_instance_lease import OperationLockedError
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore
store = ReplayStore(Path(sys.argv[1]), sys.argv[2])
try:
    value = store.execute(
        ConsumerScope('consumer-a', 'account-a', 'example/project'), 'register',
        dict(request_id='child', issued_at=float(sys.argv[3]), number=1, until=('merged',)),
        expires_at=int(time.time()) + 60)
except OperationLockedError:
    value = {'status': 'busy'}
print(json.dumps(value))
"""
    issued = str(time.time())

    def child():
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.root), store.namespace, issued],
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with OperationLock(store.root / "watch-replay.lock"):
        with pytest.raises(OperationLockedError):
            execute(store, "register", payload())
        assert child() == {"status": "busy"}
        assert json.loads(store.path.read_bytes())["records"] == []
    accepted = child()
    assert accepted["status"] == "active"
    assert child() == accepted
    assert len(json.loads(store.path.read_bytes())["records"]) == 1


def test_expiry_is_checked_again_after_loading_before_commit(store, monkeypatch):
    now = [100]
    store.clock = lambda: now[0]
    before = store.path.read_bytes()
    original = storage.json_object

    def expire(raw):
        now[0] = 102
        return original(raw)

    monkeypatch.setattr(storage, "json_object", expire)
    with pytest.raises(ReplayCallerExpired):
        store.execute(SCOPE, "register", payload(), expires_at=101)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("stage", ["lookup", "commit", "result"])
def test_request_disconnect_checks_real_lookup_commit_and_result_boundary(store, monkeypatch, stage):
    from fleet_manager.gateway_service_handler import RequestDisconnected, RequestLifetime

    lifetime = RequestLifetime()
    before = store.path.read_bytes()
    original = payload()
    decode, write = storage.json_object, storage._atomic_write_json

    def closed_lookup(raw):
        lifetime.close()
        return decode(raw)

    def closed_commit(core, *args, **kwargs):
        lifetime.close()
        return register(core, *args, **kwargs)

    def closed_result(path, state):
        write(path, state)
        lifetime.close()

    register = storage.WatchReplay.register
    if stage == "lookup":
        monkeypatch.setattr(storage, "json_object", closed_lookup)
    elif stage == "commit":
        monkeypatch.setattr(storage.WatchReplay, "register", closed_commit)
    else:
        monkeypatch.setattr(storage, "_atomic_write_json", closed_result)
    with pytest.raises(RequestDisconnected):
        store.execute(SCOPE, "register", original, expires_at=int(time.time()) + 60,
                      checkpoint=lifetime.check)
    if stage == "result":
        monkeypatch.setattr(storage, "_atomic_write_json", write)
        assert execute(store, "inspect", {
            key: original[key] for key in ("request_id", "issued_at")
        })["status"] == "active"
    else:
        assert store.path.read_bytes() == before


@pytest.mark.parametrize("raw", [
    b'{"one":1,"one":2}', b'{"nested":{"one":1,"one":2}}',
    b'{"value":NaN}', b'{"value":Infinity}', b"[]", b"null",
])
def test_json_object_rejects_ambiguous_or_nonstandard_input(raw):
    with pytest.raises(ValueError):
        json_object(raw)


def test_ordinary_cli_does_not_import_optional_fleet_stack():
    code = """
import importlib.abc, runpy, sys
class NoFleet(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fleet_manager' or fullname.startswith('fleet_manager.'):
            raise AssertionError('ordinary CLI imported optional fleet stack')
sys.meta_path.insert(0, NoFleet())
sys.argv = ['agent-pull-requests', '--help']
runpy.run_module('agent_pull_requests', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert "status" in result.stdout and "watch" in result.stdout
