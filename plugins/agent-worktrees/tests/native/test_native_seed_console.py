"""Real Windows terminal signals and pane Job ownership, without a desktop window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

from agent_procutil.owned_process import run_caller_owned

import test_native_seed_runtime as runtime_fixture

installed_native_runtime = runtime_fixture.installed_native_runtime
native_runtime_source = runtime_fixture.native_runtime_source

pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native Windows required"),
]


class _Coord(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


class _Startup(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
        *[(name, wintypes.DWORD) for name in (
            "x", "y", "x_size", "y_size", "x_count", "y_count", "fill", "flags",
        )],
        ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.POINTER(ctypes.c_byte)),
        ("stdin", wintypes.HANDLE), ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE),
    ]


class _StartupEx(ctypes.Structure):
    _fields_ = [("startup", _Startup), ("attributes", ctypes.c_void_p)]


class _Process(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD), ("tid", wintypes.DWORD),
    ]


class _Console:
    def __init__(self, command, cwd):
        self.cwd = Path(cwd)
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreatePipe": ([ctypes.POINTER(wintypes.HANDLE)] * 2 + [ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "CreatePseudoConsole": ([_Coord, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.HANDLE)], ctypes.c_long),
            "InitializeProcThreadAttributeList": (
                [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                 ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL,
            ),
            "CreateProcessW": (
                [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                 wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupEx),
                 ctypes.POINTER(_Process)], wintypes.BOOL,
            ),
            "ReadFile": ([wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL),
            "WriteFile": ([wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetStdHandle": ([wintypes.DWORD], wintypes.HANDLE),
            "SetStdHandle": ([wintypes.DWORD, wintypes.HANDLE], wintypes.BOOL),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "ClosePseudoConsole": ([wintypes.HANDLE], None),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handles = []
        self.console = wintypes.HANDLE()
        self.process = _Process()
        self.attributes = None
        self.output = bytearray()
        self.reader = None
        try:
            input_read, self.input = self._pipe()
            self.output_read, output_write = self._pipe()
            result = self.api.CreatePseudoConsole(
                _Coord(100, 30), input_read, output_write, 0, ctypes.byref(self.console),
            )
            if result:
                raise OSError(f"CreatePseudoConsole failed: {result}")
            size = ctypes.c_size_t()
            self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            self.attributes = ctypes.create_string_buffer(size.value)
            self._check(self.api.InitializeProcThreadAttributeList(self.attributes, 1, 0, ctypes.byref(size)))
            self._check(self.api.UpdateProcThreadAttribute(
                self.attributes, 0, 0x20016, self.console, ctypes.sizeof(wintypes.HANDLE), None, None,
            ))
            startup = _StartupEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.attributes = ctypes.cast(self.attributes, ctypes.c_void_p)
            # ConPTY supplies the terminal; no new desktop console or Job escape.
            # Pytest capture handles must not become this fresh terminal's stdio.
            previous = {index: self.api.GetStdHandle(index) for index in (-10, -11, -12)}
            try:
                for index in previous:
                    self._check(self.api.SetStdHandle(index, None))
                self._check(self.api.CreateProcessW(
                    command[0], ctypes.create_unicode_buffer(subprocess.list2cmdline(command)), None, None,
                    False, 0x80000, None, str(cwd), ctypes.byref(startup), ctypes.byref(self.process),
                ))
            finally:
                for index, handle in previous.items():
                    self.api.SetStdHandle(index, handle)
            self.handles.extend([self.process.process, self.process.thread])
            self.api.CloseHandle(input_read)
            self.api.CloseHandle(output_write)
            self.handles.remove(input_read)
            self.handles.remove(output_write)
            self.reader = threading.Thread(target=self._drain, daemon=True)
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def _pipe(self):
        read, write = wintypes.HANDLE(), wintypes.HANDLE()
        self._check(self.api.CreatePipe(ctypes.byref(read), ctypes.byref(write), None, 0))
        self.handles.extend([read, write])
        return read, write

    def _drain(self):
        buffer = ctypes.create_string_buffer(4096)
        count = wintypes.DWORD()
        while self.api.ReadFile(self.output_read, buffer, len(buffer), ctypes.byref(count), None) and count.value:
            self.output.extend(buffer.raw[:count.value])

    def interrupt(self):
        # Broadcast in this owned ConPTY, not the test runner's different console.
        script = """
