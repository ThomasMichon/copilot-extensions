"""Windows census failures retain refusal semantics and operation-specific bounds."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "libs" / "installer-engine" / "installer_engine_uninstall.py"
SPEC = importlib.util.spec_from_file_location("census_refusal_test_subject", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ownership census adapter")


@pytest.mark.parametrize("descendants", (False, True))
def test_census_outlives_probe_budget_without_extending_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, descendants: bool,
) -> None:
    payload, root = tmp_path / "payload", tmp_path / "runtime"
    shell = "fixture-powershell.exe"
    profile = MODULE.Profile((), (), (), (), "fixture_module")
    rows = [
        {"pid": os.getpid(), "ppid": os.getppid(), "image": sys.executable,
         "command": "fixture inspector", "created": 123456789012345678},
        {"pid": 12345, "ppid": os.getpid(), "image": str(root / "python.exe"),
         "command": 'fixture "quoted Unicode \u03bb"', "created": 123456789012345679},
    ]
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        assert kwargs["capture_output"] and kwargs["text"]
        assert kwargs["creationflags"] == int(getattr(subprocess, "CREATE_NO_WINDOW"))
        if "Get-CimInstance Win32_Process" in command[-1]:
            assert f"$descendants=${str(descendants).lower()}" in command[-1]
            if kwargs["timeout"] < 60:
                raise subprocess.TimeoutExpired(command[0], kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        return subprocess.CompletedProcess(command, 0, "probe result", "")

    monkeypatch.setattr(MODULE, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    monkeypatch.setattr(MODULE, "_dependency", lambda owner, name: SOURCE.with_name(name))
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    assert MODULE.inspect_processes(payload, root, shell, profile, descendants=descendants) == rows
    assert len(calls) == 1
    assert 60 <= calls[0][1]["timeout"] <= 180
    assert MODULE._run(payload, [shell, "fixture probe"]) == "probe result"
    MODULE._native_remove(payload, shell, root, ("versions",))
    assert [kwargs["timeout"] for _, kwargs in calls[1:]] == [45, 90]


@pytest.mark.parametrize("failure", ("timeout", "owner", "identity", "missing-inspector"))
def test_census_failure_never_becomes_empty_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    profile = MODULE.Profile((), (), (), (), "fixture_module")
    calls: list[float] = []
    messages = {
        "owner": "Cannot attribute candidate process ownership",
        "identity": "Process identity changed during inspection",
    }

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs["timeout"])
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command[0], kwargs["timeout"])
        if failure in messages:
            return subprocess.CompletedProcess(command, 1, "", messages[failure])
        return subprocess.CompletedProcess(command, 0, "[]", "")

    monkeypatch.setattr(MODULE, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    monkeypatch.setattr(MODULE, "_dependency", lambda owner, name: SOURCE.with_name(name))
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    error = subprocess.TimeoutExpired if failure == "timeout" else MODULE.UninstallRefused
    match = (None if failure == "timeout" else messages.get(
        failure, "Process inventory did not include its inspector",
    ))
    with pytest.raises(error, match=match):
        MODULE.inspect_processes(tmp_path / "payload", tmp_path / "runtime",
                                 "fixture-powershell.exe", profile, descendants=True)
    assert len(calls) == 1 and 60 <= calls[0] <= 180
