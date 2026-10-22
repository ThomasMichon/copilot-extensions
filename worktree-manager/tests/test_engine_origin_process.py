"""Native acquisition owns its child group without excluding portable POSIX."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import engine_origin as origin


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_acquisition_selects_supported_owned_scope(monkeypatch, tmp_path, platform):
    calls = []

    def execute(kind, argv, **kwargs):
        calls.append((kind, argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "updated", "")

    monkeypatch.setattr(origin, "_WINDOWS", False)
    monkeypatch.setattr(
        origin, "sys", SimpleNamespace(platform=platform, executable=sys.executable),
    )
    monkeypatch.setattr(
        origin, "run_caller_owned", lambda *args, **kwargs: execute("linux", *args, **kwargs),
    )
    monkeypatch.setattr(
        origin, "_run_owned", lambda *args, **kwargs: execute("portable", *args, **kwargs),
    )
    environment = {"HOME": str(tmp_path), "PRIVATE_INPUT": "fixture"}
    result = origin._native_acquisition(["copilot", "plugin", "update", "x@y"], environment, tmp_path)
    kind, argv, options = calls[0]
    assert kind == ("linux" if platform == "linux" else "portable")
    assert result.returncode == 0 and result.stdout == "updated"
    assert argv[:4] == [sys.executable, "-I", "-S", "-c"]
    assert "fixture" not in argv
    assert json.loads(options["input_text"]) == {
        "argv": ["copilot", "plugin", "update", "x@y"],
        "environment": environment, "cwd": str(tmp_path),
    }
    assert options["timeout"] == 180
    if platform == "linux":
        assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 65536
    else:
        assert options["capture_limits"] == (65536, 65536)


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit nonpromotion POSIX owned-tree process regression",
)
@pytest.mark.parametrize("mode", ["timeout", "interrupt"])
@pytest.mark.parametrize("route", ["linux", "portable"])
def test_native_posix_failure_has_no_delayed_descendant_write(tmp_path, mode, route):
    driver = tmp_path / "driver.py"
    driver.write_text("""
import json, os, pathlib, signal, subprocess, sys, threading, time
from types import SimpleNamespace
sys.path[:0] = json.loads(sys.argv[1])
from worktree_manager import engine_origin as origin
root = pathlib.Path(sys.argv[2]); mode = sys.argv[3]; route = sys.argv[4]
marker = root / "late"; parent_group = os.getpgrp()
owner_name = "run_caller_owned" if route == "linux" else "_run_owned"
owner = getattr(origin, owner_name)
setattr(origin, owner_name, lambda argv, **kw: owner(
    argv, **{**kw, "timeout": 0.35 if mode == "timeout" else 4}))
if route == "portable":
    origin.sys = SimpleNamespace(executable=sys.executable, platform="darwin")
child = "import pathlib,time;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").write_text('late')"
parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c'," + repr(child) + "]);time.sleep(5)"
timer = threading.Timer(0.35, lambda: os.kill(os.getpid(), signal.SIGINT))
if mode == "interrupt": timer.start()
try:
    origin._native_acquisition([sys.executable, "-I", "-c", parent], dict(os.environ), root)
    raise AssertionError("expected cancellation")
except (subprocess.TimeoutExpired, KeyboardInterrupt): pass
finally:
    if mode == "interrupt": timer.join()
time.sleep(1.2)
assert os.getpgrp() == parent_group, "caller process group changed"
assert not marker.exists(), "descendant wrote after acquisition failure"
""", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(driver), json.dumps(sys.path), str(tmp_path), mode, route],
        capture_output=True, text=True, timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr
