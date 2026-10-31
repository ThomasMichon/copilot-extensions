"""Real mux/TTY worker lifetime; the engine is an audited contract double."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs
from zdd.diagnostics import _pid_alive, process_start_time


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _wait(predicate, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError(message)


def _live(identity):
    return (
        identity is not None
        and _pid_alive(identity["pid"])
        and process_start_time(identity["pid"]) == identity["start"]
    )


def _kill(identity):
    if _live(identity):
        try:
            os.kill(identity["pid"], signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        except OSError:
            if _live(identity):
                raise


def _environment(root):
    # Start with OS executable-discovery essentials, never an inherited control
    # plane, credential, installation-cell, Git, or Python override.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    for key, name in {
        "HOME": "home", "USERPROFILE": "home", "AGENT_HOME": "home",
        "APPDATA": "roaming", "LOCALAPPDATA": "local",
        "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state", "XDG_CACHE_HOME": "cache",
        "TMP": "tmp", "TEMP": "tmp", "TMPDIR": "tmp",
        "WORKTREE_MANAGER_ROOT": "manager",
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": "providers",
        "PSMUX_DATA_DIR": "psmux", "TMUX_TMPDIR": "tmux",
    }.items():
        path = root / name
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    env.update(
        PSMUX_NO_WARM="1", TERM="xterm-256color",
        AGENT_WORKTREES_LAUNCH_TRACE=str(root / "launch.jsonl"),
        WORKTREE_MANAGER_ENGINE_ARGV=json.dumps([
            sys.executable, "-I", "-B", str(Path(__file__).resolve()), "engine", str(root),
        ]),
    )
    if os.name == "nt":
        native_temp = Path(env["USERPROFILE"]) / "AppData" / "Local" / "Temp"
        native_temp.mkdir(parents=True)
        env["TMP"] = env["TEMP"] = str(native_temp)
    else:
        env["TMUX"] = str(root / "tmux" / "fixture.sock") + ",0,0"
    return env


def _engine(root):
    from worktree_manager import demo_engine

    record = {
        "pid": os.getpid(), "start": process_start_time(os.getpid()),
        "argv": sys.argv[3:],
        "roots": {
            key: value for key, value in os.environ.items()
            if key in _environment_keys()
        },
    }
    (root / ("engine-" + str(os.getpid()) + ".json")).write_text(json.dumps(record))
    return demo_engine.main(sys.argv[3:])


def _environment_keys():
    return {
        "HOME", "USERPROFILE", "AGENT_HOME", "APPDATA", "LOCALAPPDATA",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
        "TMP", "TEMP", "TMPDIR", "WORKTREE_MANAGER_ROOT",
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", "PSMUX_DATA_DIR", "TMUX_TMPDIR",
    }


@pytest.mark.parametrize("still_live", [False, True])
def test_pinned_cleanup_tolerates_only_a_confirmed_exit_race(monkeypatch, still_live):
    states = iter([True, still_live])
    monkeypatch.setattr(sys.modules[__name__], "_live", lambda _: next(states))

    def denied(*args):
        raise PermissionError("exit raced termination")

    monkeypatch.setattr(os, "kill", denied)
    if still_live:
        with pytest.raises(PermissionError):
            _kill({"pid": 123, "start": "fixture"})
    else:
        _kill({"pid": 123, "start": "fixture"})


def _owner(root):
    from worktree_manager import demo
    from worktree_manager.warm_picker_pool import WarmPickerPool, runtime_identity
    from worktree_manager.warm_picker_worker import publish

    publish(root / "owner.json", {"pid": os.getpid(), "start": process_start_time(os.getpid())})
    pool = WarmPickerPool(root / "manager")
    claimed = False
    try:
        while True:
            if not (root / "slot.json").exists():
                pool.pump()
            for slot in pool._slots:
                ready = _read(slot.directory / "ready.json")
                if ready is None:
                    continue
                if not (root / "slot.json").exists():
                    publish(root / "slot.json", {
                        "directory": str(slot.directory), "session": slot.session,
                        "incarnation": slot.incarnation, "worker": ready,
                    })
                if not claimed and (root / "activate").exists():
                    result = pool.acquire({
                        "manager_runtime": runtime_identity(), "engine": pool.engine,
                        "context": ready["context"], "cwd": str(root),
                        "handoff": {
                            "ok": True, "contract_version": 1,
                            "runtime_root": str(root / "home" / ".agent-worktrees"),
                            "bootstrap": {
                                "version": 1, "project": demo.DEMO_PROJECT,
                                "headless": False, "should_switch_cwd": False,
                                "default_live": False,
                                "project_config": {
                                    "config_dir": str(root / "home"),
                                    "repo_dir": str(root), "machine": "fixture",
                                },
                            },
                        },
                    })
                    publish(root / "claim.json", result)
                    if result.get("available"):
                        (slot.directory / "attached").touch()
                        claimed = True
            time.sleep(.05)
    finally:
        pool.close()


@pytest.mark.parametrize("phase", ["pending", "bound"])
def test_abrupt_owner_death_stops_real_tty_worker_and_retires_slot(tmp_path, phase):
    if process_start_time(os.getpid()) is None:
        pytest.skip("warm UI process-birth identity is unsupported on this platform")
    mux = shutil.which("psmux" if os.name == "nt" else "tmux")
    if mux is None or os.name == "nt" and shutil.which("pwsh") is None:
        pytest.skip("native mux and its shell are required")
    env = _environment(tmp_path)
    namespace = [] if os.name == "nt" else ["-S", env["TMUX"].split(",")[0]]

    def command(*args):
        return subprocess.run(
            [mux, *namespace, *args], env=env, cwd=tmp_path,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5, **no_window_kwargs(),
        )

    owner = None
    slot = None
    with (tmp_path / "owner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "owner", str(tmp_path)],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **no_window_kwargs(),
        )
        try:
            owner = _wait(lambda: _read(tmp_path / "owner.json"), "owner did not start")
            slot = _wait(
                lambda: _read(tmp_path / "slot.json"),
                "unmodified worker did not preload: " + str(tmp_path / "owner.log"),
            )
            directory = Path(slot["directory"])
            assert directory.is_relative_to(tmp_path / "manager" / "picker-sessions")
            assert _live(slot["worker"])
            if phase == "bound":
                (tmp_path / "activate").touch()
                claim = _wait(lambda: _read(tmp_path / "claim.json"), "worker was not claimed")
                assert claim.get("available"), claim
                _wait(
                    lambda: any(
                        "list" in (_read(path) or {}).get("argv", [])
                        for path in tmp_path.glob("engine-*.json")
                    ),
                    "production Picker did not bind its real engine process",
                )
            else:
                assert not list(tmp_path.glob("engine-*.json"))
            captured = command("capture-pane", "-p", "-t", slot["session"])
            assert captured.returncode == 0, captured.stderr
            assert captured.stdout.strip(), "worker did not render on its native TTY"
            _kill(owner)
            process.wait(timeout=5)
            _wait(lambda: not _live(owner), "owner did not die", timeout=5)
            _wait(lambda: not _live(slot["worker"]), "worker survived its owner", timeout=5)
            _wait(
                lambda: command("has-session", "-t", slot["session"]).returncode != 0,
                "native mux session survived its worker", timeout=5,
            )
            assert not directory.exists(), "cancelled worker left its slot behind"
            records = [_read(path) for path in tmp_path.glob("engine-*.json")]
            _wait(
                lambda: all(not _live(record) for record in records),
                "engine child survived its UI", timeout=5,
            )
            expected = {key: env[key] for key in _environment_keys()}
            for record in records:
                assert record["roots"] == expected, "engine state-root mismatch: " + repr({
                    key: (expected.get(key), record["roots"].get(key))
                    for key in expected.keys() | record["roots"].keys()
                    if expected.get(key) != record["roots"].get(key)
                })
        finally:
            _kill(owner)
            if slot is not None:
                _kill(slot["worker"])
                command("kill-session", "-t", slot["session"])
            for path in tmp_path.glob("engine-*.json"):
                _kill(_read(path))
            command("kill-server")
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit((_owner if sys.argv[1] == "owner" else _engine)(Path(sys.argv[2])))
