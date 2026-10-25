"""Real lane admission must outlive a blocked Bridge stream-open worker."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from agent_dispatch import bridge_event_worker, bridge_events
from agent_dispatch.bridge_events import BridgeSubscription, LocalBridgeEventClient, SupervisorEventWake
from agent_dispatch.supervisor import Supervisor
from agent_procutil.daemon_instances import INSTANCE_ENV, daemon_test_lifecycle, inspect_instances


def test_concurrent_worker_retirement_disposes_its_witness_once():
    worker = bridge_event_worker.BridgeEventWorker()
    entered, second, release = threading.Event(), threading.Event(), threading.Event()
    checks, releases, results = [], [], []

    def check():
        checks.append(True)
        entered.set()
        if len(checks) == 2:
            second.set()
        assert release.wait(3)
        return True

    worker._witness = bridge_event_worker._Witness(check, lambda: releases.append(True))
    worker._ready.set()
    threads = [threading.Thread(target=lambda: results.append(worker.retired())) for _ in range(2)]
    try:
        threads[0].start()
        assert entered.wait(2)
        threads[1].start()
        second.wait(0.2)
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(3)
                assert not thread.is_alive()
    assert results == [True, True]
    assert releases == [True]


@pytest.mark.parametrize("outcome", [
    "interrupt", "startup", "repeated", "close-error", "done", "stream-close",
    "worker-close-error", "worker-swap", "lane-swap", "proof-error",
    "startup-close-error",
    "proof-release-interrupt",
])
@pytest.mark.parametrize("poison_facade", [False, True])
@pytest.mark.parametrize("witness", ["native", "legacy"])
def test_lane_admission_retains_original_native_stream_worker(
    monkeypatch, tmp_path, outcome, poison_facade, witness,
):
    opened = threading.Event()
    release = threading.Event()
    cleanup = threading.Event()
    observed = []
    failures = []
    closes = []
    reading = threading.Event()
    proofs = []
    released_proofs = []
    if witness == "legacy":
        monkeypatch.setattr(bridge_event_worker, "_capture", bridge_event_worker._capture_legacy)

    class Response:
        def __iter__(self):
            if outcome == "stream-close":
                reading.set()
                assert release.wait(15)
            return iter(())

        def close(self):
            closes.append("response")
            if outcome in ("stream-close", "worker-close-error") and len(closes) == 1:
                raise RuntimeError("original stream close failed")

    response = Response()
    client = LocalBridgeEventClient()
    monkeypatch.setattr(client, "_connection", lambda: ("http://synthetic.invalid", "synthetic"))
    monkeypatch.setattr(client, "_require_protocol", lambda *args: None)

    def request(*args, **kwargs):
        opened.set()
        if outcome != "stream-close":
            assert release.wait(15), "test did not release stream-open"
        return response

    monkeypatch.setattr(client, "_request", request)
    wake = SupervisorEventWake(client)
    monkeypatch.setattr(bridge_events, "_CLOSE_JOIN_SECONDS", 0.02, raising=False)
    lane = Supervisor(
        SimpleNamespace(), spawn_fn=lambda *args, **kwargs: None, machine="private-test",
        supervisor_id="private-lane", heartbeat=False, recover=False, nudge=False,
        reactive=True, event_wake=wake,
    )
    monkeypatch.setattr(lane, "_recheck_governance", lambda checkpoint: None)
    subscription = BridgeSubscription("synthetic-host", "synthetic-session", "synthetic-lane")
    worker = []

    def poll():
        wake.update([subscription])
        assert opened.wait(5)
        worker.append(wake._thread)
        proofs.append(wake._worker)
        if outcome == "stream-close":
            assert reading.wait(5)
        if outcome == "proof-error":
            def unknown():
                raise OSError("native worker observation unavailable")

            original_check = wake._worker._witness.check
            monkeypatch.setattr(wake._worker._witness, "check", unknown)
            proofs.append(original_check)
        if outcome == "proof-release-interrupt":
            original_release = wake._worker._witness.release

            def release_proof():
                released_proofs.append(1)
                original_release()
                raise KeyboardInterrupt("worker witness cleanup interrupted")

            monkeypatch.setattr(wake._worker._witness, "release", release_proof)
        original_join = wake._thread.join
        monkeypatch.setattr(
            wake._thread, "join", lambda timeout=None: original_join(timeout=0.02),
        )
        if poison_facade:
            monkeypatch.setattr(wake._thread, "is_alive", lambda: False)
        if outcome == "lane-swap":
            lane.event_wake = SupervisorEventWake()
        if outcome == "done":
            release.set()
            return []
        if outcome in ("startup", "startup-close-error"):
            raise asyncio.CancelledError("original startup cancellation")
        raise KeyboardInterrupt

    monkeypatch.setattr(lane, "poll_once", poll)

    def done_wait(timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(lane, "wait_for_turn_end", done_wait)
    real_close = wake.close
    close_calls = []

    def close():
        close_calls.append(1)
        try:
            real_close()
        finally:
            cleanup.set()
        if outcome == "worker-swap":
            wake._worker = None
            wake._thread = None
        if outcome in ("close-error", "worker-swap", "startup-close-error"):
            raise RuntimeError("original intake close failed")
        if outcome == "repeated" and len(close_calls) < 3:
            if len(close_calls) == 1:
                raise KeyboardInterrupt("first cleanup interrupt")
            raise asyncio.CancelledError("second cleanup interrupt")

    monkeypatch.setattr(wake, "close", close)
    registry = tmp_path / "registry"

    def observe():
        try:
            assert cleanup.wait(10)
            if outcome != "done":
                time.sleep(0.06)
                record, = inspect_instances(registry)
                assert record["state"] == "running"
                assert not worker[0]._handle.is_done()
                observed.append(record["instance_id"])
        except BaseException as exc:
            failures.append(exc)
        finally:
            if outcome == "proof-error" and len(proofs) == 2:
                proofs[0]._witness.check = proofs[1]
            release.set()

    observer = threading.Thread(target=observe, daemon=True)
    try:
        with daemon_test_lifecycle(owner="lane-event-retirement", registry_root=registry) as env:
            for name, value in env.items():
                monkeypatch.setenv(name, value)
            monkeypatch.delenv(INSTANCE_ENV, raising=False)
            observer.start()
            if outcome in ("startup", "startup-close-error"):
                with pytest.raises(asyncio.CancelledError, match="original startup cancellation"):
                    lane.serve()
            elif outcome in ("close-error", "worker-swap"):
                with pytest.raises(RuntimeError, match="original intake close failed"):
                    lane.serve()
            elif outcome in ("stream-close", "worker-close-error"):
                with pytest.raises(RuntimeError, match="original stream close failed"):
                    lane.serve()
            elif outcome == "proof-error":
                with pytest.raises(OSError, match="native worker observation unavailable"):
                    lane.serve()
            elif outcome == "proof-release-interrupt":
                with pytest.raises(KeyboardInterrupt, match="worker witness cleanup interrupted"):
                    lane.serve()
            elif outcome == "repeated":
                with pytest.raises(KeyboardInterrupt, match="first cleanup interrupt"):
                    lane.serve()
            else:
                lane.serve()
            observer.join(5)
            if failures:
                raise failures[0]
            if outcome != "done":
                assert observed
            assert worker[0]._handle.is_done()
            assert inspect_instances(registry)[0]["state"] == "finished"
            assert closes
            if outcome in ("stream-close", "worker-close-error"):
                assert len(closes) >= 2
            if outcome == "proof-release-interrupt":
                assert released_proofs == [1]
    finally:
        release.set()
        wake._stop.set()
        wake._changed.set()
        if worker:
            worker[0]._handle.join(5)
        if observer.ident is not None:
            observer.join(5)