import ctypes,sys,time
from ctypes import wintypes
api=ctypes.WinDLL('kernel32',use_last_error=True)
api.AttachConsole.argtypes=[wintypes.DWORD]
api.SetConsoleCtrlHandler.argtypes=[ctypes.c_void_p,wintypes.BOOL]
api.GenerateConsoleCtrlEvent.argtypes=[wintypes.DWORD,wintypes.DWORD]
api.FreeConsole()
assert api.AttachConsole(int(sys.argv[1])), ctypes.get_last_error()
assert api.SetConsoleCtrlHandler(None,True), ctypes.get_last_error()
assert api.GenerateConsoleCtrlEvent(0,0), ctypes.get_last_error()
time.sleep(.05)
api.FreeConsole()
"""
        result = run_caller_owned(
            [sys.executable, "-I", "-c", script, str(self.process.pid)],
            timeout=10, max_stdout_bytes=1024, max_stderr_bytes=4096,
        )
        assert result.returncode == 0, result.stderr

    def exited(self):
        return self.api.WaitForSingleObject(self.process.process, 0) == 0

    def close(self):
        if self.process.process and not self.exited():
            self.api.TerminateProcess(self.process.process, 99)
            self.api.WaitForSingleObject(self.process.process, 5000)
        if self.console:
            self.api.ClosePseudoConsole(self.console)
            self.console = wintypes.HANDLE()
        if self.reader:
            self.reader.join(5)
        if self.attributes:
            self.api.DeleteProcThreadAttributeList(self.attributes)
        for handle in reversed(self.handles):
            self.api.CloseHandle(handle)
        self.handles = []


def _literal(path):
    return "'" + str(path).replace("'", "''") + "'"


def _wait(predicate, console, reason, *, allow_exited=False, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if console.exited() and not allow_exited:
            break
        time.sleep(0.02)
    pytest.fail(
        reason + f" (owner_exited={console.exited()})\n"
        + console.output.decode("utf-8", errors="replace")[-8000:]
        + "\n" + "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                console.cwd / "owner-probe.json", console.cwd / "console-probe.json", console.cwd / "interrupt",
            )
            if path.is_file()
        ),
    )


@pytest.mark.timeout(60)
@pytest.mark.parametrize("operation", ["interrupt", "repeat-interrupt", "kill-owner"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_native_terminal_keeps_child_decision_and_pane_job(tmp_path, monkeypatch, operation, wrapped):
    from agent_worktrees import locks

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    pane = Path(__file__).resolve().parents[4] / "worktree-manager" / "bin" / "pane-wrapper.ps1"
    ready, interrupted, stop = (tmp_path / name for name in ("ready", "interrupt", "stop"))
    returned = tmp_path / "owner-returned"
    backend = tmp_path / "terminal-backend.py"
    backend.write_text(
        "import ctypes,json,os,pathlib,signal,sys,time\n"
        f"ready=pathlib.Path({str(ready)!r})\n"
        # A synthetic backend policy, not Copilot's count or timing contract.
        "hits=[]\n"
        "def on_interrupt(*_):\n"
        "    now=time.monotonic()\n"
        "    hits[:]=[hit for hit in hits if now-hit<10]\n"
        "    hits.append(now)\n"
        f"    pathlib.Path({str(interrupted)!r}).write_text(str(len(hits)))\n"
        "    if len(hits)>=3: sys.exit(130)\n"
        "signal.signal(signal.SIGINT,on_interrupt)\n"
        "api=ctypes.WinDLL('kernel32',use_last_error=True)\n"
        "api.GetStdHandle.restype=ctypes.c_void_p\n"
        "api.GetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]\n"
        "api.SetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.c_ulong]\n"
        "handle=api.GetStdHandle(-10)\nmode=ctypes.c_ulong()\n"
        "confirmed=api.GetConsoleMode(handle,ctypes.byref(mode))\n"
        f"pathlib.Path({str(tmp_path / 'console-probe.json')!r}).write_text(json.dumps("
        "{'cp':api.GetConsoleCP(),'handle':handle,'mode':mode.value,'confirmed':confirmed,"
        "'error':ctypes.get_last_error()}))\n"
        "assert confirmed\n"
        "assert api.SetConsoleMode(handle,(mode.value|1)&~0x200)\n"
        "temporary=ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'tty':sys.stdin.isatty()}))\n"
        "temporary.replace(ready)\n"
        f"while not pathlib.Path({str(stop)!r}).exists(): time.sleep(.02)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "terminal-owner.ps1"
    command = json.dumps([sys.executable, "-I", str(backend)])
    job = (
        "$tokens=$null; $errors=$null\n"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({_literal(pane)},[ref]$tokens,[ref]$errors)\n"
        "$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $n.Name -eq 'Set-AwPaneKillOnCloseJob'},$true)\n"
        ". ([scriptblock]::Create($fn.Extent.Text))\nSet-AwPaneKillOnCloseJob\n"
        "if (-not $script:AwPaneJobHandle) { throw 'Pane Job is required' }\n"
    ) if not wrapped else ""
    driver.write_text(
        f". {_literal(scripts / 'native-seed-boundary.ps1')}\n"
        f"[IO.File]::WriteAllText({_literal(tmp_path / 'owner-probe.json')}, "
        "(@{input_redirected=[Console]::IsInputRedirected;output_redirected=[Console]::IsOutputRedirected} "
        "| ConvertTo-Json -Compress))\n"
        + job +
        "function Invoke-AwNativeSeedPhase {\nparam([string]$Root,[string[]]$Arguments)\n"
        "if ($Arguments[1] -eq 'prepare') {\n"
        f"$argv=ConvertFrom-Json -InputObject '{command.replace(chr(39), chr(39)*2)}'\n"
        "return @{version=1;argv=$argv;environment=$null;attempt_id=('a'*32);resolution_id=('b'*32);"
        "operation_id=('c'*32);parent_id=$null}\n}\nreturn @{recorded=$true}\n}\n"
        f"$code=Invoke-AwNativeSeedBackend -Root {_literal(tmp_path)} -Record {_literal(tmp_path / 'record.yaml')} "
        "-SeedId fixture -Command @('copilot')\n"
        f"[IO.File]::WriteAllText({_literal(returned)}, [string]$code)\nexit $code\n",
        encoding="utf-8",
    )
    owner_command = [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(driver)]
    if wrapped:
        copied = tmp_path / "copied-pane"
        copied.mkdir()
        shutil.copy2(pane, copied / pane.name)
        shutil.copy2(scripts / "native-seed-boundary.ps1", copied / "native-seed-boundary.ps1")
        monkeypatch.setenv("WORKTREE_PANE_MIN_RUNTIME", "0")
        monkeypatch.setenv("WORKTREE_PANE_WAIT_TIMEOUT", "0")
        owner_command = [
            shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(copied / pane.name),
            "-AwManagerRoot", str(tmp_path / "absent-manager"), *owner_command,
        ]
    console = _Console(owner_command, tmp_path)
    child_handle = None
    try:
        _wait(ready.is_file, console, "Terminal backend did not start")
        child = json.loads(ready.read_text(encoding="utf-8"))
        assert child["tty"] is True
        birth = locks.process_start_time(child["pid"])
        assert birth
        child_handle = console.api.OpenProcess(0x100000 | 0x1000, False, child["pid"])
        console._check(child_handle)
        if operation in {"interrupt", "repeat-interrupt"}:
            console.interrupt()
            _wait(interrupted.is_file, console, "Ctrl+C ended the owner instead of cancelling the backend turn")
            assert not console.exited() and console.api.WaitForSingleObject(child_handle, 0) == 258
            assert locks.process_start_time(child["pid"]) == birth
            if operation == "repeat-interrupt":
                for count in (2, 3):
                    console.interrupt()
                    _wait(
                        lambda: interrupted.read_text(encoding="utf-8") == str(count),
                        console, "The backend did not receive repeated Ctrl+C",
                    )
            else:
                stop.write_text("exit", encoding="utf-8")
            _wait(console.exited, console, "Native owner did not return after actual child exit")
            code = wintypes.DWORD()
            console._check(console.api.GetExitCodeProcess(console.process.process, ctypes.byref(code)))
            actual = 130 if operation == "repeat-interrupt" else 7
            assert code.value == (0 if wrapped else actual)
            assert returned.read_text(encoding="utf-8") == str(actual)
            if wrapped and actual == 7:
                _wait(
                    lambda: b"Session exited with code 7" in console.output,
                    console, "The outer wrapper skipped post-child handling",
                    allow_exited=True,
                )
        else:
            console.api.TerminateProcess(console.process.process, 99)
            console.api.WaitForSingleObject(console.process.process, 5000)
            assert console.api.WaitForSingleObject(child_handle, 5000) == 0
    finally:
        console.close()
        if child_handle:
            console.api.CloseHandle(child_handle)


@pytest.mark.timeout(900)
@pytest.mark.parametrize("script_backend", [False, True], ids=["executable", "same-host-script"])
def test_copied_setup_admission_preserves_interrupt_and_actual_return(
    tmp_path, monkeypatch, installed_native_runtime, script_backend,
):
    from agent_worktrees import locks

    root, plugin, prefix, environment = installed_native_runtime
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WORKTREE_PANE_MIN_RUNTIME", "0")
    monkeypatch.setenv("WORKTREE_PANE_WAIT_TIMEOUT", "0")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    initialize = (
        "import json,sys;from pathlib import Path;from uuid import uuid4;"
        "from agent_worktrees import tracking,session_shell,config as cfg;"
        "checkout,source,root=map(Path,sys.argv[1:]);"
        "project=cfg.project_dir('native-admission');project.mkdir(parents=True,exist_ok=True);"
        "(project/'config.yaml').write_text(json.dumps({'repo_name':'native-admission','machine':'fixture',"
        "'repos':{'native-admission':{'anchor':str(checkout),'default_branch':'dev'}}}),encoding='utf-8');"
        f"record=tracking.create_new_record({tmp_path.name!r},'worktree/native-admission',"
        "str(checkout),'native-admission','fixture','win32',checkout);"
        "attempt,resolution=uuid4().hex,uuid4().hex;"
        "shell=session_shell.prepare(record.yaml_path,source,installation_roots=(root,),attempt_id=attempt);"
        "print(json.dumps({'record':str(record.yaml_path),'copied':str(shell.directory),"
        "'attempt':attempt,'resolution':resolution}))"
    )
    prepared = run_caller_owned(
        [*prefix, "-c", initialize, str(checkout), str(plugin / "scripts/default-setup.ps1"), str(root)],
        env=environment, cwd=checkout, timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)
    copied = Path(preparation["copied"])
    ready, interrupted, stop = (tmp_path / name for name in ("ready", "interrupt", "stop"))
    backend = tmp_path / "admission-backend.py"
    backend.write_text(
        "import json,os,pathlib,sys\n"
        f"pathlib.Path({str(checkout / 'console-probe.json')!r}).write_text(json.dumps("
        "{'pid':os.getpid(),'parent':os.getppid(),'cwd':os.getcwd(),'argv':sys.argv}))\n"
        "import ctypes,signal,time\n"
        f"def interrupted(*_): pathlib.Path({str(interrupted)!r}).write_text('received')\n"
        "signal.signal(signal.SIGINT,interrupted)\n"
        "api=ctypes.WinDLL('kernel32',use_last_error=True)\n"
        "api.GetStdHandle.restype=ctypes.c_void_p\n"
        "api.GetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]\n"
        "handle=api.GetStdHandle(-10)\nmode=ctypes.c_ulong()\n"
        "assert api.GetConsoleMode(handle,ctypes.byref(mode))\n"
        f"ready=pathlib.Path({str(ready)!r})\ntemporary=ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid(),"
        "'tty':sys.stdin.isatty(),'cwd':os.getcwd()}))\n"
        "temporary.replace(ready)\n"
        f"while not pathlib.Path({str(stop)!r}).exists(): time.sleep(.02)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    script_pid = tmp_path / "script-pid"
    backend_python = Path(getattr(sys, "_base_executable", sys.executable))
    if script_backend:
        executable = tmp_path / "backend.ps1"
        executable.write_text(
            f"[IO.File]::WriteAllText({_literal(script_pid)},[string]$PID)\n"
            f"& {_literal(backend_python)} -I {_literal(backend)}\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        arguments = []
    else:
        executable = backend_python
        arguments = ["-I", str(backend)]
    driver = tmp_path / "copied-setup-owner.ps1"
    driver.write_text(
        f"$env:PATH={_literal(Path(shutil.which('pwsh')).parent)}\n"
        f"[IO.File]::WriteAllText({_literal(checkout / 'owner-probe.json')},"
        "(@{pid=$PID;cwd=$PWD.ProviderPath} | ConvertTo-Json -Compress))\n"
        f"& {_literal(copied / 'default-setup.ps1')} -Recovery "
        f"-LaunchSeedPreparation admission -LaunchSeedNativeRoot {_literal(root)} "
        f"-LaunchSeedRecord {_literal(preparation['record'])} -CopilotPath {_literal(executable)} "
        f"-RuntimePython {_literal(tmp_path / 'absent-python.exe')} "
        f"-LaunchAttemptId {_literal(preparation['attempt'])} "
        f"-LaunchResolutionId {_literal(preparation['resolution'])} "
        f"-NativeArgumentsJson {_literal(json.dumps({'args': arguments}))}\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    pane = Path(__file__).resolve().parents[4] / "worktree-manager/bin/pane-wrapper.ps1"
    outer = tmp_path / "copied-pane"
    outer.mkdir()
    shutil.copy2(pane, outer / pane.name)
    shutil.copy2(plugin / "scripts/native-seed-boundary.ps1", outer / "native-seed-boundary.ps1")
    command = [
        shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(outer / pane.name),
        "-AwManagerRoot", str(tmp_path / "absent-manager"),
        shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(driver),
    ]
    console = _Console(command, checkout)
    child_handle = None
    try:
        _wait(ready.is_file, console, "Copied admission setup did not start its terminal backend", timeout=30)
        child = json.loads(ready.read_text(encoding="utf-8"))
        assert child["tty"] is True
        assert Path(child["cwd"]) == checkout
        birth = locks.process_start_time(child["pid"])
        owner_birth = locks.process_start_time(child["parent"])
        assert birth and owner_birth
        child_handle = console.api.OpenProcess(0x100000 | 0x1000, False, child["pid"])
        console._check(child_handle)
        if script_backend:
            assert int(script_pid.read_text(encoding="utf-8")) == child["parent"]
        console.interrupt()
        _wait(interrupted.is_file, console, "Admission setup swallowed the backend's interrupt")
        assert not console.exited()
        assert console.api.WaitForSingleObject(child_handle, 0) == 258
        assert locks.process_start_time(child["pid"]) == birth
        assert locks.process_start_time(child["parent"]) == owner_birth
        stop.write_text("exit", encoding="utf-8")
        _wait(console.exited, console, "Admission setup did not finish after actual child exit", timeout=30)
        files = list(copied.parent.glob("*.execution.json"))
        assert len(files) == 1
        receipt = json.loads(files[0].read_text(encoding="utf-8"))
        assert receipt["attempt_id"] == preparation["attempt"]
        assert receipt["resolution_id"] == preparation["resolution"]
        assert [event["phase"] for event in receipt["events"]] == [
            "execution-requested", "process-spawned", "execution-returned",
        ]
        assert receipt["events"][1]["details"]["pid"] == child["parent"]
        assert receipt["events"][-1]["details"]["process_exit_code"] == 7
        assert receipt["events"][-1]["details"]["exit_code"] == 7
        _wait(
            lambda: b"Session exited with code 7" in console.output,
            console, "Outer pane did not observe the setup's nonzero exit", allow_exited=True,
        )
    finally:
        console.close()
        if child_handle:
            console.api.CloseHandle(child_handle)
