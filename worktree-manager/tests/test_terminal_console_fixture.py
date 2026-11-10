"""Snapshot races do not weaken the native console's process witnesses."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def console_type(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).with_name("native")))
    return importlib.import_module("_terminal_console_fixture").Console


def _failure(code):
    error = OSError("OpenProcess failed")
    error.winerror = code
    return error


def _observer(console_type, monkeypatch, snapshots, failure):
    console = console_type.__new__(console_type)
    console.process = SimpleNamespace(pid=100, process=1000)
    observations = iter(snapshots)
    monkeypatch.setattr(console, "_process_entries", lambda: next(observations))
    monkeypatch.setattr(console, "birth", lambda handle: {1000: 10, 1020: 20}[handle])

    def pin(pid):
        if pid == 101:
            raise failure
        assert pid == 102
        return 1020

    monkeypatch.setattr(console, "pin", pin)
    return console


def test_departed_snapshot_child_does_not_hide_live_sibling(console_type, monkeypatch):
    sibling = {102: (100, "manager")}
    console = _observer(
        console_type, monkeypatch,
        [{101: (100, "query"), **sibling}, sibling], _failure(87),
    )
    assert console.pin_descendants() == {102: ("manager", 1020)}


@pytest.mark.parametrize("code,present", [(87, True), (5, False), (None, False)])
def test_unexplained_pin_failure_is_not_ignored(console_type, monkeypatch, code, present):
    entry = {101: (100, "query")}
    failure = _failure(code)
    console = _observer(
        console_type, monkeypatch, [entry, entry if present else {}], failure,
    )
    with pytest.raises(OSError) as caught:
        console.pin_descendants()
    assert caught.value is failure
    assert "101 of 100" in failure.__notes__[0]


def test_failed_departure_observation_cannot_qualify(console_type, monkeypatch):
    console = _observer(console_type, monkeypatch, [{101: (100, "query")}], _failure(87))
    observations = iter([{101: (100, "query")}])

    def snapshot():
        try:
            return next(observations)
        except StopIteration:
            raise PermissionError("snapshot unavailable")

    monkeypatch.setattr(console, "_process_entries", snapshot)
    with pytest.raises(PermissionError, match="snapshot unavailable"):
        console.pin_descendants()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows snapshot/OpenProcess race")
def test_native_departed_snapshot_pid_is_confirmed_before_omission(
    console_type, tmp_path, monkeypatch,
):
    script = tmp_path / "root.py"
    script.write_text(
        "import gc,subprocess,sys,time\n"
        "from pathlib import Path\n"
        "root=Path(sys.argv[1])\n"
        "code=\"import sys,time;from pathlib import Path;"
        "r=Path(sys.argv[1]);"
        "\\nwhile not (r/'release').exists(): time.sleep(.01)\"\n"
        "child=subprocess.Popen([sys.executable,'-I','-c',code,str(root)])\n"
        "(root/'child').write_text(str(child.pid))\n"
        "child.wait(timeout=15)\n"
        "del child\n"
        "gc.collect()\n"
        "(root/'departed').touch()\n"
        "deadline=time.monotonic()+15\n"
        "while not (root/'finish').exists() and time.monotonic()<deadline: time.sleep(.01)\n",
        encoding="utf-8",
    )

    def wait(path):
        deadline = time.monotonic() + 10
        while not path.exists():
            assert time.monotonic() < deadline, f"Missing {path.name}"
            time.sleep(.01)

    # Windows venv redirectors add another parent between the root and script.
    python = getattr(sys, "_base_executable", sys.executable)
    with console_type([python, "-I", str(script), str(tmp_path)],
                      env=os.environ.copy(), cwd=tmp_path) as console:
        wait(tmp_path / "child")
        child = int((tmp_path / "child").read_text())
        original_entries = console._process_entries
        snapshot = original_entries()
        assert snapshot[child][0] == console.process.pid
        (tmp_path / "release").touch()
        wait(tmp_path / "departed")
        deadline = time.monotonic() + 10
        while child in original_entries():
            assert time.monotonic() < deadline, "Departed child still has a process object"
            time.sleep(.01)
        calls = []
        original_pin = console.pin

        def pin(pid):
            try:
                return original_pin(pid)
            except OSError as error:
                calls.append((pid, error.winerror))
                raise

        entries = iter([snapshot, original_entries()])
        monkeypatch.setattr(console, "_process_entries", lambda: next(entries))
        monkeypatch.setattr(console, "pin", pin)
        assert child not in console.pin_descendants()
        assert calls == [(child, 87)]
        (tmp_path / "finish").touch()
        assert console.wait() == 0
