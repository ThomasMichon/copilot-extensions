"""Opt-in real shell/helper/child custody, separate from unit and desktop proof."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import venv
import zipfile

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import _windows_birth
from agent_procutil.daemon_broker import WindowsDaemonProcess

pytestmark = pytest.mark.skipif(
    os.name != "nt"
    or os.environ.get("PROCUTIL_NATIVE_INSTANCE_LIFETIME") != "1"
    or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="explicit contained Windows instance-lifetime qualification",
)


@pytest.fixture(scope="module", params=["path", "test-venv"])
def interpreter(request, tmp_path_factory):
    python = shutil.which("python")
    assert python, "qualification requires setup-provided Python on PATH"
    if request.param == "path":
        return Path(python)
    root = tmp_path_factory.mktemp("instance-path-venv")
    venv.EnvBuilder(with_pip=False).create(root)
    return root / "Scripts" / "python.exe"


def _literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _pin(value: dict) -> WindowsDaemonProcess:
    process = WindowsDaemonProcess(value["pid"], ["instance-lifetime-fixture"])
    try:
        if _windows_birth(process._handle) != value["birth"] or process.poll() is not None:
            raise ProcessLookupError("fixture process identity changed before observation")
        return process
    except BaseException:
        process.close()
        raise


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize("cycle", [0, 1])
@pytest.mark.parametrize("scenario", ["expiry", "closed", "caller-loss", "helper-loss", "production"])
def test_original_shell_lifetime_without_compilation(tmp_path, interpreter, host, cycle, scenario):
    if host == "powershell":
        executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    else:
        executable = shutil.which("pwsh")
        assert executable, "the admitted matrix requires native PowerShell 7"
    package = Path(__file__).resolve().parents[1] / "src" / "agent_procutil"
    fixture = Path(__file__).with_name("fixture_daemon_instance_shell.ps1")
    command = (
        f"& {_literal(fixture)} -Root {_literal(tmp_path)} "
        f"-Helper {_literal(package / 'daemon_instances.ps1')} -Scenario {_literal(scenario)}"
    )
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", None)
    environment.pop("COPILOT_EXTENSIONS_DAEMON_INSTANCE", None)
    environment["PATH"] = str(interpreter.parent) + os.pathsep + environment.get("PATH", "")
    inherited_scopes = environment.get("COPILOT_EXTENSIONS_OWNED_JOB_SCOPES", "")
    parent = subprocess.Popen(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    pinned = []
    try:
        ready = tmp_path / "ready.json"
        until = time.monotonic() + 10
        while not ready.exists():
            if parent.poll() is not None:
                output, error = parent.communicate(timeout=5)
                pytest.fail(f"fixture ended before observation: {output!r} {error!r}")
            assert time.monotonic() < until, "fixture did not publish original identities"
            time.sleep(.01)
        details = json.loads(ready.read_text(encoding="utf-8-sig"))
        assert details["parent"]["pid"] == parent.pid
        for name in ("parent", "helper"):
            pinned.append(_pin(details[name]))
        original, helper = pinned
        child = _pin(details["child"]) if details["child"] is not None else None
        if child is not None:
            pinned.append(child)
        from agent_procutil import _daemon_instance_store as store

        record = store._read(Path(details["record_path"]))
        assert record["pid"] == record["launcher_pid"] == str(parent.pid)
        assert record["birth"] == details["parent"]["birth"]
        assert record["instance_id"] == details["instance_id"]
        assert record["pid"] != str(helper.pid)
        if scenario == "production":
            assert details["state"] == "finished"
            assert details["deadline"] == details["hard_deadline"] == "0"
            assert (details["job_scopes"] or "") == inherited_scopes
            time.sleep(1.2)
            assert original.poll() is helper.poll() is None
            original.terminate()
            original.wait(timeout=5)
            helper.wait(timeout=5)
        else:
            scopes = details["job_scopes"].split(";")
            assert ";".join(scopes[:-1]) == inherited_scopes
            assert re.fullmatch(r"Local\\ce-owned-job-[0-9a-f]{32}", scopes[-1])
            assert child is not None
            if scenario == "closed":
                assert details["state"] == "finished"
                time.sleep(1.2)
                assert time.time() >= float(details["deadline"])
                assert original.poll() is helper.poll() is child.poll() is None
            if scenario in ("closed", "helper-loss"):
                helper.terminate()
            elif scenario == "caller-loss":
                original.terminate()
            original.wait(timeout=6)
            helper.wait(timeout=5)
            child.wait(timeout=5)
            if scenario == "expiry":
                assert original.returncode == 1
        assert all(process.poll() is not None for process in pinned)
        assert (tmp_path / "ready.json").is_file(), "lifetime proof is not a GC artifact"
    finally:
        if parent.poll() is None:
            parent.terminate()
        parent.wait(timeout=5)
        for process in reversed(pinned):
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            finally:
                process.close()
        parent.communicate(timeout=5)


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize(
    "fault", ["missing-python", "version-guard", "startup-failure", "missing-source", "legacy-loaded"],
)
def test_shell_startup_refuses_before_instance_or_application_and_never_replays(
    tmp_path, interpreter, host, fault,
):
    if host == "powershell":
        executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    else:
        executable = shutil.which("pwsh")
        assert executable, "the admitted matrix requires native PowerShell 7"
    package = Path(__file__).resolve().parents[1] / "src" / "agent_procutil"
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    helper = helpers / "daemon_instances.ps1"
    source = (package / helper.name).read_text(encoding="utf-8")
    if fault == "version-guard":
        guard = "if sys.version_info < (3,10):"
        assert source.count(guard) == 1
        source = source.replace(guard, "if True:")
    helper.write_text(source, encoding="utf-8")
    archive = helpers / "daemon_instance_runtime.pyz"
    if fault == "startup-failure":
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(
                "agent_procutil/__init__.py", "raise RuntimeError('fixture startup import refusal')\n",
            )
    elif fault != "missing-source":
        shutil.copyfile(package / archive.name, archive)
    registry = tmp_path / "registry"
    entered = tmp_path / "entered"
    alias = ""
    if fault == "legacy-loaded":
        alias = (
            "$types=[psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators'); "
            "$types::Add('CopilotExtensions.DaemonInstanceNative',[string]); "
        )
    body = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
        f". {_literal(helper)}; "
        + alias
        + "try { "
        f"$guard=Enter-DaemonInstance -Service fixture -RuntimeRoot {_literal(tmp_path)} "
        f"-Source {_literal(helper)} -RegistryRoot {_literal(registry)} -Purpose production; "
        f"[IO.File]::WriteAllText({_literal(entered)}, 'unexpected application'); "
        "throw 'expected startup refusal' "
        "} catch { $errorMessage=$_.Exception.Message }; "
        "$state=[AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'); "
        f"try {{ $null=Invoke-DaemonInstanceReconcile -RegistryRoot {_literal(registry)}; "
        "throw 'unexpected replay' } catch { $replayMessage=$_.Exception.Message }; "
        "@{error=$errorMessage;replay=$replayMessage;broken=$state.broken;sequence=$state.sequence;"
        "processCreated=($null -ne $state.process);jobAdopted=($null -ne $state.job)} "
        "| ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", None)
    environment.pop("COPILOT_EXTENSIONS_DAEMON_INSTANCE", None)
    environment["PATH"] = (
        str(helpers) if fault == "missing-python"
        else str(interpreter.parent) + os.pathsep + environment.get("PATH", "")
    )
    result = subprocess.run(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=tmp_path, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    details = json.loads(result.stdout)
    expected = {
        "missing-python": "Python 3.10+ must be available as python on PATH",
        "version-guard": "Python 3.10+ is required on PATH",
        "startup-failure": "fixture startup import refusal",
        "missing-source": "source archive is unavailable",
        "legacy-loaded": "legacy compiled daemon-instance adapter is already loaded",
    }
    assert expected[fault] in details["error"]
    assert "automatic replay is refused" in details["replay"]
    assert details["broken"] is True and details["sequence"] == 0
    assert details["processCreated"] is (fault in ("version-guard", "startup-failure"))
    assert details["jobAdopted"] is False
    assert not registry.exists()
    assert not entered.exists()
