"""The actual installer drains admitted cross-process work before target adoption."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs
from test_cell_upgrade import _pending
from test_watch_control import _CHILD, _receiver
from test_watch_controller_crash import _target
from test_watch_driver import _begin, _driver, _port
from test_watch_invocation import installed as installed
from work_coalescing_singleton.client import request
from zdd import publication_history, routing
from zdd.process_exit import open_process

from agent_pull_requests.watch_contract import MERGED
from agent_pull_requests.watch_notification import ACKNOWLEDGED_NOTIFICATIONS
from agent_pull_requests.watch_storage import read_subscriptions_state

_BACKEND = """
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_notification import default_notify, event_payload
from agent_pull_requests.watch_registry import WatchRegistry
from uuid import uuid4
import os, time
gates = Path(GATES)
def wait_gate(name):
    deadline = time.monotonic() + 25
    while not (gates / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test-owned work gate expired")
        time.sleep(0.01)
def fetch(repo, number):
    return PRSnapshot(
        merged=(gates / f"fire-{number}").exists(),
        pr_state="closed" if (gates / f"fire-{number}").exists() else "open",
    )
def notify(event):
    identity = event.subscriber.registration_id
    lock = gates / ("inflight-" + identity)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        (gates / "overlapping-generations").touch()
        raise
    try:
        (gates / f"delivery-{uuid4()}.json").write_text(json.dumps({
            "pid": os.getpid(), "payload": event_payload(event),
        }), encoding="utf-8")
        return default_notify(event)
    finally:
        os.close(descriptor)
        lock.unlink()
owner = WatchControlOwner(
    invocation, fetch=fetch, notify=notify, poll_interval=0.02,
    instance_id=sys.argv[6],
)
dispatch = owner.writer.daemon._compute_request
def admitted(kind, payload):
    name = payload.get("subscriber_id")
    if (gates / "arm-handlers").exists() and (
        kind == "register" and name == "accepted-registration"
        or kind == "unregister" and name == "cancel-me"
    ):
        (gates / f"{kind}-entered").touch()
        wait_gate("release-handlers")
    return dispatch(kind, payload)
owner.writer.daemon._compute_request = admitted
apply = WatchRegistry.apply_snapshot
def polled(self, key, snapshot):
    events = apply(self, key, snapshot)
    if key.number == 3 and events:
        (gates / "poll-event.json").write_text(
            json.dumps(event_payload(events[0])), encoding="utf-8",
        )
        (gates / "poll-entered").touch()
        wait_gate("release-poll")
    return events
WatchRegistry.apply_snapshot = polled
reload = owner.writer.daemon.reload_state
def loaded(timeout):
    reload(timeout=timeout)
    deliveries = owner.writer.daemon._deliveries
    deliveries.max_workers = 1
    legacy = deliveries.legacy
    def queued(event):
        legacy(event)
        (gates / ("queued-" + event.subscriber.subscriber_id)).touch()
    deliveries.legacy = queued
owner.writer.daemon.reload_state = loaded
"""

_CALLBACK = """
import json, sys, time
from pathlib import Path
from uuid import uuid4
gates = Path(sys.argv[1])
payload = json.load(sys.stdin)
if payload["subscriber_id"] == "slow-ack":
    (gates / "callback-entered").touch()
    deadline = time.monotonic() + 25
    while not (gates / "release-callback").exists():
        if time.monotonic() >= deadline:
            raise SystemExit(7)
        time.sleep(0.01)
(gates / f"ack-{uuid4()}.json").write_text(json.dumps(payload), encoding="utf-8")
"""

_INSTALLER = """
import json, sys
from pathlib import Path
from agent_pull_requests import watch_install
from agent_pull_requests.watch_driver import WatchDriver
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import WatchInvocation, capture_running
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
invocation = WatchInvocation(
    installation, capture_running(installation, environment={}), str(installation.receipt),
)
def spawn(port, instance_id):
    print(json.dumps({"port": port, "instanceId": instance_id}), flush=True)
    if sys.stdin.readline().strip() != "spawned":
        raise RuntimeError("test-owned passive child was not acknowledged")
def run_target(journal, target, *, poll_interval, timeout):
    return WatchDriver(
        journal, spawn=spawn, pick_free_port=lambda: int(sys.argv[6]), timeout=timeout,
    ).run()
watch_install.run_target = run_target
print("ready", flush=True)
if sys.stdin.readline().strip() != "start":
    raise RuntimeError("test-owned installer was not started")
print(json.dumps(watch_install.activate(invocation, timeout=120)), flush=True)
"""


def _wait(predicate, *, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("test-owned concurrent work did not reach its boundary")
        time.sleep(0.02)


def _call(endpoint, kind, payload=None):
    return request(
        *endpoint, kind=kind, key=uuid4().hex, payload=payload or {},
        request_deadline_s=30, completion_timeout_s=30,
    )


def _registration(name, number, callback, python, gates, *, acknowledged=True):
    value = {
        "repo": "example/project", "number": number, "subscriber_id": name,
        "until": [MERGED], "timeout": 3600,
        "notify": {"argv": [str(python), "-I", str(callback), str(gates)]},
    }
    if acknowledged:
        value["notification_protocol"] = ACKNOWLEDGED_NOTIFICATIONS
    return value


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(300)
def test_actual_installer_upgrade_drains_handlers_poll_ack_and_queued_legacy(
    installed, tmp_path, monkeypatch,
):
    installation, target, python, _ = target_slot = _target(installed)
    gates = tmp_path / "gates"
    gates.mkdir()
    callback = tmp_path / "callback.py"
    callback.write_text(_CALLBACK, encoding="utf-8")
    saved = _pending(installation.state_root, installed[2])
    script = _CHILD.replace(
        """owner = WatchControlOwner(
    invocation, fetch=lambda *_: (_ for _ in ()).throw(RuntimeError("no fixture fetch")),
    instance_id=sys.argv[6],
)""",
        _BACKEND.replace("GATES", repr(str(gates))),
    )
    assert script != _CHILD
    with ExitStack() as children, monkeypatch.context() as patch:
        patch.setattr("test_watch_control._CHILD", script)
        with installation.transaction(timeout=1, environment={}) as guard:
            initial = _begin(installed, guard)
            bootstrap = _driver(installed, initial, children)
            assert bootstrap.run().ok
        old = bootstrap.successor
        before_route = routing.read_table(installation.run_root, strict=True)
        payloads = {
            name: _registration(name, number, callback, installed[2], gates,
                                acknowledged=not name.startswith("legacy-"))
            for name, number in (
                ("cancel-me", 4), ("unrelated", 5), ("slow-ack", 1), ("polled", 3),
                ("legacy-one", 2), ("legacy-two", 2), ("legacy-three", 2),
                ("accepted-registration", 6),
            )
        }
        registrations = {}
        for name, payload in payloads.items():
            if name != "accepted-registration":
                registrations[name] = _call(old._endpoint, "register", payload)
                assert registrations[name]["registered"]
        _wait(lambda: all(
            entry["baseline"] is not None
            for entry in read_subscriptions_state(installation.state_root)
            if entry["subscriber_id"] != saved["subscriber_id"]
        ))
        retained = {
            entry["subscriber_id"]: entry
            for entry in read_subscriptions_state(installation.state_root)
        }
        controller = subprocess.Popen(  # noqa: S603 -- owned completed-slot installer
            [str(python), "-I", "-c", _INSTALLER, *(
                str(getattr(installation, key)) for key in (
                    "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
                )
            ), str(_port())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **no_window_kwargs(),
        )
        try:
            assert controller.stdout.readline().strip() == "ready", (
                controller.communicate(timeout=5)
            )
            with (
                open_process(old.instance.pid, old.instance.start_time) as original,
                ThreadPoolExecutor(max_workers=2) as callers,
            ):
                controller.stdin.write("start\n")
                controller.stdin.flush()
                line = controller.stdout.readline()
                assert line, controller.communicate(timeout=5)
                launch = json.loads(line)
                child, instance, endpoint = children.enter_context(_receiver(
                    target_slot, instance_id=launch["instanceId"],
                    bind_port=launch["port"], publish_launch=True,
                ))
                with open_process(instance.pid, instance.start_time) as successor_exit:
                    (gates / "arm-handlers").touch()
                    registered = callers.submit(
                        _call, old._endpoint, "register", payloads["accepted-registration"],
                    )
                    cancelled = callers.submit(
                        _call, old._endpoint, "unregister", {
                            **payloads["cancel-me"],
                            "registration_id": registrations["cancel-me"]["registration_id"],
                        },
                    )
                    _wait(lambda: all((gates / f"{kind}-entered").exists()
                                      for kind in ("register", "unregister")))
                    (gates / "fire-1").touch()
                    _wait(lambda: (gates / "callback-entered").exists())
                    (gates / "fire-2").touch()
                    _wait(lambda: all((gates / f"queued-legacy-{name}").exists()
                                      for name in ("one", "two", "three")))
                    (gates / "fire-3").touch()
                    _wait(lambda: (gates / "poll-entered").exists())
                    controller.stdin.write("spawned\n")
                    controller.stdin.flush()
                    _wait(lambda: not _call(old._endpoint, "health")["effectsEnabled"])
                    prior = _call(old._endpoint, "health")
                    passive = _call(endpoint, "health")
                    assert prior["writerHeld"] and not original.wait(0)
                    assert not passive["writerHeld"] and not passive["readReady"]
                    assert not passive["effectsEnabled"] and not successor_exit.wait(0)
                    assert not registered.done() and not cancelled.done()
                    refused = _call(old._endpoint, "register", {
                        **payloads["accepted-registration"], "subscriber_id": "late",
                    })
                    assert refused["retryable"] and "error" in refused
                    assert routing.read_table(installation.run_root, strict=True) == before_route
                    (gates / "release-poll").touch()
                    (gates / "release-handlers").touch()
                    (gates / "release-callback").touch()
                    accepted = registered.result(timeout=30)
                    assert accepted["registered"]
                    assert cancelled.result(timeout=30)["unregistered"]
                    assert controller.wait(timeout=120) == 0, controller.communicate(timeout=5)
                    result = json.loads(controller.stdout.readline())
                    assert result == {"status": "ready", "activated": True, "ownerStarted": True}
                    assert original.wait(0) and not successor_exit.wait(0)
                    assert child.poll() is None
                    _wait(lambda: len(list(gates.glob("ack-*.json"))) == 5)
                    _wait(lambda: {entry["subscriber_id"] for entry in read_subscriptions_state(
                        installation.state_root,
                    )} == {saved["subscriber_id"], "unrelated", "accepted-registration"})
                    state = {
                        entry["subscriber_id"]: entry
                        for entry in read_subscriptions_state(installation.state_root)
                    }
                    assert state[saved["subscriber_id"]] == saved
                    unrelated, prior_unrelated = state["unrelated"], retained["unrelated"]
                    assert {
                        key: value for key, value in unrelated.items()
                        if key not in {"deadline_at", "remaining_timeout"}
                    } == {
                        key: value for key, value in prior_unrelated.items()
                        if key not in {"deadline_at", "remaining_timeout"}
                    }
                    assert unrelated["deadline_at"] == prior_unrelated["deadline_at"]
                    assert 0 < unrelated["remaining_timeout"] < (
                        prior_unrelated["remaining_timeout"]
                    )
                    assert state["accepted-registration"]["registration_id"] == (
                        accepted["registration_id"]
                    )
                    assert _call(endpoint, "health")["effectsEnabled"]
                    assert not (gates / "overlapping-generations").exists()
                    assert not list(gates.glob("inflight-*"))
                    deliveries = [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in gates.glob("delivery-*.json")
                    ]
                    assert len(deliveries) == 5
                    assert {item["payload"]["subscriber_id"] for item in deliveries} == {
                        "slow-ack", "polled", "legacy-one", "legacy-two", "legacy-three",
                    }
                    for item in deliveries:
                        assert item["pid"] == (
                            instance.pid if item["payload"]["subscriber_id"] == "polled"
                            else old.instance.pid
                        )
                    polled = next(item["payload"] for item in deliveries
                                  if item["payload"]["subscriber_id"] == "polled")
                    assert polled == json.loads((gates / "poll-event.json").read_text("utf-8"))
                    document = json.loads((
                        installation.run_root / "watch-owner-transaction.json"
                    ).read_text("utf-8"))
                    assert document["phase"] == "complete"
                    assert document["exited"] == ["predecessor"]
                    assert document["successor"] == instance.evidence()
                    after_route = routing.read_table(installation.run_root, strict=True)
                    assert after_route["active"]["pid"] == instance.pid
                    assert after_route["active"]["version"] == target.runtime_version
                    assert all(entry in after_route[publication_history.KEY]["entries"]
                               for entry in before_route[publication_history.KEY]["entries"])
        finally:
            for name in ("release-poll", "release-handlers", "release-callback"):
                (gates / name).touch()
            if controller.poll() is None:
                controller.stdin.close()
                controller.wait(timeout=130)
            for stream in (controller.stdin, controller.stdout, controller.stderr):
                if stream is not None:
                    stream.close()
