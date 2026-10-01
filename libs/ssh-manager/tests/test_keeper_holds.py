from __future__ import annotations

import json
import threading

import pytest

from ssh_manager.forward_keeper import KeeperStore
from ssh_manager.keeper_holds import KeeperHoldStore


def _holds(tmp_path, **kwargs) -> KeeperHoldStore:
    return KeeperHoldStore(
        KeeperStore(tmp_path),
        startup_grace=kwargs.pop("startup_grace", 300.0),
        unknown_grace=kwargs.pop("unknown_grace", 1800.0),
        lock_timeout=kwargs.pop("lock_timeout", 10.0),
        lock_poll=kwargs.pop("lock_poll", 0.0),
        **kwargs,
    )


def test_reads_legacy_mux_as_hold_only_without_holds_field(tmp_path):
    holds = _holds(tmp_path)
    assert holds.read_holds({"mux": "wt-old", "started_at": 1000.0}) == {
        "wt-old": {"mux": "wt-old", "updated_at": 1000.0}
    }
    assert holds.read_holds({"mux": "wt-old", "holds": {}}) == {}


def test_tri_state_probe_semantics(tmp_path, monkeypatch):
    holds = _holds(tmp_path, startup_grace=300.0, unknown_grace=1800.0)
    monkeypatch.setattr("ssh_manager.keeper_holds.time.time", lambda: 2000.0)
    holds.store.write(
        "repo-1",
        {
            "pid": 100,
            "venue_port": 41234,
            "holds": {
                "unknown": {
                    "mux": "wt-unknown",
                    "updated_at": 1000.0,
                    "confirmed_at": 1900.0,
                },
                "gone": {
                    "mux": "wt-gone",
                    "updated_at": 1000.0,
                    "confirmed_at": 1990.0,
                },
                "starting": {"mux": "wt-starting", "updated_at": 1900.0},
                "alive": {"mux": "wt-alive", "updated_at": 1000.0},
            },
        },
    )

    result = holds.list_holds(
        "repo-1",
        probe={
            "wt-unknown": None,
            "wt-gone": False,
            "wt-starting": False,
            "wt-alive": True,
        }.__getitem__,
    )

    assert set(result) == {"unknown", "starting", "alive"}
    stored = holds.read_state("repo-1")["holds"]
    assert stored["alive"]["confirmed_at"] == 2000.0
    assert "gone" not in stored


def test_unknown_probe_eventually_drops_after_confirmation_grace(tmp_path, monkeypatch):
    holds = _holds(tmp_path, startup_grace=300.0, unknown_grace=1800.0)
    monkeypatch.setattr("ssh_manager.keeper_holds.time.time", lambda: 4000.0)
    holds.store.write(
        "repo-1",
        {
            "pid": 100,
            "holds": {
                "old": {
                    "mux": "wt-old",
                    "updated_at": 1000.0,
                    "confirmed_at": 1000.0,
                }
            },
        },
    )

    assert holds.list_holds("repo-1", probe=lambda mux: None) == {}
    assert holds.read_state("repo-1")["holds"] == {}


