"""Authenticated loopback control preserves transaction and lifecycle fences."""

from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs
from work_coalescing_singleton.client import DaemonUnavailable, request

from agent_pull_requests.watch_control import (
    WatchControlClient, WatchControlRefused, WatchControlServer,
)
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_runtime import WatchInstance
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture
from test_watch_journal import _manifest, _route, _select
from test_watch_runtime import _complete, _instance

pytestmark = pytest.mark.timeout(90)

_CHILD = """
import json, sys
from pathlib import Path
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import WatchInvocation, capture_running
from agent_pull_requests.watch_control import WatchControlOwner, WatchControlServer
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
invocation = WatchInvocation(
    installation, capture_running(installation, environment={}), str(installation.receipt),
)
owner = WatchControlOwner(
    invocation, fetch=lambda *_: (_ for _ in ()).throw(RuntimeError("no fixture fetch")),
    instance_id=sys.argv[6],
)
if sys.argv[9] == "active":
    authorize = owner.writer.authorize
    owner.writer.authorize = lambda operation: None
    try:
        owner.writer.adopt(timeout=10)
        owner.writer.activate(timeout=10)
    finally:
        owner.writer.authorize = authorize
server = WatchControlServer(owner.compute, on_idle=None, bind_port=int(sys.argv[8]))
server.start()
if sys.argv[7] == "publish":
    owner.publish_launch(server)
rv = server.rendezvous()
print(json.dumps({"instance": owner.instance.evidence(), "endpoint": rv}), flush=True)
def fixture_cleanup():
    sys.stdin.readline()
    owner.writer.close(timeout=10)
    owner._terminal.set()
import threading
cleanup = threading.Thread(target=fixture_cleanup, daemon=True)
cleanup.start()
owner.wait_for_shutdown()
server.close()
"""


