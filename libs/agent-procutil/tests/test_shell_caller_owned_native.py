"""Actual PowerShell caller loss, cancellation and callback-descendant retirement."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil import no_window_kwargs
from test_caller_owned_native import PinnedProcess, _linux_observer


ROOT = Path(__file__).resolve().parents[3]
SDK = ROOT / "libs" / "installation-context"
HOSTS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell")))))
pytestmark = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="native Windows handles or Linux pidfd/subreaper observation",
)

BODY = r'''
import json, os, runpy, subprocess, sys, time
from pathlib import Path
from agent_procutil import no_window_kwargs
sys.path.insert(0, os.environ["SHELL_CALLER_TEST_HELPERS"])
from caller_owned_fixture import identity

TARGET = """
import os, subprocess, sys, time
sys.path.insert(0, os.environ['SHELL_CALLER_TEST_HELPERS'])
from caller_owned_fixture import identity
import json
LEAF = (
    "import json,os,sys,time;"
    "sys.path.insert(0,os.environ['SHELL_CALLER_TEST_HELPERS']);"
    "from caller_owned_fixture import identity;"
    "print(json.dumps(identity()),flush=True);time.sleep(25)"
)
child = subprocess.Popen(
    [sys.executable, '-I', '-S', '-B', '-c', LEAF],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
)
leaf = json.loads(child.stdout.readline(4096))
child.stdout.close()
print(json.dumps([identity(), identity(child.pid), leaf]), flush=True)
time.sleep(25)
"""

def _powershell_main(action, parameters, supplied):
    root = Path(os.environ["SHELL_CALLER_TEST_ROOT"])
    child = subprocess.Popen(
        [sys.executable, '-I', '-S', '-B', '-c', TARGET],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    descendants = json.loads(child.stdout.readline(4096))
    child.stdout.close()
    identities = [identity(), identity(child.pid), *descendants]
    if os.getppid() != int(os.environ["SHELL_CALLER_TEST_PS_OWNER"]):
        identities.append(identity(os.getppid()))
    report = {"identities": identities, "python": sys.executable}
    pending = root / "pending.json"
    pending.write_text(json.dumps(report), encoding="utf-8")
    pending.replace(root / "ready.json")
    (root / "published").touch(exist_ok=False)
    deadline = time.monotonic() + 15
    while not (root / "gate").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test callback gate was not released")
        time.sleep(0.01)
    if os.environ["SHELL_CALLER_TEST_MODE"] == "abrupt":
        os._exit(7)
    original = runpy.run_path(os.environ["SHELL_CALLER_TEST_SDK"])
    return original["_powershell_main"](action, parameters, supplied)
'''

HARNESS = r'''
param([string]$Source, [string]$Root, [string]$Mode)
$ErrorActionPreference = 'Stop'
$env:SHELL_CALLER_TEST_PS_OWNER = [string]$PID
if ($Mode -ceq 'cancel') {
    $runner = [PowerShell]::Create()
    try {
        [void]$runner.AddScript({
            param($Source, $Root)
            & $Source source-id -SourceJson '{"source":"github","repo":"example/marketplace"}' `
                -CopilotHome (Join-Path $Root 'copilot') -DurableHome (Join-Path $Root 'durable')
        }).AddArgument($Source).AddArgument($Root)
        $pending = $runner.BeginInvoke()
        $clock = [Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath (Join-Path $Root 'cancel'))) {
            if ($clock.Elapsed.TotalSeconds -gt 15) { throw 'Cancellation fixture was not released' }
            Start-Sleep -Milliseconds 10
        }
        $runner.Stop()
        Write-Output 'cancelled'
    } finally { $runner.Dispose() }
} else {
    & $Source source-id -SourceJson '{"source":"github","repo":"example/marketplace"}' `
        -CopilotHome (Join-Path $Root 'copilot') -DurableHome (Join-Path $Root 'durable')
    exit $LASTEXITCODE
}
'''


def _wait(predicate, seconds: float, message: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(message)


def _ready_report(parent, root: Path):
    assert parent.poll() is None, parent.communicate(timeout=3)
    if not (root / "published").is_file():
        return None
    return json.loads((root / "ready.json").read_text("utf-8"))


def test_report_is_not_admitted_before_closed_publication(tmp_path):
    report = {"identities": [{"pid": 42, "birth": 43}], "python": "python"}
    (tmp_path / "ready.json").write_text(json.dumps(report), encoding="utf-8")
    parent = SimpleNamespace(poll=lambda: None)
    assert _ready_report(parent, tmp_path) is None
    (tmp_path / "published").touch(exist_ok=False)
    assert _ready_report(parent, tmp_path) == report


def test_published_report_read_errors_are_not_silently_retried(tmp_path, monkeypatch):
    (tmp_path / "published").touch(exist_ok=False)

    def denied(*args, **kwargs):
        raise PermissionError("controlled published-report read failure")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError, match="controlled published-report"):
        _ready_report(SimpleNamespace(poll=lambda: None), tmp_path)


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("failure", ["eof", "malformed"])
def test_startup_receipt_failure_releases_input_before_waiting_for_worker(tmp_path, host, failure):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    source = tmp_path / "sdk"
    source.mkdir()
    shutil.copyfile(SDK / "installation-context.ps1", source / "installation-context.ps1")
    shutil.copyfile(SDK / "shell-caller-owned.ps1", source / "shell-caller-owned.ps1")
    (source / "installation_context.py").write_text("", encoding="utf-8")
    (source / "_installation_context_worker.py").write_text(
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "def main(request):\n"
        "    root = Path(request['parameters']['DurableHome'])\n"
        "    root.mkdir()\n"
        "    if os.environ['SHELL_STARTUP_TEST_FAILURE'] == 'eof':\n"
        "        os.close(1)\n"
        "        sys.stdin.buffer.read()\n"
        "        (root / 'input-closed').touch()\n"
        "        sys.stderr.write('controlled receipt publication failure\\n')\n"
        "        return 1\n"
        "    sys.path.insert(0, os.environ['SHELL_STARTUP_TEST_SOURCE'])\n"
        "    from agent_procutil._shell_caller_owned import serve\n"
        "    original = sys.stdout\n"
        "    class MalformedReceipt:\n"
        "        def write(self, text):\n"
        "            json.loads(text)\n"
        "            (root / 'offer.json').write_text(text, encoding='utf-8')\n"
        "            sys.stderr.write('controlled malformed receipt\\n')\n"
        "            sys.stderr.flush()\n"
        "            return original.write('{invalid-json\\n')\n"
        "        def flush(self):\n"
        "            original.flush()\n"
        "    sys.stdout = MalformedReceipt()\n"
        "    def callback():\n"
        "        (root / 'forbidden-application').touch()\n"
        "        return 0\n"
        "    return serve(callback, **request['lifetime'])\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [host, "-NoProfile", "-NonInteractive", "-File",
         str(source / "installation-context.ps1"), "source-id",
         "-SourceJson", '{"source":"github","repo":"example/marketplace"}',
         "-CopilotHome", str(tmp_path / "copilot"),
         "-DurableHome", str(tmp_path / "released")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={
            **os.environ, "SHELL_STARTUP_TEST_FAILURE": failure,
            "SHELL_STARTUP_TEST_SOURCE": str(ROOT / "libs/agent-procutil/src"),
        },
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 1, (stdout, stderr)
        assert not stdout
        root = tmp_path / "released"
        if failure == "eof":
            assert "startup failed" in stderr
            assert "controlled receipt publication failure" in stderr
            assert (root / "input-closed").is_file()
        else:
            assert "installation-context:" in stderr
            assert "controlled malformed receipt" in stderr
            assert "startup failed before a custody receipt" not in stderr
            receipt = json.loads((root / "offer.json").read_text("utf-8"))
            assert receipt["owner_pid"] == process.pid
            assert receipt["scope"] == ("windows-job" if os.name == "nt" else "posix-group")
            assert not (root / "forbidden-application").exists()
            if os.name == "nt":
                assert receipt["job"]["parent_pid"] == process.pid
                assert "unconfirmed" not in stderr
                assert "original caller refused the unadopted Job offer" not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(3)
        process.stdout.close()
        process.stderr.close()


OFFER_MODES = (
    "no-permission", "wrong-permission", "abort", "write-abort",
    "write-eof", "wrong-ack", "missing-ack", "adopted",
)
OFFER_BODY = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil._shell_caller_owned import serve
mode = sys.argv[2]
if mode.startswith("write-"):
    class FailedReceipt:
        def write(self, text):
            sys.stderr.write("receipt:" + text)
            sys.stderr.flush()
            raise OSError("controlled receipt write failure")
        def flush(self):
            pass
    sys.stdout = FailedReceipt()
def callback():
    print("admitted", flush=True)
    return 7
sys.exit(serve(
    callback, owner_pid=int(sys.argv[3]),
    owner_birth=birth_identity(int(sys.argv[3])), nonce="a" * 32,
))
'''
OFFER_GATE = r'''
from pathlib import Path
import time
gate_deadline = time.monotonic() + 5
while Path(_offer_gate).read_bytes() != b"1":
    if time.monotonic() >= gate_deadline:
        raise TimeoutError("native offer fixture did not release its cold child")
    time.sleep(0.01)
'''


@pytest.mark.skipif(os.name != "nt", reason="actual Windows parent Job handle table")
@pytest.mark.parametrize("mode", OFFER_MODES)
def test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path):
    import ctypes
    from ctypes import wintypes

    from agent_procutil._daemon_instance_identity import birth_identity
    from agent_procutil._windows_parent_job import _api

    kernel = _api()
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessHandleCount.restype = wintypes.BOOL
    kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetHandleInformation.restype = wintypes.BOOL

    def handle_count():
        count = wintypes.DWORD()
        assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
        return count.value

    parent_birth = birth_identity(os.getpid())
    nonce = "a" * 32
    decision = {
        "no-permission": b"",
        "wrong-permission": b"wrong\n",
        "abort": ("offer:" + nonce + "\nabort:" + nonce + "\n").encode(),
        "write-abort": ("offer:" + nonce + "\nabort:" + nonce + "\n").encode(),
        "write-eof": ("offer:" + nonce + "\n").encode(),
        "wrong-ack": ("offer:" + nonce + "\nwrong\n").encode(),
        "missing-ack": ("offer:" + nonce + "\n").encode(),
        "adopted": ("offer:" + nonce + "\n" + nonce + "\n").encode(),
    }[mode]
    gate_path = tmp_path / "offer-fixture.gate"
    with (
        (tmp_path / "offer-fixture.stdout").open("w+b") as output,
        (tmp_path / "offer-fixture.stderr").open("w+b") as errors,
        gate_path.open("w+b") as gate,
    ):
        gate.write(b"0")
        gate.flush()
        body = f"_offer_gate = {str(gate_path)!r}\n" + OFFER_GATE + OFFER_BODY
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", body,
             str(ROOT / "libs/agent-procutil/src"), mode, str(os.getpid())],
            stdin=subprocess.PIPE, stdout=output, stderr=errors,
            **no_window_kwargs(),
        )
        remote = None
        try:
            process.stdin.write(decision)
            process.stdin.close()
            # Hold the same fixture handles/references across the cold SDK call.
            # File-backed capture creates no communicate() reader threads.
            baseline = handle_count()
            gate.seek(0)
            gate.write(b"1")
            gate.flush()
            process.wait(timeout=5)
            output.seek(0)
            errors.seek(0)
            stdout, stderr = output.read(), errors.read()
            if mode not in ("no-permission", "wrong-permission"):
                wire = stderr.split(b"receipt:", 1)[1] if mode.startswith("write-") else stdout
                receipt = json.loads(wire.splitlines()[0])
                assert receipt["version"] == 1 and receipt["scope"] == "windows-job"
                assert receipt["nonce"] == nonce and receipt["owner_pid"] == os.getpid()
                assert receipt["job"]["parent_pid"] == os.getpid()
                assert receipt["job"]["parent_birth"] == parent_birth
                if mode not in ("abort", "write-abort"):
                    remote = receipt["job"]["parent_handle"]
                    flags = wintypes.DWORD()
                    assert kernel.GetHandleInformation(remote, ctypes.byref(flags)), (
                        "unconfirmed adoption reclaimed the caller's still-owned handle"
                    )
                    assert flags.value & 1 == 0
            if mode == "adopted":
                assert process.returncode == 7, stderr
                assert stdout.splitlines()[1:] == [
                    b"admitted", b"", ("__COPILOT_CALLER_EXIT_" + nonce + "=7").encode(),
                ]
                assert not stderr
            else:
                if mode in ("abort", "write-abort"):
                    # Last-Job-handle closure may kill the worker before Python raises.
                    flags = wintypes.DWORD()
                    assert not kernel.GetHandleInformation(
                        receipt["job"]["parent_handle"], ctypes.byref(flags),
                    ), "aborted offer left its caller handle valid"
                    assert ctypes.get_last_error() == 6, "aborted offer handle closure is unconfirmed"
                else:
                    assert process.returncode != 0, "offer failure unexpectedly exited successfully"
                assert b"admitted" not in stdout and b"__COPILOT_CALLER_EXIT_" not in stdout
                if mode in ("no-permission", "wrong-permission"):
                    assert b"original caller did not acknowledge lifetime custody" in stderr
                if mode in ("write-eof", "wrong-ack", "missing-ack"):
                    assert b"Job adoption is unconfirmed" in stderr
            assert handle_count() == baseline + int(remote is not None), (
                "parent Job offer has an unexpected native handle balance"
            )
            if remote is not None:
                handle, remote = remote, None
                assert kernel.CloseHandle(handle)
            assert handle_count() == baseline, "parent Job offer left an unsettled native handle"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(3)
            process.stdin.close()
            process._handle.Close()
            if remote is not None:
                assert kernel.CloseHandle(remote)


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("composition", ["path", "test-venv"])
@pytest.mark.parametrize("mode", ["completed", "abrupt", "caller-exit", "cancel"])
@pytest.mark.parametrize("cycle", range(2))
def test_sdk_scope_retires_before_observer_cleanup(tmp_path, host, composition, mode, cycle):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    source = tmp_path / "sdk"
    source.mkdir()
    for name in (
        "installation-context.ps1", "shell-caller-owned.ps1",
        "_installation_context_worker.py", "daemon_instance_runtime.pyz",
    ):
        shutil.copyfile(SDK / name, source / name)
    (source / "installation_context.py").write_text(BODY, encoding="utf-8")
    harness = tmp_path / "caller.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    environment = {
        **os.environ,
        "SHELL_CALLER_TEST_ROOT": str(tmp_path),
        "SHELL_CALLER_TEST_HELPERS": str(Path(__file__).parent),
        "SHELL_CALLER_TEST_MODE": mode,
        "SHELL_CALLER_TEST_SDK": str(SDK / "installation_context.py"),
    }
    if composition == "test-venv":
        executable = Path(sys.executable)
        if not (executable.parent.parent / "pyvenv.cfg").is_file():
            pytest.skip("this test interpreter is not an existing venv")
        environment["PATH"] = str(executable.parent) + os.pathsep + environment.get("PATH", "")
    observer = _linux_observer() if os.name != "nt" else nullcontext()
    with observer, ExitStack() as cleanup:
        parent = subprocess.Popen(
            [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
             "-Source", str(source / "installation-context.ps1"),
             "-Root", str(tmp_path), "-Mode", mode],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=environment, **no_window_kwargs(),
        )
        try:
            report = _wait(
                lambda: _ready_report(parent, tmp_path), 10,
                "actual SDK callback did not close its test report publication",
            )
            if composition == "test-venv":
                assert os.path.normcase(os.path.abspath(report["python"])) == os.path.normcase(
                    os.path.abspath(sys.executable),
                )
            pins = []
            seen = set()
            for identity in report["identities"]:
                key = (identity["pid"], identity["birth"])
                if key in seen:
                    continue
                seen.add(key)
                pin = PinnedProcess(identity)
                cleanup.callback(pin.close)
                cleanup.callback(pin.kill)
                pins.append(pin)
            assert all(not pin.gone() for pin in pins)
            if mode == "caller-exit":
                parent.kill()
            elif mode == "cancel":
                (tmp_path / "cancel").touch()
            else:
                (tmp_path / "gate").touch()
            stdout, stderr = parent.communicate(timeout=6)
            _wait(
                lambda: all(pin.gone() for pin in pins), 3,
                "SDK callback or descendants survived original caller retirement",
            )
            if mode == "completed":
                assert parent.returncode == 0, stdout + stderr
                assert json.loads(stdout)["canonical"] == "github:example/marketplace"
                assert not stderr
            elif mode == "abrupt":
                assert parent.returncode != 0
                assert not stdout.strip()
            elif mode == "cancel":
                assert parent.returncode == 0, stdout + stderr
                assert stdout.strip() == "cancelled"
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(3)
            parent.stdout.close()
            parent.stderr.close()


CELL_HASH_WORKER = r'''
import os, runpy, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["SHELL_CALLER_TEST_HELPERS"])
from caller_owned_fixture import identity
import json

def main(request):
    cell = runpy.run_path(os.environ["CELL_CALLER_TEST_SOURCE"])
    def blocked_identity(payload, version):
        root = Path(os.environ["SHELL_CALLER_TEST_ROOT"])
        identities = [identity()]
        if os.getppid() != int(os.environ["SHELL_CALLER_TEST_PS_OWNER"]):
            identities.append(identity(os.getppid()))
        pending = root / "pending.json"
        pending.write_text(json.dumps({"identities": identities}), encoding="utf-8")
        pending.replace(root / "ready.json")
        (root / "published").touch(exist_ok=False)
        time.sleep(25)
        (root / "forbidden-after-hash").touch()
        raise RuntimeError("blocked hash was not retired")
    real_identity = cell["payload_identity"]
    def observed_identity(payload, version):
        identities = [identity()]
        if os.getppid() != int(os.environ["SHELL_CALLER_TEST_PS_OWNER"]):
            identities.append(identity(os.getppid()))
        os.environ["CELL_CALLER_TEST_OUTER"] = json.dumps(identities)
        return real_identity(payload, version)
    cell["watchdog"].__globals__["payload_identity"] = (
        blocked_identity if os.environ["CELL_CALLER_TEST_PHASE"] == "hash" else observed_identity
    )
    return cell["main"](request)
'''

CELL_HARNESS = r'''
param([string]$Source, [string]$Worker, [string]$Root, [int]$Deadline)
$ErrorActionPreference = 'Stop'
$env:SHELL_CALLER_TEST_PS_OWNER = [string]$PID
$request = @{
    argv = @($env:CELL_CALLER_TEST_PYTHON, '-I', '-B', '-X', 'utf8',
        (Join-Path $Root 'installer.py'))
    cwd = $Root
    payload = (Join-Path $Root 'payload')
    timeout = 30
}
& $Source -Worker $Worker -Request $request -TimeoutSeconds $Deadline
exit $LASTEXITCODE
'''


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("mode", ["deadline", "caller-exit"])
@pytest.mark.parametrize("phase", ["hash", "installer"])
def test_cell_work_is_bound_to_original_shell_and_whole_deadline(tmp_path, host, mode, phase):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "payload"
    root.mkdir()
    (root / "plugin.json").write_text('{"name":"agent-machines","version":"1.0"}')
    (root / "pyproject.toml").write_text('[project]\nversion = "1.0"\n')
    (tmp_path / "installer.py").write_text(
        "import json,os,subprocess,sys,time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, os.environ['SHELL_CALLER_TEST_HELPERS'])\n"
        "from caller_owned_fixture import identity\n"
        "child = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(25)'], "
        "creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)\n"
        "identities = [*json.loads(os.environ['CELL_CALLER_TEST_OUTER']), identity(), identity(child.pid)]\n"
        "root = Path(os.environ['SHELL_CALLER_TEST_ROOT'])\n"
        "pending = root / 'pending.json'\n"
        "pending.write_text(json.dumps({'identities':identities}), encoding='utf-8')\n"
        "pending.replace(root / 'ready.json')\n"
        "(root / 'published').touch(exist_ok=False)\n"
        "time.sleep(25)\n"
        "(root / 'forbidden-after-hash').touch()\n",
        encoding="utf-8",
    )
    worker = tmp_path / "worker.py"
    worker.write_text(CELL_HASH_WORKER, encoding="utf-8")
    harness = tmp_path / "caller.ps1"
    harness.write_text(CELL_HARNESS, encoding="utf-8")
    environment = {
        **os.environ,
        "SHELL_CALLER_TEST_ROOT": str(tmp_path),
        "SHELL_CALLER_TEST_HELPERS": str(Path(__file__).parent),
        "CELL_CALLER_TEST_SOURCE": str(ROOT / "plugins/agent-machines/scripts/cell_installer.py"),
        "CELL_CALLER_TEST_PHASE": phase,
        "CELL_CALLER_TEST_PYTHON": sys.executable,
    }
    observer = _linux_observer() if os.name != "nt" else nullcontext()
    with observer, ExitStack() as cleanup:
        started = time.monotonic()
        parent = subprocess.Popen(
            [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
             "-Source", str(SDK / "shell-caller-owned.ps1"), "-Worker", str(worker),
             "-Root", str(tmp_path), "-Deadline", "4" if mode == "deadline" else "30"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=environment, **no_window_kwargs(),
        )
        try:
            report = _wait(
                lambda: _ready_report(parent, tmp_path), 3,
                "cell hash did not publish its closed readiness witness",
            )
            pins = []
            for identity in report["identities"]:
                pin = PinnedProcess(identity)
                cleanup.callback(pin.close)
                cleanup.callback(pin.kill)
                pins.append(pin)
            assert all(not pin.gone() for pin in pins)
            if mode == "caller-exit":
                parent.kill()
            stdout, stderr = parent.communicate(timeout=6)
            _wait(
                lambda: all(pin.gone() for pin in pins), 3,
                "cell hash survived original PowerShell retirement",
            )
            assert time.monotonic() - started < 10
            assert not (tmp_path / "forbidden-after-hash").exists()
            if mode == "deadline":
                assert parent.returncode == 124, stdout + stderr
                assert "WATCHDOG" in stderr
                assert "unconfirmed" not in stderr, stderr
            else:
                assert parent.returncode != 0
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(3)
            parent.stdout.close()
            parent.stderr.close()


@pytest.mark.parametrize("host", HOSTS or [None])
def test_cell_transport_round_trips_utf8_with_legacy_output_environment(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "payload"
    root.mkdir()
    (root / "plugin.json").write_text('{"name":"agent-machines","version":"1.0"}')
    (root / "pyproject.toml").write_text('[project]\nversion = "1.0"\n')
    (tmp_path / "installer.py").write_text(
        "import sys\nprint('result \\u03bb')\n"
        "sys.stderr.write('diagnostic \\u03bb\\n')\nsys.exit(7)\n",
        encoding="utf-8",
    )
    harness = tmp_path / "caller.ps1"
    harness.write_text(CELL_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SDK / "shell-caller-owned.ps1"),
         "-Worker", str(ROOT / "plugins/agent-machines/scripts/cell_installer.py"),
         "-Root", str(tmp_path), "-Deadline", "10"],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        env={**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0",
             "CELL_CALLER_TEST_PYTHON": sys.executable},
        **no_window_kwargs(),
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert result.stdout == "result \u03bb\n"
    assert result.stderr == "diagnostic \u03bb\n"
