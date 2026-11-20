"""No execution clock: root exit, not inherited pipe EOF, ends execution."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator

import pytest

from agent_procutil import _caller_owned, _native_query
from agent_procutil._linux_pidfd import open_pidfd
from agent_procutil.daemon_broker import WindowsDaemonProcess
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OutputLimitExceeded, _run_owned, run_caller_owned


native = pytest.mark.skipif(
    os.name != "nt" and sys.platform not in {"linux", "darwin"},
    reason="caller-owned execution requires Windows, Linux or macOS",
)


@contextmanager
def _pin_descendant(pid: int) -> Iterator[Callable[[], None]]:
    if os.name == "nt":
        process = WindowsDaemonProcess(pid, ["pipe-retaining-fixture"])
        try:
            assert process.poll() is None

            def confirm_windows() -> None:
                process.wait(timeout=2)

            yield confirm_windows
        finally:
            process.close()
    elif sys.platform == "darwin":
        import select

        with select.kqueue() as queue:
            change = select.kevent(
                pid, filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            assert not queue.control([change], 1, 0)

            def confirm_darwin() -> None:
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    for event in queue.control(None, 1, .05):
                        assert not event.flags & select.KQ_EV_ERROR
                        assert event.fflags & select.KQ_NOTE_EXIT
                        assert event.ident == pid
                        return
                pytest.fail("the identity-bound descendant did not exit")

            yield confirm_darwin
    else:
        descriptor = open_pidfd(pid)
        try:
            assert not _caller_owned._pidfd_exited(descriptor)

            def confirm_linux() -> None:
                assert _caller_owned._pidfd_exited(descriptor)

            yield confirm_linux
        finally:
            os.close(descriptor)


@pytest.mark.skipif(
    os.name != "nt" and sys.platform not in {"linux", "darwin"},
    reason="positive descendant identities require Windows handles, Linux pidfds or Darwin kqueue",
)
@pytest.mark.parametrize("exclusive", [False, True])
@pytest.mark.parametrize("with_input", [False, True])
def test_root_exit_retires_descendant_before_waiting_for_pipe_eof(
    tmp_path: Path, exclusive: bool, with_input: bool,
) -> None:
    marker, release = tmp_path / "descendant.pid", tmp_path / "root.release"
    child = (
        "import os,pathlib,time;"
        "os.write(1,b'descendant\\r\\n');os.write(2,b'child-error\\r\\n');"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(20)"
    )
    root = (
        "import pathlib,subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-I','-c',{child!r}],"
        "stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,"
        "creationflags=(0x08000000 if sys.platform=='win32' else 0));"
        f"release=pathlib.Path({str(release)!r})\n"
        "while not release.exists(): time.sleep(.01)\n"
        "sys.stdout.buffer.write(b'root\\r\\n');sys.stderr.buffer.write(b'root-error\\r\\n');"
        "sys.exit(7)"
    )
    notifications: list[bool] = []

    def execute() -> subprocess.CompletedProcess[object]:
        argv = [sys.executable, "-I", "-c", root]
        if exclusive:
            return _run_owned(
                argv, timeout=None, capture_limits=(4096, 4096), confirm_posix_group=True,
                input_text="x" * 262144 if with_input else None,
                on_settled=lambda: notifications.append(True),
            )
        return run_caller_owned(
            argv, timeout=None, max_stdout_bytes=4096, max_stderr_bytes=4096,
            input_text="x" * 262144 if with_input else None,
            on_settled=lambda: notifications.append(True),
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute)
        try:
            deadline = time.monotonic() + 8
            while not marker.exists() or not marker.read_text().isdigit():
                if future.done():
                    future.result()
                    pytest.fail("root completed before its descendant was pinned")
                assert time.monotonic() < deadline, "descendant did not reach its release gate"
                time.sleep(.01)
            with _pin_descendant(int(marker.read_text())) as confirm:
                release.touch()
                result = future.result(timeout=8)
                assert result.returncode == 7
                assert result.stdout == "descendant\nroot\n"
                assert result.stderr == "child-error\nroot-error\n"
                assert notifications == [True]
                confirm()
        finally:
            release.touch()


@native
def test_no_execution_deadline_preserves_capture_and_confirmed_settlement() -> None:
    settled: list[bool] = []
    result = run_caller_owned(
        [sys.executable, "-I", "-c", "import time;time.sleep(.1);print('launch ready')"],
        timeout=None, max_stdout_bytes=4096, max_stderr_bytes=4096,
        on_settled=lambda: settled.append(True),
    )
    assert result.returncode == 0 and result.stdout == "launch ready\n"
    assert settled == [True]


@native
def test_no_execution_deadline_still_honors_the_aggregate_budget() -> None:
    settled: list[bool] = []
    with pytest.raises(subprocess.TimeoutExpired):
        run_caller_owned(
            [sys.executable, "-I", "-c", "import time;time.sleep(30)"],
            timeout=None, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=OwnedExecutionBudget(time.monotonic() + 3, 1),
            on_settled=lambda: settled.append(True),
        )
    assert settled == [True]


@native
def test_no_execution_deadline_still_enforces_output_limits() -> None:
    settled: list[bool] = []
    with pytest.raises(OutputLimitExceeded):
        run_caller_owned(
            [sys.executable, "-I", "-c",
             "import sys,time;sys.stdout.buffer.write(b'x'*65536);sys.stdout.flush();time.sleep(30)"],
            timeout=None, max_stdout_bytes=32, max_stderr_bytes=32,
            on_settled=lambda: settled.append(True),
        )
    assert settled == [True]


@pytest.mark.parametrize("timeout_ms", [2000, None])
def test_native_query_forwards_nullable_timeout_without_changing_settlement(
    monkeypatch: pytest.MonkeyPatch, timeout_ms: int | None,
) -> None:
    def execute(
        argv: list[str], *, cwd: str, env: dict[str, str], timeout: float | None,
        capture_limits: tuple[int, int], on_settled: Callable[[], None],
    ) -> subprocess.CompletedProcess[str]:
        assert argv == [r"C:\Python\python.exe", "", "a b", 'x"y', "tail\\"]
        assert cwd == "C:\\fixture\\"
        assert env == {"PATH": r"C:\Python", "UNCHANGED": "value"}
        assert timeout == (2 if timeout_ms is not None else None)
        assert capture_limits == (4096, 4096)
        on_settled()
        return subprocess.CompletedProcess(argv, 7, "exact output", "exact error")

    monkeypatch.setattr(_native_query, "_run_owned", execute)
    assert _native_query.run({
        "executable": r"C:\Python\python.exe", "arguments": ["", "a b", 'x"y', "tail\\"],
        "cwd": "C:\\fixture\\", "timeout_ms": timeout_ms, "limit": 4096,
        "environment": {"PATH": r"C:\Python", "UNCHANGED": "value"},
    }) == {"returncode": 7, "stdout": "exact output", "stderr": "exact error"}


@pytest.fixture(params=["powershell", "pwsh"])
def native_shell(request: pytest.FixtureRequest) -> Path:
    if request.param == "powershell":
        return Path(os.environ["SystemRoot"]) / r"System32\WindowsPowerShell\v1.0\powershell.exe"
    shell = shutil.which("pwsh")
    assert shell, "the admitted matrix requires native PowerShell 7"
    return Path(shell)


def _quoted(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _shell_result(native_shell: Path, body: str) -> object:
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    result = run_caller_owned(
        [str(native_shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        timeout=45, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0, result.stderr
    assert isinstance(result.stdout, str)
    return json.loads(result.stdout)


@pytest.mark.skipif(os.name != "nt", reason="Windows native transient query contract")
def test_transient_query_has_no_implicit_ten_second_deadline(
    tmp_path: Path, native_shell: Path,
) -> None:
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
    program = "import sys,time;time.sleep(10.1);print('finite response');sys.exit(7)"
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(helper)};"
        "$environment=@{};foreach($entry in [Environment]::GetEnvironmentVariables().GetEnumerator())"
        "{$environment[[string]$entry.Key]=[string]$entry.Value};"
        "$clock=[Diagnostics.Stopwatch]::StartNew();"
        "$result=Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{"
        f"executable={_quoted(sys.executable)};arguments=@('-I','-B','-c',{_quoted(program)});"
        f"cwd={_quoted(tmp_path)};environment=$environment;timeout_ms=$null;limit=4096}}}};"
        "@{result=$result;elapsed=$clock.ElapsedMilliseconds;"
        "globalHelper=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))}"
        "|ConvertTo-Json -Compress"
    )
    outcome = _shell_result(native_shell, body)
    assert isinstance(outcome, dict)
    assert outcome["result"] == {"returncode": 7, "stdout": "finite response\n", "stderr": ""}
    assert not outcome["globalHelper"]
    assert 10000 <= outcome["elapsed"] < 30000


@pytest.mark.skipif(os.name != "nt", reason="Windows native query custody")
def test_no_deadline_query_refuses_shared_helper_custody(native_shell: Path) -> None:
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(helper)};"
        "$refused=$false;try {"
        "Invoke-DaemonInstanceRequest @{command='run-query';parameters=@{timeout_ms=$null}}"
        "}catch{$refused=$_.Exception.Message -like '*transient caller custody*'};"
        "@{refused=$refused;"
        "globalHelper=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))}"
        "|ConvertTo-Json -Compress"
    )
    assert _shell_result(native_shell, body) == {"refused": True, "globalHelper": False}


@pytest.mark.skipif(os.name != "nt", reason="Windows concurrent transient queries")
@pytest.mark.parametrize("timeout_ms", ["10000", "$null"])
def test_transient_queries_do_not_share_a_serialization_gate(
    tmp_path: Path, native_shell: Path, timeout_ms: str,
) -> None:
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
    markers = (tmp_path / "first.ready", tmp_path / "second.ready")

    def program(own: Path, peer: Path) -> str:
        return (
            "import pathlib,sys,time;"
            f"pathlib.Path({str(own)!r}).touch();peer=pathlib.Path({str(peer)!r});"
            "deadline=time.monotonic()+5\n"
            "while not peer.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "sys.exit(0 if peer.exists() else 9)"
        )

    body = (
        "$ErrorActionPreference='Stop';"
        "$script={param($helper,$python,$cwd,$program)"
        "$ErrorActionPreference='Stop';. $helper;"
        "$environment=@{};foreach($entry in [Environment]::GetEnvironmentVariables().GetEnumerator())"
        "{$environment[[string]$entry.Key]=[string]$entry.Value};"
        "Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{"
        "executable=$python;arguments=@('-I','-B','-c',$program);cwd=$cwd;"
        f"environment=$environment;timeout_ms={timeout_ms};limit=4096}}}}}};"
        "$first=[PowerShell]::Create();$second=[PowerShell]::Create();try{"
        f"$null=$first.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(sys.executable)}).AddArgument({_quoted(tmp_path)})"
        f".AddArgument({_quoted(program(*markers))});"
        f"$null=$second.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(sys.executable)}).AddArgument({_quoted(tmp_path)})"
        f".AddArgument({_quoted(program(*reversed(markers)))});"
        "$one=$first.BeginInvoke();$two=$second.BeginInvoke();"
        "$a=@($first.EndInvoke($one));$b=@($second.EndInvoke($two));"
        "if($first.HadErrors -or $second.HadErrors){throw 'transient runspace failed'};"
        "@{codes=@($a[0].returncode,$b[0].returncode);"
        "globalHelper=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))}"
        "|ConvertTo-Json -Compress"
        "}finally{$first.Dispose();$second.Dispose()}"
    )
    assert _shell_result(native_shell, body) == {"codes": [0, 0], "globalHelper": False}


@pytest.mark.skipif(os.name != "nt", reason="Windows native transient query cancellation")
def test_cancelling_active_no_deadline_query_confirms_its_original_tree(
    tmp_path: Path, native_shell: Path,
) -> None:
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
    targets, ready, attempts = (
        tmp_path / "targets.json", tmp_path / "leaf.ready", tmp_path / "attempts.txt",
    )
    leaf = (
        "import pathlib,time;"
        f"pathlib.Path({str(ready)!r}).touch();time.sleep(25)"
    )
    program = (
        "import json,os,pathlib,subprocess,sys,time;"
        f"pathlib.Path({str(attempts)!r}).open('a').write(str(os.getpid())+'\\n');"
        f"child=subprocess.Popen([sys.executable,'-I','-c',{leaf!r}],"
        "creationflags=0x08000000);"
        f"ready=pathlib.Path({str(ready)!r});deadline=time.monotonic()+5\n"
        "while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert ready.exists();"
        f"pending=pathlib.Path({str(targets.with_suffix('.pending'))!r});"
        "pending.write_text(json.dumps([os.getpid(),child.pid]));"
        f"pending.replace({str(targets)!r});"
        "time.sleep(25)"
    )
    body = (
        "$ErrorActionPreference='Stop';"
        "$shared=[hashtable]::Synchronized(@{});$runner=[PowerShell]::Create();"
        "$pins=New-Object 'Collections.Generic.List[Diagnostics.Process]';try{"
        "$script={param($helper,$python,$cwd,$program,$shared)"
        "$ErrorActionPreference='Stop';. $helper;"
        "$originalStart=(Get-Command Start-DaemonInstanceWorker).ScriptBlock;"
        "function Start-DaemonInstanceWorker {param($State,[switch]$NoTimeout)"
        "& $originalStart $State -NoTimeout:$NoTimeout;$shared.State=$State};"
        "$environment=@{};foreach($entry in [Environment]::GetEnvironmentVariables().GetEnumerator())"
        "{$environment[[string]$entry.Key]=[string]$entry.Value};"
        "Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{"
        "executable=$python;arguments=@('-I','-B','-c',$program);cwd=$cwd;"
        "environment=$environment;timeout_ms=$null;limit=4096}}};"
        f"$null=$runner.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(sys.executable)}).AddArgument({_quoted(tmp_path)})"
        f".AddArgument({_quoted(program)}).AddArgument($shared);"
        "$pending=$runner.BeginInvoke();$clock=[Diagnostics.Stopwatch]::StartNew();"
        f"while(-not $shared.State -or -not [IO.File]::Exists({_quoted(targets)})){{"
        "if($pending.IsCompleted -or $clock.Elapsed.TotalSeconds -gt 15){"
        "throw ('native query did not start: '+$runner.Streams.Error.ToString())};"
        "Start-Sleep -Milliseconds 10};"
        f"$targets=Get-Content -Raw -LiteralPath {_quoted(targets)}|ConvertFrom-Json;"
        "$ids=@($shared.State.process.Id,$shared.State.worker.Id)+@($targets);"
        "foreach($identity in @($ids|Select-Object -Unique)){"
        "$pin=[Diagnostics.Process]::GetProcessById([int]$identity);$null=$pin.Handle;"
        "if($pin.HasExited){throw 'original query identity exited before cancellation'};"
        "$pins.Add($pin)};"
        "$clock.Restart();$runner.Stop();"
        "foreach($pin in $pins){"
        "$remaining=[Math]::Max(0,3000-$clock.ElapsedMilliseconds);"
        "if(-not $pin.WaitForExit([int]$remaining)){"
        "throw 'original transient query identity survived cancellation'}};"
        "if($clock.Elapsed.TotalSeconds -gt 6){throw 'native cancellation did not settle promptly'};"
        "if(-not $shared.State.job.IsClosed -or -not $shared.State.broken){"
        "throw 'original helper custody did not close during cancellation'};"
        "if($runner.InvocationStateInfo.State -ne 'Stopped'){throw 'query was not cancelled'};"
        f"if(@(Get-Content -LiteralPath {_quoted(attempts)}).Count -ne 1){{throw 'query was replayed'}};"
        # These assertions run while this PowerShell host and its outer test Job
        # are still alive, before runner disposal or the Python owner's cleanup.
        "@{cancelled=$true;treeExited=$true;jobClosed=$shared.State.job.IsClosed;"
        "pinned=$pins.Count;attempts=1}|ConvertTo-Json -Compress"
        "}finally{foreach($pin in $pins){$pin.Dispose()};$runner.Dispose()}"
    )
    outcome = _shell_result(native_shell, body)
    assert isinstance(outcome, dict)
    assert outcome == {
        "cancelled": True, "treeExited": True, "jobClosed": True,
        "pinned": outcome["pinned"], "attempts": 1,
    }
    assert outcome["pinned"] >= 3


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="native borrowed POSIX scope")
def test_borrowed_scope_does_not_report_success_for_a_quiet_live_descendant(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "borrowed-descendant.pid"
    source = str(Path(_caller_owned.__file__).resolve().parents[1])
    child = (
        "import os,pathlib,time;"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(25)"
    )
    root = (
        "import pathlib,subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-I','-c',{child!r}]);"
        f"marker=pathlib.Path({str(marker)!r})\n"
        "while not marker.exists(): time.sleep(.01)\n"
        "sys.exit(7)"
    )
    borrower = (
        f"import subprocess,sys,time;sys.path.insert(0,{source!r});"
        "from agent_procutil.owned_process import run_caller_owned;"
        "from agent_procutil.owned_budget import OwnedExecutionBudget\n"
        "try:\n"
        f" run_caller_owned({[sys.executable, '-I', '-c', root]!r},"
        "timeout=None,max_stdout_bytes=64,max_stderr_bytes=64,"
        "borrow_parent_scope=True,budget=OwnedExecutionBudget(time.monotonic()+3,1))\n"
        "except subprocess.TimeoutExpired: print('budget-expired')\n"
        "else: raise AssertionError('borrowed capture returned with a live writer')"
    )

    def execute() -> subprocess.CompletedProcess[object]:
        return run_caller_owned(
            [sys.executable, "-I", "-c", borrower], timeout=10,
            max_stdout_bytes=4096, max_stderr_bytes=4096,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute)
        deadline = time.monotonic() + 6
        while not marker.exists() or not marker.read_text().isdigit():
            if future.done():
                future.result()
                pytest.fail("borrowed child exited before its descendant was pinned")
            assert time.monotonic() < deadline
            time.sleep(.01)
        with _pin_descendant(int(marker.read_text())) as confirm:
            result = future.result(timeout=8)
            assert result.returncode == 0 and result.stdout == "budget-expired\n"
            confirm()


@pytest.mark.skipif(os.name != "nt", reason="Windows unsupported interpreter capture grace")
def test_no_deadline_unsupported_interpreter_stderr_capture_has_finite_grace(
    tmp_path: Path, native_shell: Path,
) -> None:
    package = Path(__file__).resolve().parents[1] / "src" / "agent_procutil"
    helper = tmp_path / "daemon_instances.ps1"
    source = (package / helper.name).read_text(encoding="utf-8")
    anchor = "import base64,json,sys\n"
    assert source.count(anchor) == 1
    source = source.replace(
        anchor, anchor
        + "import subprocess\n"
        + "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(20)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=sys.stderr,"
        "creationflags=subprocess.CREATE_NO_WINDOW)\n"
        + "sys.version_info=(3,9)\n",
    )
    helper.write_text(source, encoding="utf-8")
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(helper)};"
        f"$script:DaemonInstanceRuntime={_quoted(package / 'daemon_instance_runtime.pyz')};"
        f"$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON={_quoted(sys.executable)};"
        "$state=@{process=$null;worker=$null;job=$null;input=$null;output=$null;error=$null;"
        "diagnostics=$null;nonce=$null;sequence=0;broken=$true;offerPermitted=$false};"
        "$failure=$null;$cleanupError=$null;$clock=[Diagnostics.Stopwatch]::StartNew();"
        "try{Start-DaemonInstanceWorker $state -NoTimeout}"
        "catch{$failure=$_.Exception.Message};"
        "$elapsed=$clock.ElapsedMilliseconds;$ended=$state.process.HasExited;"
        "try{Close-DaemonInstanceWorker $state}catch{$cleanupError=$_.Exception.Message};"
        "@{failure=$failure;cleanupError=$cleanupError;elapsed=$elapsed;ended=$ended}"
        "|ConvertTo-Json -Compress"
    )
    outcome = _shell_result(native_shell, body)
    assert isinstance(outcome, dict)
    assert outcome["ended"] is True
    assert "diagnostic capture is unconfirmed" in outcome["failure"]
    assert "not retried" in outcome["failure"]
    assert outcome["cleanupError"] and "unconfirmed" in outcome["cleanupError"]
    assert 2500 <= outcome["elapsed"] < 10000