@contextmanager
def _receiver(installed, *, instance_id=None, publish_launch=False, bind_port=0, active=False):
    installation, runtime, python, _ = installed
    child = subprocess.Popen(
        [str(python), "-I", "-c", _CHILD, *(
            str(getattr(installation, key)) for key in (
                "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
            )
        ), str(uuid4()) if instance_id is None else instance_id,
         "publish" if publish_launch else "ordinary", str(bind_port),
         "active" if active else "passive"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline()
        assert line, child.stderr.read() if child.stderr is not None else "child failed"
        hello = json.loads(line)
        instance = WatchInstance.from_evidence(hello["instance"], installation, environment={})
        assert instance.runtime == runtime
        host, port = hello["endpoint"]["endpoint"].split(":")
        endpoint = host, int(port), hello["endpoint"]["token"]
        yield child, instance, endpoint
    finally:
        if child.poll() is None:
            assert child.stdin is not None
            child.stdin.write("fixture-owned cleanup\n")
            child.stdin.flush()
            child.wait(timeout=15)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


@pytest.fixture
def receiver(installed):
    with _receiver(installed) as value:
        yield value


def _client(installation, receiver, journal):
    return WatchControlClient(
        endpoint=receiver[2], instance=receiver[1], installation=lambda: installation,
        transaction=lambda: (journal.transaction_id, journal.authorization),
        request_timeout=60,
    )


def _begin(installed, receiver, guard):
    installation, target, _, _ = installed
    old = _complete(installation, version="1.0.0")
    journal = WatchJournal.begin(
        installation, guard, target=target, target_manifest=_manifest(target),
        predecessor=_instance(old), environment={},
    )
    journal.bind_successor(receiver[1], route=_route(receiver[1]))
    return journal


@pytest.mark.timeout(180)
def test_real_tcp_successor_adopts_without_effects_then_requires_durable_commit(installed, receiver):
    installation, target, _, _ = installed
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        health = client.health()
        assert not health["writerHeld"] and not health["effectsEnabled"]
        with pytest.raises(WatchControlRefused):
            client.control("adopt")
        journal.advance("draining")
        journal.advance("released")
        client.control("adopt")
        health = client.health()
        assert health["writerHeld"] and health["readReady"] and not health["effectsEnabled"]
        with pytest.raises(WatchControlRefused):
            client.adopt_relay()
        journal.advance("read-ready")
        journal.advance("publishing")
        _select(installation, target, route=_route(receiver[1]))
        journal.commit(read_health=client.health)
        assert client.adopt_relay() == {"adopted": True}
        assert client.health()["effectsEnabled"] is True
        assert receiver[0].poll() is None


def test_real_tcp_wrong_capability_and_shutdown_cannot_transfer_or_retire(installed, receiver):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        client._transaction = lambda: (journal.transaction_id, "0" * 64)
        journal.advance("draining")
        journal.advance("released")
        with pytest.raises(WatchControlRefused):
            client.control("adopt")
        with pytest.raises(WatchControlRefused):
            client.drain(timeout=30, poll=0.1, force=True)
        host, port, token = receiver[2]
        refused = request(
            host, port, token, kind="shutdown", key="same", payload={},
            request_deadline_s=30,
        )
        assert refused["error"] == "watch-owner-control-refused"
        assert client.health()["writerHeld"] is False
        with pytest.raises(DaemonUnavailable):
            request(
                host, port, "wrong-token", kind="health", key="same", payload={},
                request_deadline_s=5,
            )
        assert receiver[0].poll() is None


def test_real_tcp_rollback_retirement_is_graceful_and_native_exit_is_separate(installed, receiver):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        reference = receiver[1].capture_exit(
            installation, read_health=client.health, environment={},
        )
        try:
            journal.rollback()
            assert client.shutdown() == {"shutting_down": True}
            journal.record_exit("successor", reference, timeout=10)
            receiver[0].wait(timeout=10)
            assert reference.wait(0)
            # This receiver fixture deliberately has no serving predecessor.
            # Its native exit proves retirement, not successful rollback.
            with pytest.raises(PermissionError, match="active-owner health"):
                journal.finish()
        finally:
            reference.close()


@pytest.mark.parametrize("changed", ["transactionId", "authorization", "operation", "nonce", "instance"])
def test_caller_key_cannot_coalesce_different_control_authority(changed):
    started = threading.Event()
    finish = threading.Event()
    seen = []
    first = {
        "transactionId": "one", "authorization": "one",
        "operation": "adopt", "nonce": "one", "instance": {"pid": 1},
    }
    second = {**first, changed: "different"}

    def compute(kind, payload):
        seen.append(payload)
        if payload == first:
            started.set()
            assert finish.wait(timeout=5)
            return {"first": True}
        return {"distinct": True}

    server = WatchControlServer(compute, on_idle=None)
    server.start()
    try:
        rv = server.rendezvous()
        host, port = rv["endpoint"].split(":")

        def send(payload):
            return request(
                host, int(port), rv["token"], kind="owner-control", key="same-client-key",
                payload=payload, request_deadline_s=10,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(send, first)
            assert started.wait(timeout=5)
            try:
                assert send(second) == {"distinct": True}
            finally:
                finish.set()
            assert pending.result(timeout=5) == {"first": True}
        assert len(seen) == 2
    finally:
        finish.set()
        server.close()


def test_client_refuses_foreign_instance_acknowledgement(monkeypatch, installed, receiver):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        original = client._request

        def wrong_ack(kind, payload, timeout):
            if kind == "health":
                return original(kind, payload, timeout)
            return {
                "accepted": True, "operation": payload["operation"], "nonce": payload["nonce"],
                "instance": replace(receiver[1], start_time="0").evidence(),
            }

        monkeypatch.setattr(client, "_request", wrong_ack)
        with pytest.raises(WatchControlRefused, match="ambiguous"):
            client.control("adopt")


@pytest.mark.parametrize("timeout", [float("nan"), float("inf")])
def test_nonfinite_control_payload_has_explicit_refusal_before_coalescing(timeout):
    server = WatchControlServer(lambda *_: pytest.fail("invalid payload must not run"))
    server.start()
    try:
        rv = server.rendezvous()
        host, port = rv["endpoint"].split(":")
        result = request(
            host, int(port), rv["token"], kind="owner-control", key="ignored",
            payload={"timeout": timeout}, request_deadline_s=5,
        )
        assert result == {"error": "watch-owner-control-refused", "retryable": False}
    finally:
        server.close()
