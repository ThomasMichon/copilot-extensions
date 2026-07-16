"""Tests for agent_worktrees.update_stage -- #1430 background stage-then-join.

Covers the *stage* half only (marketplace download + fingerprint + single-flight
lock + status file). The *apply* half lives in the shell launch wrappers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_worktrees import update_stage as us


# ---------------------------------------------------------------------------
# Single-flight lock
# ---------------------------------------------------------------------------

def test_acquire_then_second_is_blocked(tmp_path: Path):
    lock = tmp_path / "updater.lock"
    # We (this live pid) take the lock.
    assert us.acquire_lock(lock, pid=os.getpid()) is True
    # A foreign acquirer is blocked while the recorded owner (our pid) is alive
    # and fresh -- reclaim keys on the *owner's* liveness, not the caller's.
    assert us.acquire_lock(lock, pid=1234567) is False


def test_stale_lock_by_dead_pid_is_reclaimed(tmp_path: Path):
    lock = tmp_path / "updater.lock"
    # Dead owner (pid that isn't running) -> reclaimable.
    lock.write_text(json.dumps({"pid": 999999999, "started": us.time.time()}),
                    encoding="utf-8")
    assert us.acquire_lock(lock, pid=os.getpid()) is True


def test_stale_lock_by_age_is_reclaimed(tmp_path: Path):
    lock = tmp_path / "updater.lock"
    old = us.time.time() - (us._LOCK_TTL_SECS + 10)
    # Even our own live pid, if the lock is older than the TTL, is reclaimable.
    lock.write_text(json.dumps({"pid": os.getpid(), "started": old}),
                    encoding="utf-8")
    assert us.acquire_lock(lock, pid=os.getpid()) is True


def test_release_only_removes_own_lock(tmp_path: Path):
    lock = tmp_path / "updater.lock"
    lock.write_text(json.dumps({"pid": 4242, "started": us.time.time()}),
                    encoding="utf-8")
    us.release_lock(lock, pid=os.getpid())      # not our lock -> untouched
    assert lock.exists()
    us.release_lock(lock, pid=4242)             # owner releases
    assert not lock.exists()


# ---------------------------------------------------------------------------
# Plugin discovery + fingerprint
# ---------------------------------------------------------------------------

def _make_marketplace(home: Path, files: dict[str, str]) -> Path:
    d = (home / ".copilot" / "installed-plugins" / "copilot-extensions"
         / "agent-worktrees")
    d.mkdir(parents=True)
    for rel, content in files.items():
        fp = d / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    return d


def _make_runtime_manifest(home: Path, version: str) -> Path:
    """Write the deployed runtime's deploy-manifest (source.version) under home."""
    d = home / ".agent-worktrees"
    d.mkdir(parents=True, exist_ok=True)
    mf = d / "deploy-manifest.json"
    mf.write_text(json.dumps({"source": {"version": version}}), encoding="utf-8")
    return mf


def test_discover_marketplace_layout(tmp_path: Path):
    home = tmp_path / "home"
    d = _make_marketplace(home, {"plugin.json": '{"name":"agent-worktrees"}'})
    found, layout = us.discover_plugin_dir(home)
    assert found == d
    assert layout == "marketplace"


