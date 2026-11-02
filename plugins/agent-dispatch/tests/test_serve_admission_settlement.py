"""Real admission must outlive retiring federation threads and assignment intake."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
import uvicorn

from agent_dispatch import federation_runner, procutil, runtime_version, server
from agent_dispatch.assignment_intake import AssignmentIntake
from agent_dispatch.config import Config
from agent_dispatch.federation_runner import FederationRunner
from agent_procutil.daemon_instances import (
    INSTANCE_ENV,
    daemon_test_lifecycle,
    inspect_instances,
)


class PrivateClient:
    def __init__(self):
        self.closed = False

    async def async_stream_events(self):
        yield {"type": "ready"}
        await asyncio.Event().wait()

    def close(self):
        self.closed = True


@pytest.mark.parametrize("work", ["tick", "intake"])
@pytest.mark.parametrize(
    "outcome", [
        "normal", "startup", "server-error", "cancel", "interrupt", "stop-error", "lock-error",
    ],
)
def test_host_admission_outlives_retained_work(monkeypatch, tmp_path, work, outcome):
    release = threading.Event()
    entered = threading.Event()
    pending = threading.Event()
    observed = []
    failures = []
    resignations = []

    def deregister(instance):
        resignations.append(instance)
        if outcome == "stop-error":
            raise TimeoutError("directory retirement timed out")

    runner = FederationRunner(
        SimpleNamespace(deregister=deregister), "private-test", role="peer",
    )
    client = PrivateClient()
    runner._work_intake_client = client
    runner._work_intake = SimpleNamespace()

    def block():
        entered.set()
        assert release.wait(15), "test retirement was not released"
        return {}

    if work == "tick":
        owned = threading.Thread(target=block, daemon=True)
        runner._thread = owned
        owned.start()
        threads = [owned]
    else:
        intake = AssignmentIntake(
            client, runner._work_intake, machine="private-test",
            enabled=lambda: True, reconcile=block,
        )
        runner._assignment_intake = intake
        intake.start()
        threads = [intake._reader, intake._worker]
    assert entered.wait(5)

    calls = []
    real_stop = runner.stop

    def bounded_stop(**kwargs):
        settled = real_stop(timeout=0.02, **kwargs)
        calls.append(settled)
        if len(calls) >= 3 and not settled:
            pending.set()
        if outcome in ("cancel", "interrupt") and len(calls) <= 2:
            if len(calls) == 1:
                raise KeyboardInterrupt("cleanup interrupted")
            raise asyncio.CancelledError("cleanup cancelled again")
        return settled

    monkeypatch.setattr(runner, "stop", bounded_stop)

    def start(**kwargs):
        if outcome == "startup":
            raise RuntimeError("partial federation startup")

    monkeypatch.setattr(runner, "start", start)
    monkeypatch.setattr(federation_runner, "runner_from_config", lambda **kwargs: runner)
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "peer")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "private-test")
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    monkeypatch.setattr(procutil, "relocate_off_payload", lambda: None)
    monkeypatch.setattr(runtime_version, "write_running_version", lambda: None)

    async def app(scope, receive, send):
        raise AssertionError("no application traffic is part of this test")

    app.state = SimpleNamespace(drain_gate=None)
    monkeypatch.setattr(server, "build_app", lambda cfg: app)

    def run(_server, **kwargs):
        if outcome == "server-error":
            raise RuntimeError("server startup failed")
        if outcome == "cancel":
            raise asyncio.CancelledError("original server cancellation")
        if outcome == "startup":
            assert client.closed

    monkeypatch.setattr(uvicorn.Server, "run", run)
    if outcome == "lock-error":
        def failed_release():
            raise OSError("startup lock release failed")

        def failed_server_config(*args, **kwargs):
            raise RuntimeError("partial server construction")

        monkeypatch.setattr(
            server, "SingleInstance",
            lambda path: SimpleNamespace(acquire=lambda: True, release=failed_release),
        )
        monkeypatch.setattr(server, "has_live_local_coordinator", lambda **kwargs: False)
        monkeypatch.setattr(uvicorn, "Config", failed_server_config)
    registry = tmp_path / "registry"

    def observe():
        try:
            assert pending.wait(10), "hosting never attempted bounded retirement"
            records = inspect_instances(registry)
            assert len(records) == 1
            assert records[0]["state"] == "running"
            assert records[0]["purpose"] == "unattended-test"
            assert not client.closed
            assert runner._work_intake is not None
            assert any(thread.is_alive() for thread in threads)
            observed.append(records[0]["instance_id"])
        except BaseException as exc:
            failures.append(exc)
        finally:
            release.set()

    observer = threading.Thread(target=observe, daemon=True)
    try:
        with daemon_test_lifecycle(owner="dispatch-host-retirement", registry_root=registry) as env:
            for name, value in env.items():
                monkeypatch.setenv(name, value)
            monkeypatch.delenv(INSTANCE_ENV, raising=False)
            observer.start()
            if outcome == "server-error":
                with pytest.raises(RuntimeError, match="server startup failed"):
                    server.serve(Config(host="127.0.0.1"), passive=True)
            elif outcome == "cancel":
                with pytest.raises(asyncio.CancelledError, match="original server cancellation"):
                    server.serve(Config(host="127.0.0.1"), passive=True)
            elif outcome == "interrupt":
                with pytest.raises(KeyboardInterrupt, match="cleanup interrupted"):
                    server.serve(Config(host="127.0.0.1"), passive=True)
            elif outcome == "stop-error":
                with pytest.raises(TimeoutError, match="directory retirement timed out"):
                    server.serve(Config(host="127.0.0.1"), passive=True)
            elif outcome == "lock-error":
                with pytest.raises(OSError, match="startup lock release failed"):
                    server.serve(Config(host="127.0.0.1"), passive=False)
            else:
                server.serve(Config(host="127.0.0.1"), passive=True)
            observer.join(5)
            assert not observer.is_alive()
            if failures:
                raise failures[0]
            assert len(observed) == 1
            assert inspect_instances(registry)[0]["state"] == "finished"
            assert calls[-1] is True
            assert client.closed
            assert runner._assignment_intake is None
            assert runner._work_intake is None
            assert len(resignations) <= 1
    finally:
        release.set()
        real_stop(resign=False, timeout=5)
        if observer.ident is not None:
            observer.join(5)
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()


def test_stop_settles_unstarted_thread_after_partial_start():
    runner = FederationRunner(SimpleNamespace(), "private-test", role="peer")
    runner._thread = threading.Thread(target=lambda: None)
    assert runner.stop(resign=False, timeout=0.01) is True
    assert runner._thread is None
