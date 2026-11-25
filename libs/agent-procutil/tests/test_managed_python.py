"""Exact prepared runtime selection, independent of application process custody."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import shutil
from types import SimpleNamespace
from typing import Any

import pytest

from agent_procutil import managed_python


@pytest.fixture
def selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    python = tmp_path / "completed runtime" / "python"
    python.parent.mkdir()
    python.touch()
    uv = tmp_path / ("uv.exe" if os.name == "nt" else "uv")
    uv.touch(mode=0o700)
    monkeypatch.setattr(shutil, "which", lambda name: str(uv))
    return python, uv


def test_exact_selection_is_isolated_bounded_and_prepared_only(selected: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    python, uv = selected
    before = dict(os.environ)
    for key in managed_python._SELECTION_ENV:
        monkeypatch.setenv(key, "foreign")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "custom managed store")
    calls = []

    def capture(command: Any, **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        assert kwargs["env"]["UV_NO_CONFIG"] == "1"
        assert kwargs["env"]["UV_OFFLINE"] == "1"
        assert kwargs["env"]["UV_PYTHON_INSTALL_DIR"] == "custom managed store"
        assert not (managed_python._SELECTION_ENV - {"UV_NO_CONFIG"}) & kwargs["env"].keys()
        return subprocess.CompletedProcess(command, 0, str(python) + "\r\n", "")

    monkeypatch.setattr(managed_python, "run_caller_owned", capture)
    inherited = dict(os.environ)
    assert managed_python.resolve_runtime_python(python) == str(python)
    assert os.environ == inherited
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        str(uv), "python", "find", "--no-project", "--managed-python",
        "--no-python-downloads", str(python),
    ]
    assert options["timeout"] == 10.0
    assert options["input_text"] == ""
    assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 65536
    assert before != inherited


@pytest.mark.parametrize("outcome", ["failed", "different", "empty", "multiline"])
def test_lookup_failure_never_falls_back(selected: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    python, _uv = selected
    calls = []

    def capture(command: Any, **kwargs: Any) -> Any:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 42 if outcome == "failed" else 0,
            {"different": str(python.parent / "other"), "empty": "",
             "multiline": str(python) + "\nextra"}.get(outcome, ""), "refused",
        )

    monkeypatch.setattr(managed_python, "run_caller_owned", capture)
    with pytest.raises(RuntimeError, match="installation management"):
        managed_python.resolve_runtime_python(python)
    assert len(calls) == 1


def test_explicit_uv_is_not_rediscovered(selected: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    python, uv = selected
    monkeypatch.setattr(shutil, "which", lambda name: pytest.fail("PATH discovery"))
    monkeypatch.setattr(
        managed_python, "run_caller_owned",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=str(python)),
    )
    assert managed_python.resolve_runtime_python(python, uv=uv) == str(python)


@pytest.mark.parametrize("missing", ["python", "uv", "relative-uv", "empty-uv"])
def test_missing_preparation_refuses_before_native_creation(selected: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    python, uv = selected
    monkeypatch.setattr(
        managed_python, "run_caller_owned", lambda *a, **k: pytest.fail("process created"),
    )
    if missing == "python":
        python = python.with_name("absent")
    elif missing == "uv":
        monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        if missing in {"relative-uv", "empty-uv"}:
            managed_python.resolve_runtime_python(
                python, uv="relative-uv" if missing == "relative-uv" else "",
            )
        else:
            managed_python.resolve_runtime_python(python)


def test_unsettled_capture_is_not_retried(selected: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    python, _uv = selected
    failure = subprocess.TimeoutExpired("uv", 10)

    def capture(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(managed_python, "run_caller_owned", capture)
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        managed_python.resolve_runtime_python(python)
    assert raised.value is failure
