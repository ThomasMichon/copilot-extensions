"""Native selector custody before the Python SDK worker exists."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import pytest

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

import test_payload_invocation as payload_tests


PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
windows_payload_runtime = payload_tests.windows_payload_runtime
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(os.name != "nt", reason="native Windows Job custody"),
]


@pytest.fixture(scope="module")
def selector_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("bootstrap-probe") / "probe.exe"
    spec = importlib.util.spec_from_file_location(
        "bootstrap_capture_builder", REPO / "tools" / "build-bootstrap-capture.py",
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    getattr(builder, "compile_source")(
        PLUGIN / "tests" / "fixtures" / "bootstrap-capture-probe.cs", output,
    )
    return output


def _kernel() -> Any:
    if sys.platform != "win32":
        raise RuntimeError("native Windows process observation")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_longlong),
        ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_longlong),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _pin_workers(marker: Path, process: subprocess.Popen[bytes], kernel: Any) -> list[int]:
    if sys.platform != "win32":
        raise RuntimeError("native Windows process observation")
    deadline = time.monotonic() + 20
    while True:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            pytest.fail(f"Caller ended before selector execution: {stdout!r} {stderr!r}")
        assert time.monotonic() < deadline, "selector did not publish its native identities"
        try:
            values = [int(value) for value in marker.read_text().split()]
            break
        except (FileNotFoundError, PermissionError):
            time.sleep(0.01)
    assert len(values) == 4
    handles = []
    try:
        for pid, expected_birth in zip(values[::2], values[1::2]):
            handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
            assert handle, ctypes.get_last_error()
            handles.append(handle)
            birth, exit_time, kernel_time, user_time = (ctypes.c_longlong() for _ in range(4))
            assert kernel.GetProcessTimes(
                handle, ctypes.byref(birth), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            ), ctypes.get_last_error()
            assert birth.value == expected_birth
            assert kernel.WaitForSingleObject(handle, 0) == 258
        return handles
    except BaseException:
        for handle in handles:
            kernel.CloseHandle(handle)
        raise


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("selection", ["runtime", "uv"])
def test_unhinted_selector_caller_death_retires_descendants(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    selector_probe: Path, tmp_path: Path, shell: str, selection: str,
) -> None:
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    root = tmp_path / "runtime"
    if selection == "runtime":
        slot = root / "versions" / "1.2.3"
        (slot / "Scripts").mkdir(parents=True)
        shutil.copyfile(selector_probe, slot / "Scripts" / "python.exe")
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": "1.2.3", "completed_at": "2026-01-01T00:00:00Z", "pid": 1}),
            encoding="utf-8",
        )
        (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    marker = tmp_path / "selector-identities"
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env.update(
        AGENT_BRIDGE_INSTALL_DIR=str(root), AGENT_BRIDGE_CONFIG_DIR=str(root),
        BOOTSTRAP_PROBE_MARKER=str(marker), UV_PYTHON_DOWNLOADS="never",
    )
    if selection == "uv":
        directory = tmp_path / "uv"
        directory.mkdir()
        shutil.copyfile(selector_probe, directory / "uv.exe")
        env["PATH"] = str(directory) + os.pathsep + env["PATH"]
    command = [
        host, "-NoProfile", "-NonInteractive", "-File",
        str(plugin / "scripts" / "installation-context" / "installation-context.ps1"),
        "-Action", "status", "-PayloadRoot", str(plugin), "-PluginId", "agent-bridge",
        "-LegacyRoot", str(root),
    ]
    process, outer = spawn_sync_in_kill_on_close_job(
        command, env=env, cwd=tmp_path, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert outer is not None
    kernel = _kernel()
    handles: list[int] = []
    try:
        handles = _pin_workers(marker, process, kernel)
        process.kill()
        process.communicate(timeout=5)
        # The fixture's enclosing Job stays open throughout these observations.
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        outer.terminate_and_wait(3)
        process.wait(timeout=3)
        outer.close()
        for handle in handles:
            assert kernel.CloseHandle(handle)


def test_unowned_capture_control_survives_caller_death(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    selector_probe: Path, tmp_path: Path,
) -> None:
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which("pwsh")
    if host is None:
        pytest.skip("pwsh is unavailable")
    marker = tmp_path / "selector-identities"
    script = tmp_path / "unowned.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
. $env:BOOTSTRAP_ORIGINAL_CAPTURE
$diagnostic=''
$null=Invoke-Hidden -FilePath $env:BOOTSTRAP_PROBE -TimeoutMilliseconds 30000 `
    -CleanupMilliseconds 3000 -ErrorOutput ([ref]$diagnostic)
""",
        encoding="utf-8",
    )
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env.update(
        BOOTSTRAP_ORIGINAL_CAPTURE=str(plugin / "scripts" / "versioned_runtime_native.ps1"),
        BOOTSTRAP_PROBE=str(selector_probe), BOOTSTRAP_PROBE_MARKER=str(marker),
    )
    process, outer = spawn_sync_in_kill_on_close_job(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, cwd=tmp_path, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert outer is not None
    kernel = _kernel()
    handles: list[int] = []
    try:
        handles = _pin_workers(marker, process, kernel)
        process.kill()
        process.wait(timeout=5)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 100) == 258
    finally:
        outer.terminate_and_wait(3)
        process.communicate(timeout=3)
        outer.close()
        for handle in handles:
            assert kernel.CloseHandle(handle)


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_completed_capture_does_not_pin_asset_or_reload_retirement(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path, shell: str,
) -> None:
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    capture = tmp_path / "capture"
    capture.mkdir()
    for name in ("bootstrap-capture.ps1", "bootstrap-capture.exe"):
        shutil.copyfile(plugin / "scripts" / name, capture / name)
    completed = tmp_path / "caller-survived"
    script = tmp_path / "replace.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