def test_prune_probes_outside_lock_and_compare_deletes(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    monkeypatch.setattr("ssh_manager.keeper_holds.time.time", lambda: 2000.0)
    holds.store.write(
        "repo-1",
        {
            "pid": 100,
            "holds": {
                "race": {"mux": "wt-race", "updated_at": 1000.0},
            },
        },
    )
    in_lock = False
    real_lock = holds.lock

    def wrapped_lock(key):
        cm = real_lock(key)

        class Wrapper:
            def __enter__(self):
                nonlocal in_lock
                value = cm.__enter__()
                in_lock = True
                return value

            def __exit__(self, *exc):
                nonlocal in_lock
                in_lock = False
                return cm.__exit__(*exc)

        return Wrapper()

    monkeypatch.setattr(holds, "lock", wrapped_lock)

    def probe(_mux):
        assert in_lock is False
        state = holds.read_state("repo-1")
        state["holds"]["race"]["updated_at"] = 2000.0
        holds.store.write("repo-1", state)
        return False

    assert set(holds.list_holds("repo-1", probe=probe)) == {"race"}
    assert holds.read_state("repo-1")["holds"]["race"]["updated_at"] == 2000.0


def test_retiring_keeper_removes_own_empty_state(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    monkeypatch.setattr("ssh_manager.keeper_holds.os.getpid", lambda: 100)
    holds.store.write("repo-1", {"pid": 100, "holds": {}})

    _state, current_holds, _live = holds.prune_snapshot(
        "repo-1",
        probe=lambda mux: (_ for _ in ()).throw(AssertionError("no probe")),
    )

    assert current_holds == {}
    assert holds.read_state("repo-1") is None


def test_remove_self_state_preserves_new_holds(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    monkeypatch.setattr("ssh_manager.keeper_holds.os.getpid", lambda: 100)
    holds.store.write(
        "repo-1",
        {"pid": 100, "holds": {"new": {"mux": "wt-new", "updated_at": 2000.0}}},
    )

    holds.remove_self_state("repo-1")

    assert set(holds.read_state("repo-1")["holds"]) == {"new"}


def test_lock_treats_permission_error_as_contention(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    import os

    real_link = os.link
    attempts = 0

    def flaky_link(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("pending delete")
        return real_link(*args, **kwargs)

    monkeypatch.setattr("ssh_manager.keeper_holds.os.link", flaky_link)

    with holds.lock("repo-1"):
        pass

    assert attempts == 2


def test_partial_lock_file_is_reclaimed_after_acquisition_window(tmp_path, monkeypatch):
    holds = _holds(tmp_path, lock_timeout=0.0)
    monkeypatch.setattr("ssh_manager.keeper_holds.time.time", lambda: 100.0)
    lock = holds.state_path("repo-1").with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("{", encoding="utf-8")
    lock.touch()
    __import__("os").utime(lock, (0.0, 0.0))
    monkeypatch.setattr("ssh_manager.keeper_holds.time.sleep", lambda delay: None)

    with holds.lock("repo-1"):
        assert json.loads(lock.read_text(encoding="utf-8"))["token"]


def test_two_reclaimers_do_not_steal_winners_live_lock(tmp_path, monkeypatch):
    holds = _holds(tmp_path, lock_timeout=0.0, lock_poll=0.001)
    lock = holds.state_path("repo-1").with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": 987654321, "identity": "dead-owner", "token": "stale"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ssh_manager.keeper_holds.process_identity",
        lambda pid: "current-owner" if pid == __import__("os").getpid() else None,
    )
    entered: list[str] = []
    errors: list[str] = []
    start = threading.Barrier(2)
    winner_entered = threading.Event()
    loser_failed = threading.Event()
    release_winner = threading.Event()

    def worker(name: str) -> None:
        start.wait(timeout=2)
        try:
            with holds.lock("repo-1"):
                entered.append(name)
                winner_entered.set()
                release_winner.wait(timeout=2)
        except RuntimeError:
            errors.append(name)
            loser_failed.set()

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    assert winner_entered.wait(timeout=2)
    assert loser_failed.wait(timeout=2)
    release_winner.set()
    for thread in threads:
        thread.join(timeout=2)

    assert len(entered) == 1
    assert len(errors) == 1


def test_publish_lock_temp_cleanup_failure_does_not_lose_acquired_lock(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    real_unlink = type(holds.state_path("repo-1")).unlink
    temp_unlinks = 0

    def flaky_temp_unlink(self, *args, **kwargs):
        nonlocal temp_unlinks
        if self.name.endswith(".tmp"):
            temp_unlinks += 1
            raise PermissionError("scanner still holds temp file")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(holds.state_path("repo-1")), "unlink", flaky_temp_unlink)

    with holds.lock("repo-1"):
        assert json.loads(holds.state_path("repo-1").with_suffix(".lock").read_text())["token"]

    assert temp_unlinks == 1


def test_lock_release_retries_permission_error(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    lock = holds.state_path("repo-1").with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    owner = {"pid": 123, "identity": None, "token": "owner"}
    lock.write_text(json.dumps(owner), encoding="utf-8")
    real_unlink = type(lock).unlink
    attempts = 0

    def flaky_unlink(self, *args, **kwargs):
        nonlocal attempts
        if self == lock:
            attempts += 1
            if attempts == 1:
                raise PermissionError("pending delete")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(lock), "unlink", flaky_unlink)

    holds.release_lock(lock, owner)

    assert attempts == 2
    assert not lock.exists()


def test_live_lock_owner_is_not_stolen(tmp_path, monkeypatch):
    holds = _holds(tmp_path, lock_timeout=0.0)
    owner = {"pid": 123, "identity": "live", "token": "owner"}
    lock = holds.state_path("repo-1").with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps(owner), encoding="utf-8")
    monkeypatch.setattr("ssh_manager.keeper_holds.process_identity", lambda pid: "live")

    with pytest.raises(RuntimeError, match="Could not acquire"):
        with holds.lock("repo-1"):
            pass

    assert json.loads(lock.read_text(encoding="utf-8")) == owner


def test_alive_or_fail_open_catches_lock_and_state_errors(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    monkeypatch.setattr(
        holds,
        "prune_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(OSError("sharing violation")),
    )

    assert holds.alive_or_fail_open("repo-1", probe=lambda mux: False) is True
