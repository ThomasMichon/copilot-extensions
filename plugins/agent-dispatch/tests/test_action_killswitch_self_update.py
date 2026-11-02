"""Live action inhibition must not retire the consumer that can observe clear."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon, _reg


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_switch_pauses_and_restores_same_supervisor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, legacy: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    root = tmp_path / ".agent-dispatch"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    relative = (
        ".copilot-extensions/bootstrap-killswitch.json"
        if legacy
        else ".copilot-extensions/killSwitches/bootstrap-killswitch.json"
    )
    switch = tmp_path / relative
    switch.parent.mkdir(parents=True)
    switch.write_text(json.dumps({"active": True, "reason": "temporary maintenance"}))
    clock = [0.0]
    spawned: list[object] = []
    target = tmp_path / "next-runtime" / "python"
    launcher = FakeLauncher()
    lock = FakeLock()
    daemon = _daemon(
        FakeClient([_reg("ordinary-lane")]),
        launcher,
        lock=lock,
        clock=lambda: clock[0],
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: root,
        self_update_stale_target=lambda _root, _version: target,
        self_update_spawn=lambda selected, _argv: spawned.append(selected),
    )
    try:
        daemon.reconcile_once()
        lane = launcher.proc_for("ordinary-lane")
        assert lane.poll() is None
        assert daemon._maybe_self_update() is False
        assert spawned == []
        assert lane.terminated is False
        assert lock.released is False

        switch.write_text(json.dumps({"active": False}))
        clock[0] += 61.0
        assert daemon._maybe_self_update() is True
        assert spawned == [target]
    finally:
        daemon.shutdown()


def test_supervisor_rechecks_before_shutdown_after_waiting_for_owned_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    spawned: list[object] = []
    lock = FakeLock()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=lock,
        self_update_enabled=True, self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: tmp_path,
        self_update_stale_target=lambda _root, _version: tmp_path / "next-python",
        self_update_spawn=lambda selected, _argv: spawned.append(selected),
    )

    def wait_for_owned_work(_timeout: float) -> bool:
        (tmp_path / "action-controls.json").write_text(
            '{"supervisor-self-update": {"disabled": true}}',
        )
        return True

    monkeypatch.setattr(daemon, "_await_runtime_pool_idle", wait_for_owned_work)
    try:
        assert daemon._maybe_self_update() is False
        assert spawned == []
        assert lock.released is False
        assert daemon._self_update_last_spawn is None
    finally:
        daemon.shutdown()
