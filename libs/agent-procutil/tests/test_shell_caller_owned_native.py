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
@pytest.mark.parametrize("composition", ["path", "test-venv"])
@pytest.mark.parametrize("mode", ["completed", "abrupt", "caller-exit", "cancel"])
@pytest.mark.parametrize("cycle", range(2))
def test_sdk_scope_retires_before_observer_cleanup(tmp_path, host, composition, mode, cycle):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    source = tmp_path / "sdk"
    source.mkdir()
    for name in (
        "installation-context.ps1", "_installation_context_worker.py", "daemon_instance_runtime.pyz",
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
