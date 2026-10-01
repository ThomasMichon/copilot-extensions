from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time

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
    attempts = 0
    real_acquire = holds.acquire_os_lock

    def flaky_acquire(handle):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("pending delete")
        return real_acquire(handle)

    monkeypatch.setattr(holds, "acquire_os_lock", flaky_acquire)

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
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"]


def test_os_lock_released_when_holder_process_dies(tmp_path):
    script = tmp_path / "hold_lock.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            import time
            from pathlib import Path
            from ssh_manager.forward_keeper import KeeperStore
            from ssh_manager.keeper_holds import KeeperHoldStore

            holds = KeeperHoldStore(KeeperStore(Path(sys.argv[1])), lock_timeout=1.0, lock_poll=0.01)
            with holds.lock("repo-1"):
                print("READY", flush=True)
                time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    env = {
        **__import__("os").environ,
        "PYTHONPATH": __import__("os").pathsep.join(sys.path),
    }
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        if line != "READY":
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            raise AssertionError(f"child did not acquire lock: stdout={line!r} stderr={stderr!r}")
        proc.kill()
        proc.wait(timeout=5)
        with _holds(tmp_path, lock_timeout=1.0, lock_poll=0.01).lock("repo-1"):
            pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_two_process_contenders_never_both_hold_lock(tmp_path):
    script = tmp_path / "contend_lock.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            import time
            from pathlib import Path
            from ssh_manager.forward_keeper import KeeperStore
            from ssh_manager.keeper_holds import KeeperHoldStore

            root, active, overlap, name = sys.argv[1:]
            holds = KeeperHoldStore(KeeperStore(Path(root)), lock_timeout=5.0, lock_poll=0.01)
            with holds.lock("repo-1"):
                active_path = Path(active)
                if active_path.exists():
                    Path(overlap).write_text(name, encoding="utf-8")
                active_path.write_text(name, encoding="utf-8")
                print(f"ENTER {name}", flush=True)
                time.sleep(0.25)
                active_path.unlink(missing_ok=True)
                print(f"EXIT {name}", flush=True)
            """
        ),
        encoding="utf-8",
    )
    active = tmp_path / "active"
    overlap = tmp_path / "overlap"
    procs: list[subprocess.Popen[str]] = []
    env = {
        **__import__("os").environ,
        "PYTHONPATH": __import__("os").pathsep.join(sys.path),
    }
    with _holds(tmp_path, lock_timeout=1.0, lock_poll=0.01).lock("repo-1"):
        for name in ("a", "b"):
            procs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        str(script),
                        str(tmp_path),
                        str(active),
                        str(overlap),
                        name,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
            )
        time.sleep(0.25)
    outputs = [proc.communicate(timeout=10) for proc in procs]

    assert [proc.returncode for proc in procs] == [0, 0], outputs
    assert not overlap.exists()
    assert sorted(line for out, _err in outputs for line in out.splitlines()) == [
        "ENTER a",
        "ENTER b",
        "EXIT a",
        "EXIT b",
    ]


def test_live_os_lock_owner_is_not_stolen(tmp_path, monkeypatch):
    holds = _holds(tmp_path, lock_timeout=0.0)
    monkeypatch.setattr(
        holds,
        "acquire_os_lock",
        lambda _handle: (_ for _ in ()).throw(PermissionError("lock held")),
    )

    with pytest.raises(RuntimeError, match="Could not acquire"):
        with holds.lock("repo-1"):
            pass


def test_alive_or_fail_open_catches_lock_and_state_errors(tmp_path, monkeypatch):
    holds = _holds(tmp_path)
    monkeypatch.setattr(
        holds,
        "prune_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(OSError("sharing violation")),
    )

    assert holds.alive_or_fail_open("repo-1", probe=lambda mux: False) is True
