from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from tools.plugin_test_containment import (
    CONTAINED_ENV,
    SANDBOX_ENV,
    Limits,
    isolated_environment,
    partition,
    run_contained,
)
from tools.pytest_portfolio_guard import (
    validate_contained_environment,
    validate_declaration,
)


def _pid_exists(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def test_isolated_environment_redirects_mutable_state(tmp_path):
    env = isolated_environment(
        {
            "PATH": os.environ.get("PATH", ""),
            "AGENT_RT_ROOT": str(Path.home() / ".agent-worktrees"),
            "COPILOT_PLUGIN_ROOT": str(Path.home() / ".copilot/plugins"),
            "GH_TOKEN": "not-a-real-token",
        },
        tmp_path,
    )

    assert env[CONTAINED_ENV] == "1"
    assert env[SANDBOX_ENV] == str(tmp_path.resolve())
    assert env["PATH"] == os.environ.get("PATH", "")
    assert "AGENT_RT_ROOT" not in env
    assert "COPILOT_PLUGIN_ROOT" not in env
    assert "GH_TOKEN" not in env
    for name in (
        "HOME",
        "USERPROFILE",
        "COPILOT_HOME",
        "AGENT_HOME",
        "TEMP",
        "TMP",
        "TMPDIR",
    ):
        Path(env[name]).resolve().relative_to(tmp_path.resolve())


def test_run_contained_returns_child_exit_code_and_preserves_output(tmp_path, capfd):
    env = isolated_environment(os.environ, tmp_path)
    rc = run_contained(
        [sys.executable, "-c", "print('contained-output');raise SystemExit(7)"],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(wall_seconds=10, max_processes=8, max_memory_mb=256, max_temp_mb=32),
    )

    assert rc == 7
    assert "contained-output" in capfd.readouterr().out


def test_timeout_reaps_recursive_descendant(tmp_path):
    pid_file = tmp_path / "descendant.pid"
    script = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"open({str(pid_file)!r},'w').write(str(p.pid));"
        "time.sleep(60)"
    )
    env = isolated_environment(os.environ, tmp_path)
    rc = run_contained(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=0.5,
            max_processes=8,
            max_memory_mb=256,
            max_temp_mb=32,
            poll_seconds=0.05,
        ),
    )

    assert rc == 124
    pid = int(pid_file.read_text(encoding="ascii"))
    deadline = time.monotonic() + 5
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_exists(pid)


def test_timeout_reaps_descendant_requesting_detachment(tmp_path):
    pid_file = tmp_path / "detached-descendant.pid"
    procutil_src = REPO / "libs" / "agent-procutil" / "src"
    script = (
        "import pathlib,subprocess,sys,time;"
        f"sys.path.insert(0,{str(procutil_src)!r});"
        "from agent_procutil import detached_kwargs;"
        "p=subprocess.Popen("
        "[sys.executable,'-c','import time;time.sleep(60)'],"
        "**detached_kwargs(breakaway=True));"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid),encoding='ascii');"
        "time.sleep(60)"
    )
    env = isolated_environment(os.environ, tmp_path)
    rc = run_contained(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=0.5,
            max_processes=8,
            max_memory_mb=256,
            max_temp_mb=32,
            poll_seconds=0.05,
        ),
    )

    assert rc == 124
    pid = int(pid_file.read_text(encoding="ascii"))
    deadline = time.monotonic() + 5
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_exists(pid)


def test_temp_budget_stops_tree(tmp_path):
    script = (
        "import os,pathlib,time;"
        "root=pathlib.Path(os.environ['COPILOT_EXTENSIONS_TEST_SANDBOX']);"
        "(root/'large.bin').write_bytes(b'x'*(2*1024*1024));"
        "time.sleep(60)"
    )
    env = isolated_environment(os.environ, tmp_path)
    rc = run_contained(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=8,
            max_memory_mb=256,
            max_temp_mb=1,
            poll_seconds=0.05,
        ),
    )

    assert rc == 124


def test_process_budget_stops_tree(tmp_path):
    script = (
        "import subprocess,sys,time;"
        "children=[subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])"
        " for _ in range(3)];"
        "time.sleep(60)"
    )
    env = isolated_environment(os.environ, tmp_path)
    rc = run_contained(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=1,
            max_memory_mb=512,
            max_temp_mb=32,
            poll_seconds=0.05,
        ),
    )

    assert rc == 124


def test_memory_budget_stops_tree(tmp_path):
    script = "import time;payload=bytearray(160*1024*1024);time.sleep(60)"
    env = isolated_environment(os.environ, tmp_path)
    rc = run_contained(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=4,
            max_memory_mb=96,
            max_temp_mb=32,
            poll_seconds=0.05,
        ),
    )

    assert rc == 124


def test_environment_validation_fails_closed_on_escaped_tmpdir(tmp_path, monkeypatch):
    env = isolated_environment(os.environ, tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TMPDIR", str(tmp_path.parent))

    with pytest.raises(pytest.UsageError, match="TMPDIR"):
        validate_contained_environment()


def test_injected_pytest_policy_loads_and_enforces_declarations(tmp_path, capfd):
    test_file = tmp_path / "test_invalid.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.mark.portfolio_tier('T0')\n"
        "@pytest.mark.effect('process')\n"
        "def test_invalid(): pass\n",
        encoding="utf-8",
    )
    env = isolated_environment(os.environ, tmp_path)
    pytest_root = Path(pytest.__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join((str(REPO / "tools"), str(pytest_root)))
    rc = run_contained(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_portfolio_guard"],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=8,
            max_memory_mb=512,
            max_temp_mb=32,
        ),
    )

    assert rc == pytest.ExitCode.USAGE_ERROR
    assert "invalid test portfolio declarations" in capfd.readouterr().err

    test_file.write_text(
        "import pytest\n"
        "@pytest.mark.portfolio_tier('T0')\n"
        "def test_valid(): pass\n",
        encoding="utf-8",
    )
    rc = run_contained(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_portfolio_guard"],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=8,
            max_memory_mb=512,
            max_temp_mb=32,
        ),
    )

    assert rc == pytest.ExitCode.OK


def test_effect_declarations_enforce_tier_boundaries():
    assert validate_declaration("T0", set()) is None
    assert validate_declaration("T1", {"filesystem"}) is None
    assert validate_declaration("T2", {"filesystem", "process"}) is None
    assert "does not allow" in validate_declaration("T0", {"process"})
    assert "require a portfolio_tier" in validate_declaration(None, {"filesystem"})
    assert "unknown" in validate_declaration("T2", {"telepathy"})


def test_partition_bounds_sequential_subsuites():
    assert partition(list(range(7)), 3) == [[0, 1, 2], [3, 4, 5], [6]]
    with pytest.raises(ValueError, match="positive"):
        partition([1], 0)
