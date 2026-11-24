"""Consoleless finite entry into the canonical pre-interpreter capture owner."""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
from typing import Any

import pytest

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

import test_bootstrap_capture_native as capture


REPO = Path(__file__).resolve().parents[3]
selector_probe = capture.selector_probe
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(os.name != "nt", reason="native Windows task entry"),
]


@pytest.fixture(scope="module")
def task_host(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("bootstrap-task")
    spec = importlib.util.spec_from_file_location(
        "bootstrap_task_builder", REPO / "tools/build-bootstrap-capture.py",
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    output = directory / "bootstrap-capture.exe"
    getattr(builder, "build")(output)
    data = output.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    assert struct.unpack_from("<H", data, pe + 24 + 68)[0] == 2, "GUI subsystem"
    return output


def _arguments(
    host: Path, log: Path, cwd: Path, executable: Path, *args: str,
    timeout: int = 20000, cleanup: int = 3000,
) -> list[str]:
    return [
        str(host), "--task", str(timeout), str(cleanup), str(log), str(cwd),
        str(executable), *args,
    ]


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_consoleless_task_repeats_with_native_streams_exit_and_environment(
    task_host: Path, tmp_path: Path, shell: str,
    record_property: Any,
) -> None:
    powershell = shutil.which(shell)
    assert powershell, f"required native host {shell}"
    gui = Path(sys.base_prefix) / "pythonw.exe"
    assert gui.is_file()
    script = tmp_path / "child.ps1"
    script.write_text(
        """param([string]$Value,[int]$ExitCode)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ConsoleState { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
$packet=@{argv=$Value; cwd=[Environment]::CurrentDirectory; marker=$env:TASK_MARKER; console=[ConsoleState]::GetConsoleWindow().ToInt64()}
[Console]::Out.WriteLine(($packet | ConvertTo-Json -Compress))
[Console]::Error.WriteLine('task-stderr')
[Console]::Out.Write(('x' * 1100000))
exit $ExitCode
""",
        encoding="utf-8-sig",
    )
    parent = tmp_path / "gui-parent.py"
    parent.write_text(
        "import json,subprocess,sys\n"
        "from pathlib import Path\n"
        "result=subprocess.run(json.loads(sys.argv[2]),stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25)\n"
        "Path(sys.argv[1]).write_text(str(result.returncode))\n",
        encoding="utf-8",
    )
    samples = []
    for cycle in range(2):
        log, result = tmp_path / f"task-{cycle}.log", tmp_path / f"result-{cycle}"
        exit_code = 41 if cycle == 0 else 0
        value = 'space "quoted" café ☃ trailing\\'
        command = _arguments(
            task_host, log, tmp_path, Path(powershell),
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), value, str(exit_code),
        )
        started = time.monotonic()
        # A physical GUI parent leaves the new GUI task root unmasked by flags.
        completed = subprocess.run(
            [str(gui), "-I", "-B", str(parent), str(result), json.dumps(command)],
            env={**os.environ, "PATH": str(tmp_path), "TASK_MARKER": "kept"},
            capture_output=True, timeout=30, **no_window_kwargs(),
        )
        samples.append(time.monotonic() - started)
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert result.read_text() == str(exit_code)
        content = log.read_text(encoding="utf-8")
        packets = [json.loads(line) for line in content.splitlines() if line.startswith("{")]
        assert packets == [{
            "argv": value, "cwd": str(tmp_path), "marker": "kept", "console": 0,
        }]
        assert "task-stderr" in content and content.count("x") >= 1100000
    record_property("bootstrap_task_performance", json.dumps({
        "shell": shell, "samples_seconds": samples, "cycles": 2,
        "parent": "physical-gui", "child_console": 0,
        "visual_observation": "unmeasured: unattended runner",
    }))


@pytest.mark.parametrize("ending", ["deadline", "caller-loss"])
def test_task_deadline_and_root_loss_retire_only_the_owned_descendants(
    task_host: Path, selector_probe: Path, tmp_path: Path, ending: str,
) -> None:
    kernel = capture._kernel()
    marker = tmp_path / "identities"
    log = tmp_path / "task.log"
    process, job = spawn_sync_in_kill_on_close_job(
        _arguments(
            task_host, log, tmp_path, selector_probe, timeout=10000, cleanup=3000,
        ),
        require_job=True, allow_breakaway=False,
        env={**os.environ, "BOOTSTRAP_PROBE_MARKER": str(marker)},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    unrelated = subprocess.Popen(
        [str(selector_probe), "leaf"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **no_window_kwargs(),
    )
    handles = []
    try:
        handles = capture._pin_workers(marker, process, kernel)
        if ending == "caller-loss":
            process.kill()
        stdout, stderr = process.communicate(timeout=15)
        if ending == "deadline":
            assert process.returncode == 124, (stdout, stderr, log.read_bytes())
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 3000) == 0
        assert unrelated.poll() is None
    finally:
        assert job is not None
        job.terminate_and_wait(3)
        job.close()
        process.wait(timeout=3)
        unrelated.terminate()
        unrelated.wait(timeout=3)
        for handle in handles:
            assert kernel.CloseHandle(handle), ctypes.get_last_error()


def test_task_refuses_missing_executable_and_invalid_deadline(
    task_host: Path, tmp_path: Path,
) -> None:
    capabilities = subprocess.run(
        [str(task_host), "--capabilities"], capture_output=True, timeout=5,
        **no_window_kwargs(),
    )
    assert capabilities.returncode == 0
    assert json.loads(capabilities.stdout) == {
        "version": 1, "capabilities": ["finite-task-v1"],
    }
    unknown = subprocess.run(
        [str(task_host), "--unknown"], capture_output=True, timeout=5, **no_window_kwargs(),
    )
    assert unknown.returncode == 2
    log = tmp_path / "missing.log"
    command = _arguments(task_host, log, tmp_path, tmp_path / "missing.exe")
    result = subprocess.run(command, capture_output=True, timeout=25, **no_window_kwargs())
    assert result.returncode == 1
    assert "native-error=2" in log.read_text()
    invalid = _arguments(
        task_host, tmp_path / "invalid.log", tmp_path, tmp_path / "missing.exe",
        timeout=1000, cleanup=1000,
    )
    result = subprocess.run(invalid, capture_output=True, timeout=5, **no_window_kwargs())
    assert result.returncode == 2 and not (tmp_path / "invalid.log").exists()
