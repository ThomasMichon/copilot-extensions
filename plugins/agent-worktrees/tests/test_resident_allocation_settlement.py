"""The monitor's stronger retirement settles nested admission, not only sockets."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from agent_procutil.daemon_instances import admit_daemon_instance, inspect_instances

from agent_worktrees import mux_link, status_monitor_health


@pytest.mark.parametrize("failure", ["normal", "interrupted", "earlier-error"])
def test_stronger_mux_settlement_waits_for_nested_guard_before_host_return(tmp_path, monkeypatch, failure):
    runtime = mux_link.InProcessRuntime()
    entered, release = threading.Event(), threading.Event()
    interrupts = []
    primary = OSError("earlier bounded shutdown failed")
    with admit_daemon_instance(
        "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
    ) as host:
        runtime.start(tmp_path / "cache.json")
        owner = runtime._retirement
        close = owner.guard.close
        wait = owner.settled.wait
        shutdown = runtime.shutdown

        def delayed_close():
            entered.set()
            assert release.wait(5)
            close()

        def interrupted_wait(*args, **kwargs):
            if len(interrupts) < 2:
                interrupts.append(True)
                raise KeyboardInterrupt("settlement interrupted")
            return wait(*args, **kwargs)

        def failed_shutdown():
            shutdown()
            raise primary

        monkeypatch.setattr(owner.guard, "close", delayed_close)
        if failure == "interrupted":
            monkeypatch.setattr(owner.settled, "wait", interrupted_wait)
        if failure == "earlier-error":
            monkeypatch.setattr(runtime, "shutdown", failed_shutdown)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(runtime.shutdown_and_wait)
            try:
                assert entered.wait(5)
                assert not result.done()
            finally:
                release.set()
            if failure == "normal":
                result.result(timeout=5)
            else:
                error = KeyboardInterrupt if failure == "interrupted" else OSError
                with pytest.raises(error) as raised:
                    result.result(timeout=5)
                if failure == "earlier-error":
                    assert raised.value is primary
        assert owner.settled.is_set() and not owner.thread.is_alive()
    record = next(
        row for row in inspect_instances(host.receipt.registry_root)
        if row["instance_id"] == host.receipt.instance_id
    )
    assert record["state"] == "finished"


@pytest.mark.parametrize("draining,shutdown", [(False, False), (True, False), (True, True)])
def test_health_retains_existing_surfaces_and_reports_private_linker_counts_only(draining, shutdown):
    def surface(capability, **fields):
        return SimpleNamespace(health=lambda: {"capabilities": [capability], **fields})

    result = status_monitor_health.response(
        worktrees=surface("worktrees-current-v1", worktrees={"status": "ready"}),
        receipts=surface("receipts-v1", promptReceipt={"status": "ready"}),
        repositories=surface("repos-map-v1", repositories={"admitting": True}),
        allocations=surface("worktree-allocation-linker-v1", allocationLinker={
            "admitting": not draining, "pending": 1, "active": 0, "discovering": False, "errors": 0,
        }),
        admission_closed=draining, published=not draining, shutdown=shutdown,
    )
    assert result["status"] == ("draining" if draining else "ready")
    assert result["worktrees"]["status"] == ("shutdown" if shutdown else "ready")
    assert result["allocationLinker"]["pending"] == 1
    assert result["allocationLinker"]["admitting"] is not draining
    assert result["capabilities"] == [
        "worktrees-current-v1", "receipts-v1", "repos-map-v1", "worktree-allocation-linker-v1",
    ]
