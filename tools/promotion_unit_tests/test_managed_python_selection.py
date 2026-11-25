"""Actor-free work-count and refusal controls for prepared runtime selection."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

from agent_procutil import managed_python


@pytest.mark.parametrize("outcome", ["selected", "different", "failed"])
def test_prepared_lookup_has_one_isolated_offline_call(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    python = os.path.abspath("selected-runtime/bin/python")
    uv = os.path.abspath("prepared-tools/uv.exe")
    monkeypatch.setattr(Path, "is_file", lambda path: True)
    monkeypatch.setattr(os, "access", lambda *args: True)
    monkeypatch.setattr(shutil, "which", lambda name: uv)
    monkeypatch.setenv("UV_PYTHON", "unrelated")
    monkeypatch.setenv("UV_CONFIG_FILE", "unrelated")
    monkeypatch.setenv("VIRTUAL_ENV", "unrelated")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "configured-store")
    calls: list[list[str]] = []

    def captured(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["timeout"] == 10.0 and kwargs["input_text"] == ""
        assert kwargs["max_stdout_bytes"] == kwargs["max_stderr_bytes"] == 65536
        environment = kwargs["env"]
        assert environment["UV_OFFLINE"] == environment["UV_NO_CONFIG"] == "1"
        assert environment["UV_PYTHON_INSTALL_DIR"] == "configured-store"
        assert not {"UV_PYTHON", "UV_CONFIG_FILE", "VIRTUAL_ENV"} & environment.keys()
        return subprocess.CompletedProcess(
            command, 42 if outcome == "failed" else 0,
            python + "\n" if outcome == "selected" else "different\n", "",
        )

    monkeypatch.setattr(managed_python, "run_caller_owned", captured)
    inherited = dict(os.environ)
    if outcome == "selected":
        assert managed_python.resolve_runtime_python(python) == python
    else:
        with pytest.raises(RuntimeError, match="installation management"):
            managed_python.resolve_runtime_python(python)
    assert dict(os.environ) == inherited
    assert calls == [[
        uv, "python", "find", "--no-project", "--managed-python",
        "--no-python-downloads", python,
    ]]


def test_missing_uv_never_starts_an_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda path: True)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        managed_python, "run_caller_owned", lambda *a, **k: pytest.fail("actor started"),
    )
    with pytest.raises(FileNotFoundError, match="UV is unavailable"):
        managed_python.resolve_runtime_python(os.path.abspath("selected-runtime/bin/python"))
