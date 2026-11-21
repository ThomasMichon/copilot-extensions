"""Managed selection and native capture deadlines at the PowerShell cue entry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
import pytest

from cue_fixture import PLUGIN, managed_cue_environment, prepare_cue_assets


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
def test_powershell_writer_ignores_path_python_and_foreign_venv(tmp_path: Path, host: str) -> None:
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    scripts = tmp_path / "venv/Scripts"
    scripts.mkdir(parents=True)
    for name in ("python", "python3", "python.exe", "python3.exe", "py.exe"):
        path = scripts / name
        path.write_bytes(b"not-a-launchable-interpreter")
        path.chmod(0o700)
    (scripts.parent / "pyvenv.cfg").write_text("home = invalid-foreign-python\n", encoding="utf-8")
    payload = tmp_path / "payload"
    shutil.copytree(PLUGIN / "src", payload / "src", ignore=shutil.ignore_patterns("__pycache__"))
    prepare_cue_assets(payload)
    for name in ("write_session_guidance.py", "write-session-guidance.ps1"):
        shutil.copy2(PLUGIN / "scripts" / name, payload / "scripts")
    shutil.copy2(PLUGIN / "plugin.json", payload / "plugin.json")
    original = payload / "scripts/actual_cue.py"
    (payload / "scripts/write_session_guidance.py").rename(original)
    identity = tmp_path / "writer-identity.json"
    (payload / "scripts/write_session_guidance.py").write_text(
        "import json,os,runpy,sys\nfrom pathlib import Path\n"
        "assert os.getppid() == int(os.environ['CUE_PARENT'])\n"
        "assert not any(k in os.environ for k in ('VIRTUAL_ENV','UV_PYTHON','UV_NO_MANAGED_PYTHON','UV_WORKING_DIR'))\n"
        "window = None\n"
        "if os.name == 'nt':\n"
        "    import ctypes\n"
        "    window = int(ctypes.windll.kernel32.GetConsoleWindow())\n"
        "Path(os.environ['CUE_IDENTITY']).write_text(json.dumps({"
        "'version': list(sys.version_info[:3]), 'window': window}))\n"
        "runpy.run_path(os.environ['CUE_ORIGINAL'], run_name='__main__')\n",
    )
    entry = tmp_path / "observed-cue.ps1"
    entry.write_text(
        "$env:CUE_PARENT = [string]$PID\n"
        "& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts/write-session-guidance.ps1')\n",
    )
    env = {
        **managed_cue_environment(), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "COPILOT_PLUGIN_ROOT": str(payload), "VIRTUAL_ENV": str(scripts.parent),
        "UV_PYTHON": "foreign", "UV_NO_MANAGED_PYTHON": "1",
        "UV_WORKING_DIR": str(tmp_path / "not-a-working-directory"),
        "BUDGET_GUIDANCE_CONFIG": str(tmp_path / "missing.json"),
        "PATH": str(scripts) + os.pathsep + os.environ.get("PATH", ""),
        "CUE_IDENTITY": str(identity), "CUE_ORIGINAL": str(original),
    }
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(entry)],
        input=json.dumps({"sessionId": "test-session", "cwd": str(tmp_path)}),
        text=True, capture_output=True, env=env, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"
    target = tmp_path / ".copilot/session-state/test-session/instructions/budget-guidance/session-guidance.instructions.md"
    assert json.loads(target.read_text().split("\n", 1)[1])["error"] == "configuration not found"
    observed = json.loads(identity.read_text())
    declared = json.loads((PLUGIN / "payload-invocation.json").read_bytes())["bootstrapPython"]
    assert observed["version"] == list(map(int, declared.split(".")))
    assert observed["window"] == (0 if os.name == "nt" else None)


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
def test_powershell_writer_bounds_maximum_stdin_delivery_to_stalled_child(tmp_path: Path, host: str) -> None:
    shell = shutil.which(host)
    if shell is None:
        pytest.skip("PowerShell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    prepare_cue_assets(payload)
    shutil.copy2(PLUGIN / "scripts/write-session-guidance.ps1", scripts)
    (scripts / "write_session_guidance.py").write_text(
        "import time\ntime.sleep(20)\n", encoding="utf-8",
    )
    env = {**managed_cue_environment(), "COPILOT_PLUGIN_ROOT": str(payload)}
    started = time.monotonic()
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(scripts / "write-session-guidance.ps1")],
        input="x" * 65536, text=True, capture_output=True, env=env,
        timeout=9, **no_window_kwargs(),
    )
    assert 5 <= time.monotonic() - started < 9
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert result.stderr.strip() == "budget-guidance: session cue could not be written"


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("prerequisite", ["uv", "managed-python", "request"])
def test_powershell_writer_missing_prerequisite_never_uses_path_python(
    tmp_path: Path, host: str, prerequisite: str,
) -> None:
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    payload = tmp_path / "payload"
    prepare_cue_assets(payload)
    shutil.copy2(PLUGIN / "scripts/write-session-guidance.ps1", payload / "scripts")
    marker = tmp_path / "must-not-run"
    (payload / "scripts/write_session_guidance.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
    )
    env = {**managed_cue_environment(), "COPILOT_PLUGIN_ROOT": str(payload)}
    entry = payload / "scripts/write-session-guidance.ps1"
    if prerequisite == "uv":
        empty = tmp_path / "empty-path"
        empty.mkdir()
        env["CUE_EMPTY_PATH"] = str(empty)
        entry = tmp_path / "missing-uv.ps1"
        entry.write_text(
            "$env:PATH = $env:CUE_EMPTY_PATH\n"
            "& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts/write-session-guidance.ps1')\n",
        )
    elif prerequisite == "managed-python":
        env["UV_PYTHON_INSTALL_DIR"] = str(tmp_path / "unprepared-managed-store")
    else:
        (payload / "scripts/bootstrap-python-version.txt").unlink()
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(entry)],
        input="{}", text=True, capture_output=True, env=env, timeout=9, **no_window_kwargs(),
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert result.stderr.strip() == (
        "budget-guidance: managed cue prerequisites are unavailable; "
        "run the owning scripts/install.ps1 install"
    )
    assert not marker.exists()
    assert not (tmp_path / "unprepared-managed-store").exists()


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
def test_powershell_selector_capture_shares_the_writer_deadline(tmp_path: Path, host: str) -> None:
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    payload = tmp_path / "payload"
    prepare_cue_assets(payload)
    scripts = payload / "scripts"
    shutil.copy2(PLUGIN / "scripts/write-session-guidance.ps1", scripts)
    writer = tmp_path / "writer-started"
    (scripts / "write_session_guidance.py").write_text(
        f"from pathlib import Path\nPath({str(writer)!r}).touch()\n",
    )
    phases = tmp_path / "selection-phases"
    workload = (
        "import os,time; from pathlib import Path; "
        "p=Path(os.environ['CUE_PHASES']); "
        "p.write_text(p.read_text()+'started\\n' if p.exists() else 'started\\n'); "
        "time.sleep(3); p.write_text(p.read_text()+'completed\\n'); print('completed')"
    )
    # A direct, descendant-free surrogate isolates the native capture clock.
    # Real uv/version/request/environment selection is exercised separately.
    (scripts / "bootstrap-uv.ps1").write_text(
        "function Resolve-UvBootstrapPython {\n"
        "param($RequestFile, $Operation, [switch]$IsolateSelection, $InvokeCaptured)\n"
        "foreach ($stage in @(1, 2)) {\n"
        "  $null = & $InvokeCaptured -FilePath $env:CUE_PYTHON "
        "-ArgList @('-I', '-S', '-c', $env:CUE_WORKLOAD) -Environment @{}\n"
        "}\nthrow 'two separate deadlines were incorrectly allowed'\n}\n",
    )
    env = {
        **os.environ, "COPILOT_PLUGIN_ROOT": str(payload),
        "CUE_PYTHON": str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
        "CUE_PHASES": str(phases), "CUE_WORKLOAD": workload,
    }
    started = time.monotonic()
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(scripts / "write-session-guidance.ps1")],
        input="{}", text=True, capture_output=True, env=env, timeout=9, **no_window_kwargs(),
    )
    assert 5 <= time.monotonic() - started < 9
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert result.stderr.strip() == "budget-guidance: session cue could not be written"
    assert phases.read_text().splitlines() == ["started", "completed", "started"]
    assert not writer.exists()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows cold-cache uv descendant")
@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("outcome", ["complete", "timeout"])
def test_powershell_selector_cold_query_has_no_console_and_settles_before_outer_cleanup(
    tmp_path: Path, host: str, outcome: str,
) -> None:
    import ctypes
    from ctypes import wintypes

    shell, uv = shutil.which(host), shutil.which("uv")
    if shell is None or uv is None:
        pytest.skip(f"{host} and already-prepared uv required")
    environment = managed_cue_environment()
    request = json.loads((PLUGIN / "payload-invocation.json").read_bytes())["bootstrapPython"]
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", f"cpython@{request}"],
        env=environment, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert selected.returncode == 0, selected.stderr
    source = Path(selected.stdout.strip()).resolve()
    managed = tmp_path / "managed"
    private = managed / source.parent.name
    shutil.copytree(source.parent, private)
    record, release = tmp_path / "query.json", tmp_path / "release-query"
    # Instrument only a private managed runtime. The real uv query uses -I but
    # still imports its own sitecustomize; the cue writer deliberately uses -S.
    sitecustomize = private / "Lib/site-packages/sitecustomize.py"
    sitecustomize.parent.mkdir(parents=True, exist_ok=True)
    sitecustomize.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "from windows_external_reader_fixture import _record\n"
        f"_record(Path({str(record)!r}))\n"
        "deadline = time.monotonic() + 20\n"
        f"while not Path({str(release)!r}).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )
    payload = tmp_path / "payload"
    prepare_cue_assets(payload)
    scripts = payload / "scripts"
    shutil.copy2(PLUGIN / "scripts/write-session-guidance.ps1", scripts)
    writer = tmp_path / "writer.json"
    (scripts / "write_session_guidance.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"Path({str(writer)!r}).write_text(json.dumps({{'executable':sys.executable}}))\n"
        "print('{}', end='')\n",
        encoding="utf-8",
    )
    environment.update(
        COPILOT_PLUGIN_ROOT=str(payload), UV_PYTHON_INSTALL_DIR=str(managed),
        UV_CACHE_DIR=str(tmp_path / "cold-cache"),
    )
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    handles = []
    started = time.monotonic()
    process, job = spawn_sync_in_kill_on_close_job(
        [shell, "-NoProfile", "-File", str(scripts / "write-session-guidance.ps1")],
        env=environment, cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, require_job=True, allow_breakaway=False, **no_window_kwargs(),
    )
    assert job is not None
    try:
        deadline = started + 4
        while not record.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert record.exists(), "a genuinely cold uv metadata query was not observed"
        query = json.loads(record.read_text())
        births = []
        for pid, image in ((query["pid"], private / source.name), (query["parent"], Path(uv))):
            handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
            assert handle, "cannot pin the original query/uv process"
            handles.append(handle)
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            assert kernel.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            )
            births.append((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
            buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            assert kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length))
            assert Path(buffer.value).resolve() == image.resolve()
        assert births[0] == query["birth"] and births[1] <= births[0]
        if outcome == "complete":
            release.touch()
        stdout, stderr = process.communicate(b"{}", timeout=max(1, started + 9 - time.monotonic()))
        assert process.returncode == 0 and stdout == b"{}", (stdout, stderr)
        # This assertion precedes the fixture Job's safety cleanup. Otherwise
        # outer containment would conceal a leaked query in the hook boundary.
        assert all(kernel.WaitForSingleObject(handle, 0) == 0 for handle in handles), (
            "uv/query survived the cue's own completion", outcome, query,
        )
        assert query["console"] == 0 and not query["visible"], query
        if outcome == "complete":
            assert stderr == b""
            assert Path(json.loads(writer.read_text())["executable"]).resolve() == private / source.name
        else:
            assert time.monotonic() - started < 9
            assert stderr.strip() == b"budget-guidance: session cue could not be written"
            assert not writer.exists()
    finally:
        job.terminate_and_wait(5)
        process.wait(timeout=5)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 0) == 0, "fixture cleanup did not settle owned process"
            kernel.CloseHandle(handle)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