. $env:BOOTSTRAP_CAPTURE_SCRIPT
$asset=Join-Path (Split-Path $env:BOOTSTRAP_CAPTURE_SCRIPT) 'bootstrap-capture.exe'
$bytes=[IO.File]::ReadAllBytes($asset)
for ($cycle=0; $cycle -lt 2; $cycle++) {
    $result=Invoke-BootstrapOwnedCapture -FilePath $env:BOOTSTRAP_TARGET `
        -ArgList @('-NoProfile','-NonInteractive','-Command',"[Console]::Write('selected')") `
        -TimeoutMilliseconds 10000 -CleanupMilliseconds 3000
    if ($result.ExitCode -ne 0 -or $result.Stdout -cne 'selected') {
        throw 'Bootstrap capture result changed.'
    }
    $assemblies=@([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object {
        $null -ne $_.GetType('CopilotExtensions.BootstrapCapture.JobRetirement',$false)
    })
    if ($assemblies.Count -ne 1) { throw 'Bootstrap retirement assembly was reloaded.' }
    [IO.File]::Delete($asset)
    if ($cycle -eq 0) { [IO.File]::WriteAllBytes($asset,$bytes) }
}
[IO.File]::WriteAllText($env:BOOTSTRAP_CALLER_SURVIVED,'alive')
Start-Sleep -Seconds 30
""",
        encoding="utf-8",
    )
    env = dict(base_env)
    env.update(
        BOOTSTRAP_CAPTURE_SCRIPT=str(capture / "bootstrap-capture.ps1"),
        BOOTSTRAP_TARGET=host, BOOTSTRAP_CALLER_SURVIVED=str(completed),
    )
    process, outer = spawn_sync_in_kill_on_close_job(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, cwd=tmp_path, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert outer is not None
    try:
        deadline = time.monotonic() + 20
        while not completed.is_file():
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                pytest.fail(f"Caller ended before asset replacement: {stdout!r} {stderr!r}")
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert process.poll() is None
        assert not (capture / "bootstrap-capture.exe").exists()
    finally:
        outer.terminate_and_wait(3)
        process.wait(timeout=3)
        outer.close()


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_selector_timeout_retires_descendants_while_caller_stays_alive(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    selector_probe: Path, tmp_path: Path, shell: str,
) -> None:
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    marker = tmp_path / "selector-identities"
    completed = tmp_path / "caller-survived"
    script = tmp_path / "timeout.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
. $env:BOOTSTRAP_CAPTURE_SCRIPT
try {
    $null=Invoke-BootstrapOwnedCapture -FilePath $env:BOOTSTRAP_PROBE `
        -TimeoutMilliseconds 8000 -CleanupMilliseconds 3000
    throw 'selector unexpectedly completed'
} catch [TimeoutException] {
    [IO.File]::WriteAllText($env:BOOTSTRAP_CALLER_SURVIVED,'alive')
}
Start-Sleep -Seconds 30
""",
        encoding="utf-8",
    )
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env.update(
        BOOTSTRAP_CAPTURE_SCRIPT=str(plugin / "scripts" / "bootstrap-capture.ps1"),
        BOOTSTRAP_PROBE=str(selector_probe), BOOTSTRAP_PROBE_MARKER=str(marker),
        BOOTSTRAP_CALLER_SURVIVED=str(completed),
    )
    process, outer = spawn_sync_in_kill_on_close_job(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, cwd=tmp_path, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert outer is not None
    kernel = _kernel()
    handles: list[int] = []
    try:
        handles = _pin_workers(marker, process, kernel)
        deadline = time.monotonic() + 12
        while not completed.is_file():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 0) == 0
        assert process.poll() is None
    finally:
        outer.terminate_and_wait(3)
        process.wait(timeout=3)
        outer.close()
        for handle in handles:
            assert kernel.CloseHandle(handle)