def test_discover_none_when_absent(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    found, layout = us.discover_plugin_dir(home)
    assert found is None
    assert layout == ""


def test_fingerprint_changes_with_content(tmp_path: Path):
    home = tmp_path / "home"
    d = _make_marketplace(home, {"plugin.json": '{"version":"1"}'})
    fp1 = us.fingerprint(d)
    (d / "plugin.json").write_text('{"version":"2"}', encoding="utf-8")
    fp2 = us.fingerprint(d)
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# stage() end to end (copilot mocked)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_prelaunch(monkeypatch):
    # plan_pre_launch touches real repo/config; stub it for stage() tests.
    import agent_worktrees.__main__ as m
    monkeypatch.setattr(m, "plan_pre_launch", lambda: {"action": "continue"})


def test_stage_skipped_when_no_plugin_dir(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"
    result = us.stage(status=status, lock=lock, home=home)
    assert result["stage_done"] is True
    assert result["skipped"] == "no-plugin-dir"
    assert result["plugin_changed"] is False
    assert json.loads(status.read_text(encoding="utf-8"))["skipped"] == "no-plugin-dir"


def test_stage_detects_change(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    d = _make_marketplace(home, {"plugin.json": '{"version":"dev1"}'})
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"

    def fake_update():
        # Simulate the marketplace download rewriting the payload.
        (d / "plugin.json").write_text('{"version":"dev2"}', encoding="utf-8")
        return True, "updated to dev2"

    monkeypatch.setattr(us, "_run_copilot_update", fake_update)
    result = us.stage(status=status, lock=lock, home=home)
    assert result["stage_done"] is True
    assert result["plugin_changed"] is True
    assert result["prelaunch"] == {"action": "continue"}


def test_stage_no_change_when_download_noop(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    _make_marketplace(home, {"plugin.json": '{"version":"dev1"}'})
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"
    monkeypatch.setattr(us, "_run_copilot_update",
                        lambda: (True, "already at latest"))
    result = us.stage(status=status, lock=lock, home=home)
    assert result["plugin_changed"] is False


def test_stage_detects_venv_drift_when_payload_ahead(tmp_path: Path, monkeypatch):
    # #2826: the payload already advanced on a prior run (dev2) but the runtime
    # venv is still dev1. The download is a no-op ("already at latest"), so the
    # fingerprint never moves -- only the version-drift reconcile catches it.
    home = tmp_path / "home"
    _make_marketplace(home, {"plugin.json": '{"version":"dev2"}'})
    _make_runtime_manifest(home, "dev1")
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"
    monkeypatch.setattr(us, "_run_copilot_update",
                        lambda: (True, "already at latest"))
    result = us.stage(status=status, lock=lock, home=home)
    assert result["venv_drift"] is True
    assert result["plugin_changed"] is True
    assert result["plugin_changed_reason"] == "venv-drift"
    assert result["payload_version"] == "dev2"
    assert result["deployed_version"] == "dev1"


def test_stage_no_drift_when_versions_match(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    _make_marketplace(home, {"plugin.json": '{"version":"dev1"}'})
    _make_runtime_manifest(home, "dev1")
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"
    monkeypatch.setattr(us, "_run_copilot_update",
                        lambda: (True, "already at latest"))
    result = us.stage(status=status, lock=lock, home=home)
    assert result["venv_drift"] is False
    assert result["plugin_changed"] is False


def test_stage_single_flight_second_call_skips(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    _make_marketplace(home, {"plugin.json": '{"version":"dev1"}'})
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"
    # Pre-hold the lock with a *live* foreign owner (our own pid) so the stage's
    # acquire fails and it records skipped=locked.
    lock.write_text(json.dumps({"pid": os.getpid(), "started": us.time.time()}),
                    encoding="utf-8")

    called = {"n": 0}

    def fake_update():
        called["n"] += 1
        return True, "x"

    monkeypatch.setattr(us, "_run_copilot_update", fake_update)
    result = us.stage(status=status, lock=lock, home=home)
    assert result["skipped"] == "locked"
    assert called["n"] == 0  # never hit the network while locked


# ---------------------------------------------------------------------------
# indicator_state (picker-facing, #1430)
# ---------------------------------------------------------------------------

def test_indicator_idle_when_no_status(tmp_path: Path):
    assert us.indicator_state(status=tmp_path / "none.json",
                              lock=tmp_path / "none.lock") == "idle"


def test_indicator_checking_on_live_lock(tmp_path: Path):
    lock = tmp_path / "lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started": us.time.time()}),
                    encoding="utf-8")
    assert us.indicator_state(status=tmp_path / "none.json", lock=lock) == "checking"


def test_indicator_current_and_available(tmp_path: Path):
    status = tmp_path / "status.json"
    lock = tmp_path / "lock"  # absent
    status.write_text(json.dumps({"stage_done": True, "plugin_changed": False}),
                      encoding="utf-8")
    assert us.indicator_state(status=status, lock=lock) == "current"
    status.write_text(json.dumps({"stage_done": True, "plugin_changed": True}),
                      encoding="utf-8")
    assert us.indicator_state(status=status, lock=lock) == "available"


def test_indicator_locked_skip_reads_as_checking(tmp_path: Path):
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"stage_done": True, "skipped": "locked",
                                  "plugin_changed": False}), encoding="utf-8")
    assert us.indicator_state(status=status, lock=tmp_path / "none.lock") == "checking"
