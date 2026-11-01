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
    published = marker.with_suffix(".published")
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
        + f"Path({str(published)!r}).touch()\n"
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
assert.equal(result.permissionDecision, undefined);
assert.match(result.additionalContext, /policy-process-timeout/);
assert.deepEqual(reports, ["policy-process-timeout"]);
console.log("policy timeout reported after its owned scope was retired");
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
    crt = ctypes.CDLL("ucrtbase")
    crt._get_doserrno.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
    crt._get_doserrno.restype = ctypes.c_int
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
        # The renamed path can be visible before Windows closes its rename handle.
        while not published.is_file():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "policy descendant never started"
            time.sleep(0.02)
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except OSError as error:
            native_error = ctypes.GetLastError()
            dos_error = ctypes.c_ulong()
            crt_result = crt._get_doserrno(ctypes.byref(dos_error))
            error.add_note(json.dumps({
                "phase": "first-marker-read-before-pin",
                "consumer": consumer, "termination": termination,
                "errno": error.errno, "winerror": getattr(error, "winerror", None),
                "native_error": native_error, "crt_result": crt_result,
                "dos_error": dos_error.value,
                "caller_returncode": process.poll(),
                "owned_job_handle": bool(job.handle),
                "prepared_exists": marker.with_suffix(".prepared").exists(),
            }))
            try:
                error.add_note(f"file_stat={tuple(marker.stat())!r}")
            except OSError as inspection_error:
                error.add_note(f"file_stat_unavailable={inspection_error.errno}")
            raise
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
                decision = json.loads(stdout)
                assert "permissionDecision" not in decision
                assert "policy worker execution" in decision["additionalContext"]
            assert time.monotonic() - started < 30, "policy exceeded the host hook deadline"
        assert retired(), "policy descendant survived its invocation/caller"
    finally:
        if close_observer is not None:
            close_observer()
        job.terminate_and_wait(5)
        process.wait(timeout=5)


@pytest.mark.parametrize("consumer", ["powershell.exe", "pwsh"])
def test_legacy_venv_redirector_does_not_outlive_original_caller(tmp_path, consumer):
    from agent_procutil._daemon_instance_identity import _windows_birth

    interpreter = Path(sys.prefix) / "Scripts" / "python.exe"
    assert interpreter.is_file() and (interpreter.parent.parent / "pyvenv.cfg").is_file()
    shell = shutil.which(consumer)
    assert shell
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("hook_policy_process.py", "hook_process_identity.py"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    nonce = uuid.uuid4().hex
    (scripts / "hook_client.py").write_text("""
import importlib.util,json,os,subprocess,sys,time
from pathlib import Path
def _load_sibling(name):
    spec=importlib.util.spec_from_file_location('fixture_identity',Path(__file__).with_name(name))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
if __name__=='__main__':
    from agent_procutil import direct_python,no_window_kwargs
    from agent_procutil._daemon_instance_identity import _windows_birth
    python,overrides=direct_python(sys.executable)
    environment=dict(os.environ);environment.update(overrides)
    child=subprocess.Popen([python,'-c','import time;time.sleep(60)'],env=environment,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**no_window_kwargs())
    marker=Path(os.environ['LEGACY_NATIVE_ROOT'])/'lookup.json'
    marker.with_suffix('.prepared').write_text(json.dumps({
        'pid':os.getpid(),'birth':_windows_birth(__import__('ctypes').WinDLL('kernel32').GetCurrentProcess()),
        'child':child.pid,'child_birth':_windows_birth(int(child._handle)),
        'nonce':os.environ['LEGACY_NATIVE_NONCE']}))
    marker.with_suffix('.prepared').replace(marker)
    marker.with_suffix('.published').touch()
    time.sleep(60)
""", encoding="utf-8")
    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text("""
import ctypes,json,os,sys,time
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from agent_procutil import owned_process,_windows_caller_owned
from agent_procutil._daemon_instance_identity import _windows_birth
import hook_policy_process as policy
import hook_process_identity as identity
root=Path(os.environ['LEGACY_NATIVE_ROOT'])
def refuse_confirmation(job,timeout):
    marker=root/'pending.json'
    marker.with_suffix('.prepared').write_text(json.dumps({
        'pid':os.getpid(),'birth':_windows_birth(ctypes.WinDLL('kernel32').GetCurrentProcess()),
        'launcher':os.getppid(),'launcher_birth':'windows:'+identity.process_start_time(os.getppid()),
        'nonce':os.environ['LEGACY_NATIVE_NONCE']}))
    marker.with_suffix('.prepared').replace(marker)
    marker.with_suffix('.published').touch()
    raise OSError('fixture unconfirmed native retirement')
_windows_caller_owned._retire_job=refuse_confirmation
real_run=owned_process.run_caller_owned
def observe_settlement(*args,**kwargs):
    original=kwargs['on_settled']
    def settled():
        (root/'settled').touch();original()
    kwargs['on_settled']=settled
    return real_run(*args,**kwargs)
owned_process.run_caller_owned=observe_settlement
policy.execute(b'{}',legacy=True,settlement_deadline=time.monotonic()+2,
    initial_decision={'permissionDecision':'ask','permissionDecisionReason':'confirmed fixture'})
raise AssertionError('unconfirmed cleanup must not return a decision')
""", encoding="utf-8")
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(
        "param($Python,$Supervisor,$Source,$Scripts)\n"
        "& $Python -I $Supervisor $Source $Scripts\nexit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    source = PLUGIN.parents[1] / "libs" / "agent-procutil" / "src"
    environment = dict(os.environ, LEGACY_NATIVE_ROOT=str(tmp_path), LEGACY_NATIVE_NONCE=nonce)
    environment["PYTHONPATH"] = str(source) + os.pathsep + environment.get("PYTHONPATH", "")
    process, job = spawn_sync_in_kill_on_close_job(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(wrapper),
         str(interpreter), str(supervisor), str(source), str(scripts)],
        require_job=True, allow_breakaway=False, env=environment, cwd=tmp_path,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    observers = []
    try:
        observers.append(_pin_descendant(process.pid, _windows_birth(int(process._handle))))
        records = {}
        deadline = time.monotonic() + 10
        for name in ("lookup", "pending"):
            marker = tmp_path / (name + ".json")
            while not marker.with_suffix(".published").is_file():
                assert process.poll() is None, process.communicate(timeout=5)
                assert time.monotonic() < deadline, "legacy native custody witness never published"
                time.sleep(.02)
            record = records[name] = json.loads(marker.read_text(encoding="utf-8"))
            assert record["nonce"] == nonce
            observers.append(_pin_descendant(record["pid"], record["birth"]))
            if name == "lookup":
                observers.append(_pin_descendant(record["child"], record["child_birth"]))
        assert records["pending"]["launcher"] != process.pid, "fixture bypassed the real venv redirector"
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, records["pending"]["launcher"])
        assert handle
        try:
            image, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            assert kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))
            assert os.path.samefile(image.value, interpreter)
        finally:
            assert kernel.CloseHandle(handle)
        observers.append(_pin_descendant(
            records["pending"]["launcher"], records["pending"]["launcher_birth"],
        ))
        assert not (tmp_path / "settled").exists()
        process.kill()
        for retired, _ in observers:
            assert retired(), "legacy supervisor or descendant survived the original shell"
        assert not (tmp_path / "settled").exists(), "caller exit falsely attested settlement"
        process.communicate(timeout=5)
    finally:
        for _, close in observers:
            close()
        job.terminate_and_wait(5)
        process.wait(timeout=5)
