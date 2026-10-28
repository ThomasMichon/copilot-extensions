"""Explicit native qualification of the resident policy's caller-owned lifetime."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from agent_procutil import spawn_sync_in_kill_on_close_job

PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_NATIVE_RECOVERY") != "1" or os.name != "nt",
    reason="explicit contained Windows-handle policy lifetime qualification",
)


def _pin_descendant(pid: int, expected_birth: str):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    assert handle, ctypes.get_last_error()
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        assert kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ), ctypes.get_last_error()
        birth = created.dwLowDateTime | created.dwHighDateTime << 32
        assert expected_birth == f"windows:{birth}"
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return (
        lambda: kernel.WaitForSingleObject(handle, 5000) == 0,
        lambda: kernel.CloseHandle(handle),
    )


@pytest.mark.parametrize("termination", ["timeout", "caller-exit"])
@pytest.mark.parametrize("consumer", ["resident", "powershell.exe", "pwsh"])
def test_policy_scope_retires_descendant(tmp_path, termination, consumer):
    node = shutil.which("node")
    assert node
    interpreter = os.environ.get("COPILOT_TEST_HOOK_PRE_PYTHON", sys.executable)
    assert Path(interpreter).is_absolute() and Path(interpreter).is_file()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for path in (PLUGIN / "scripts").glob("*.py"):
        shutil.copyfile(path, scripts / path.name)
    shutil.copyfile(
        PLUGIN / "scripts" / "hook-read-policy.json", scripts / "hook-read-policy.json"
    )
    if consumer != "resident":
        for name in ("pre-tool-hook.ps1", "versioned_runtime_native.ps1"):
            shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
        (scripts / "resolve-runtime.ps1").write_text(
            "param([string]$TraceMode)\n$AwPy = '"
            + interpreter.replace("'", "''") + "'\n",
            encoding="utf-8-sig",
        )
    home = tmp_path / "home"
    home.mkdir()
    marker = tmp_path / "descendant.json"
    nonce = uuid.uuid4().hex
    (scripts / "hook_client.py").write_text(
        "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
        "from agent_procutil import direct_python,no_window_kwargs\n"
        "python,overrides=direct_python(sys.executable)\n"
        "env=dict(os.environ);env.update(overrides)\n"
        "p=subprocess.Popen([python,'-c','import time;time.sleep(120)'],env=env,"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**no_window_kwargs())\n"
        "birth=''\n"
        "if os.name=='nt':\n"
        " from agent_procutil._daemon_instance_identity import _windows_birth\n"
        " birth=_windows_birth(int(p._handle))\n"
        + f"marker=Path({str(marker)!r});prepared=marker.with_suffix('.prepared')\n"
        + f"prepared.write_text(json.dumps({{'pid':p.pid,'birth':birth,'nonce':{nonce!r}}}),encoding='utf-8');prepared.replace(marker)\n"
        + "time.sleep(120)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        """
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
const [modulePath, root, python, cwd] = process.argv.slice(2);
const { createPythonPolicy, createResidentPreToolHook } = await import(pathToFileURL(modulePath));
const reports = [];
const hook = createResidentPreToolHook({
  scriptsRoot: root, invokePolicy: createPythonPolicy({ scriptsRoot: root, python }),
  report: reason => reports.push(reason),
});
const result = await hook({ workingDirectory: cwd, sessionId: "lifetime-fixture",
  toolName: "edit", toolArgs: {}, timestamp: new Date() });
assert.equal(result.permissionDecision, "deny");
assert.deepEqual(reports, ["policy-process-timeout"]);
console.log("policy timeout denied after its owned scope was retired");
""",
        encoding="utf-8",
    )
    module = PLUGIN / "extensions" / "agent-worktrees" / "resident-pre-tool.mjs"
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment["PRETOOL_KILLSWITCH_STATE_FILE"] = str(tmp_path / "absent-stop.json")
    if consumer == "resident":
        command = [node, str(driver), str(module), str(scripts), interpreter, str(home)]
    else:
        shell = shutil.which(consumer)
        assert shell
        command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(scripts / "pre-tool-hook.ps1")]
        ambient = tmp_path / "ambient"
        ambient.mkdir()
        (ambient / "python.cmd").write_text("@exit /b 92\n", encoding="utf-8")
        environment["PATH"] = str(ambient) + os.pathsep + environment.get("PATH", "")
    started = time.monotonic()
    process, job = spawn_sync_in_kill_on_close_job(
        command,
        require_job=True,
        allow_breakaway=False,
        env=environment,
        cwd=home,
        stdin=subprocess.DEVNULL if consumer == "resident" else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    close_observer = None
    try:
        if consumer != "resident":
            process.stdin.write(b'{"toolName":"edit","toolArgs":{}}')
            process.stdin.close()
            process.stdin = None
        deadline = time.monotonic() + 15
        while not marker.is_file():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "policy descendant never started"
            time.sleep(0.02)
        record = json.loads(marker.read_text(encoding="utf-8"))
        assert record["nonce"] == nonce
        retired, close_observer = _pin_descendant(record["pid"], record["birth"])
        if termination == "caller-exit":
            process.kill()
            assert retired(), "policy descendant survived its caller"
            process.communicate(timeout=10)
        else:
            stdout, stderr = process.communicate(timeout=40)
            assert process.returncode == 0, (stdout, stderr)
            if consumer != "resident":
                assert json.loads(stdout)["permissionDecision"] == "deny"
            assert time.monotonic() - started < 30, "policy exceeded the host hook deadline"
        assert retired(), "policy descendant survived its invocation/caller"
    finally:
        if close_observer is not None:
            close_observer()
        job.terminate_and_wait(5)
        process.wait(timeout=5)
