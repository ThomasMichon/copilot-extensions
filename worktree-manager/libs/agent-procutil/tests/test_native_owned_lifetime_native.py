"""Opt-in pinned native writers, helper loss and runspace cancellation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from agent_procutil import no_window_kwargs
from test_daemon_shell_lifetime_native import _pin
from test_daemon_shell_lifetime_native import interpreter as interpreter

pytestmark = pytest.mark.skipif(
    os.name != "nt"
    or os.environ.get("PROCUTIL_NATIVE_OWNED_LIFETIME") != "1"
    or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="explicit contained Windows finite-writer lifetime qualification",
)


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize("cycle", [0, 1])
@pytest.mark.parametrize("scenario", ["caller-loss", "helper-loss", "cancel"])
def test_original_shell_owns_helper_and_all_writers(
    tmp_path: Path, interpreter: Path, host: str, cycle: int, scenario: str,
) -> None:
    if host == "powershell":
        executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    else:
        executable = shutil.which("pwsh")
        assert executable, "the admitted matrix requires native PowerShell 7"
        executable = Path(executable)
    fixture = Path(__file__).with_name("fixture_native_owned_lifetime.ps1")
    helper = Path(__file__).resolve().parents[1] / "src/agent_procutil/native_owned.ps1"
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", None)
    environment.pop("COPILOT_EXTENSIONS_DAEMON_INSTANCE", None)
    environment["PATH"] = str(interpreter.parent) + os.pathsep + environment.get("PATH", "")
    parent = subprocess.Popen(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
         "-Root", str(tmp_path), "-Executable", str(executable), "-Helper", str(helper),
         "-Scenario", scenario],
        cwd=tmp_path, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    pinned = {}
    try:
        until = time.monotonic() + 18
        while not (tmp_path / "ready.closed").is_file():
            if parent.poll() is not None:
                output, error = parent.communicate(timeout=5)
                pytest.fail(f"fixture ended before observation: {output!r} {error!r}")
            assert time.monotonic() < until, "finite-writer identities were not published"
            time.sleep(.01)
        details = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8-sig"))
        assert details["parent"]["pid"] == parent.pid
        identities = {}
        for role in ("parent", "helper", "launcher", "writer", "leaf"):
            identity = details[role]
            key = identity["pid"], identity["birth"]
            if key not in identities:
                identities[key] = _pin(identity)
            pinned[role] = identities[key]
        assert len({pinned[role].pid for role in ("parent", "helper", "writer", "leaf")}) == 4
        if scenario == "caller-loss":
            pinned["parent"].terminate()
        else:
            if scenario == "helper-loss":
                pinned["helper"].terminate()
            (tmp_path / "release").write_text("observe-retirement", encoding="ascii")
        for process in identities.values():
            process.wait(timeout=6)
        output, error = parent.communicate(timeout=5)
        if scenario != "caller-loss":
            assert parent.returncode == 0, (output, error)
            assert (tmp_path / "completed.closed").is_file()
            result = json.loads((tmp_path / "completed.json").read_text(encoding="utf-8-sig"))
            if scenario == "cancel":
                assert result == {"cancelled": True, "broken": True, "helper_job_closed": True}
            else:
                assert result == {"status": "refused", "cleanup_confirmed": False}
        assert all(process.poll() is not None for process in identities.values())
        assert (tmp_path / "ready.closed").is_file(), "proof must precede cleanup"
    finally:
        if parent.poll() is None:
            parent.terminate()
        parent.wait(timeout=5)
        try:
            for process in set(pinned.values()):
                try:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)
                finally:
                    process.close()
        finally:
            for stream in (parent.stdout, parent.stderr):
                if stream is not None:
                    stream.close()
