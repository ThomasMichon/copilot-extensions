"""Actual input and contributor deadline settlement through the generated hook."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import time
from collections.abc import Callable

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned
from test_powershell_guidance import (
    REPO,
    packaged_guidance as packaged_guidance,
    prepared_guidance_python as prepared_guidance_python,
)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        from ctypes import wintypes

        kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    state = Path("/proc") / str(pid) / "stat"
    try:
        return not state.read_text().split(") ", 1)[1].startswith("Z")
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("entry", ["powershell", "pwsh"])
@pytest.mark.parametrize("scenario", ["held-input", "slow-selection"])
def test_guidance_deadline_settles_actual_hook(
    tmp_path: Path, entry: str, scenario: str, packaged_guidance: Path,
    prepared_guidance_python: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    shell = shutil.which(entry)
    if not shell or entry == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    _uv, selected, managed_root = prepared_guidance_python
    payload = packaged_guidance
    scripts = payload / "scripts"
    home = tmp_path / "home"
    home.mkdir()
    proofs = tmp_path / "contributors"
    proofs.mkdir()
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(managed_root), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "GUIDANCE_TEST_PROOFS": str(proofs), "GUIDANCE_TEST_PYTHON": str(selected),
        "COPILOT_EXTENSIONS_GUIDANCE_DEADLINE": "invalid-inherited-deadline",
    }
    if scenario == "slow-selection":
        selector = scripts / "_bootstrap_python/select.ps1"
        selector.write_text(
            selector.read_text(encoding="utf-8").replace(
                "$ErrorActionPreference = 'Stop'",
                "Start-Sleep -Seconds 17\n$ErrorActionPreference = 'Stop'", 1,
            ),
            encoding="utf-8",
        )
        blocked = scripts / "blocked_contributor.py"
        blocked.write_text(
            "import os,time\nfrom pathlib import Path\n"
            "Path(os.environ['GUIDANCE_TEST_PROOFS'],str(os.getpid())).write_text(str(os.getpid()))\n"
            "time.sleep(20)\n", encoding="utf-8",
        )
        shutil.copy2(blocked, scripts / "snapshot_runtime.py")
        (scripts / "emit-command-catalog.ps1").write_text(
            "[IO.File]::WriteAllText((Join-Path $env:GUIDANCE_TEST_PROOFS ([string]$PID)),[string]$PID)\n"
            "& $env:GUIDANCE_TEST_PYTHON (Join-Path $PSScriptRoot 'blocked_contributor.py')\n",
            encoding="utf-8",
        )
        (scripts / "emit-command-catalog.sh").write_text(
            '#!/bin/sh\nexec "$GUIDANCE_TEST_PYTHON" "$COPILOT_PLUGIN_ROOT/scripts/blocked_contributor.py"\n',
            encoding="utf-8",
        )
        dependency = payload / "libs/agent-procutil/src/agent_procutil"
        dependency.mkdir(parents=True)
        shutil.copy2(REPO / "libs/agent-procutil/src/agent_procutil/__init__.py", dependency / "__init__.py")
    hooks = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"]
    command = [h["powershell"] for h in hooks if "write-session-guidance.ps1" in h["powershell"]]
    assert len(command) == 1
    argv = [shell, "-NoProfile", "-NonInteractive", "-Command", command[0]]
    if scenario == "held-input":
        worker = (
            "import json,os,subprocess,sys,time\n"
            "p=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.PIPE,"
            "stdout=subprocess.PIPE,stderr=subprocess.PIPE,"
            "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
            "try:\n"
            " p.stdin.write(b'{\"sessionId\":\"session-1\"}');p.stdin.flush()\n"
            " p.wait(timeout=48)\n"
            " print(json.dumps({'code':p.returncode,'out':p.stdout.read().decode(),"
            "'err':p.stderr.read().decode()}))\n"
            "finally:\n"
            " if p.poll() is None:p.kill();p.wait(timeout=5)\n"
            " try:p.stdin.close()\n"
            " except OSError:pass\n"
        )
        argv = [sys.executable, "-I", "-B", "-c", worker, json.dumps(argv)]
    started = time.monotonic()
    result = run_caller_owned(
        argv, env=env, cwd=tmp_path, input_text='{"sessionId":"session-1"}',
        timeout=55, max_stdout_bytes=65536, max_stderr_bytes=65536,
        budget=OwnedExecutionBudget.from_timeout(60, cleanup_reserve=5),
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0 and not result.stderr, result
    if scenario == "held-input":
        data = json.loads(result.stdout)
        assert data == {"code": 0, "out": "{}", "err": ""}
        assert not list(proofs.iterdir())
    else:
        pids = [int(p.read_text()) for p in proofs.iterdir()]
        survivors = [pid for pid in pids if _alive(pid)]
        # A broken owner still cannot leave this finite fixture running indefinitely.
        settlement = time.monotonic() + 22
        while any(_alive(pid) for pid in survivors) and time.monotonic() < settlement:
            time.sleep(.05)
        assert pids and not survivors, survivors
        assert result.stdout == "{}"
        assert (home / ".copilot/session-state/session-1/instructions/agent-ssh/session-guidance.instructions.md").is_file()
    assert elapsed < 45, elapsed
    record_property("guidance_deadline_execution", json.dumps({
        "entry": entry, "scenario": scenario, "elapsed_seconds": elapsed,
        "hook_timeout_seconds": 45, "contributors_remaining": 0,
    }))
