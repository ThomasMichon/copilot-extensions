"""Prepared-only admission at the hosted server interpreter boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import SyncProcessLaunchError
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError

from agent_index import __main__ as cli
from agent_index import transport


@pytest.fixture
def selected_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    python = tmp_path / "server environment" / "python"
    monkeypatch.setattr(cli, "host_runtime_python", lambda: python)
    monkeypatch.setattr(transport, "plan_route", lambda: ("host", None))
    monkeypatch.setattr(transport, "maybe_delegate", lambda *_args: None)
    monkeypatch.setattr(cli, "_validate_cell_start_authority", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli, "serve", lambda *_a, **_kw: pytest.fail("unadmitted server started"),
    )
    return python


def test_handoff_admits_selected_server_once_before_original_spawn(
    selected_server: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    events: list[str] = []
    binding = str(tmp_path / "bound UV")
    monkeypatch.setenv("AGENT_INDEX_UV", binding)
    monkeypatch.setenv("PYTHONPATH", "foreign imports")
    monkeypatch.setenv("PYTHONHOME", "foreign home")
    monkeypatch.setattr(cli, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "python_cache_args", lambda *_a, **_kw: ["-B"])
    inherited = dict(os.environ)

    def select() -> Path:
        events.append("select")
        return selected_server

    def admit(python: Path, *, uv: str | None = None) -> str:
        assert python == selected_server and uv == binding
        events.append("admit")
        return str(python)

    def spawn(command: list[str], **kwargs: Any) -> Any:
        events.append("spawn")
        assert command == [
            str(selected_server), "-I", "-B", "-X", "utf8", "-m", "agent_index",
            "start", "--host", "127.0.0.1", "--port", "8123", "--passive",
        ]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"] == {
            key: value for key, value in inherited.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        assert kwargs.keys() == {"cwd", "env"}
        return SimpleNamespace(wait=lambda: 17)

    monkeypatch.setattr(cli, "host_runtime_python", select)
    monkeypatch.setattr(cli, "resolve_runtime_python", admit)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    assert cli._dispatch_to_server_venv(
        argparse.Namespace(host="127.0.0.1", port=8123, passive=True), "start",
    ) == 17
    assert events == ["select", "admit", "spawn"]
    assert dict(os.environ) == inherited


def test_complete_current_runtime_needs_no_admission_or_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli, "host_runtime_python", lambda: Path(os.path.abspath(sys.executable)),
    )
    monkeypatch.setattr(
        cli, "resolve_runtime_python", lambda *_a, **_kw: pytest.fail("unneeded lookup"),
    )
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_a, **_kw: pytest.fail("unneeded child"),
    )
    assert cli._dispatch_to_server_venv(argparse.Namespace(), "start") is None


@pytest.mark.parametrize("command", ["start", "__cell-start", "search"])
@pytest.mark.parametrize(
    "failure", ["missing", "different", "deadline", "output", "decode", "os", "native-launch"],
)
def test_hosted_commands_report_admission_refusal_without_spawn_or_retry(
    selected_server: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str, failure: str,
) -> None:
    errors = {
        "missing": FileNotFoundError("UV is unavailable; use explicit installation management."),
        "different": RuntimeError("different selected runtime; use explicit installation management."),
        "deadline": subprocess.TimeoutExpired("uv", 10),
        "output": OutputLimitExceeded("stdout", 65536),
        "decode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
        "os": OSError(8, "invalid executable"),
        "native-launch": SyncProcessLaunchError(OSError(13, "execution refused")),
    }
    attempts: list[Path] = []

    def refuse(python: Path, **_kwargs: Any) -> str:
        attempts.append(python)
        raise errors[failure]

    monkeypatch.setattr(cli, "resolve_runtime_python", refuse)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_a, **_kw: pytest.fail("unadmitted child"),
    )
    args = [command, "needle"] if command == "search" else [command]
    assert cli.main(args) == 1
    diagnostic = json.loads(capsys.readouterr().out)
    assert "explicit installation management" in diagnostic["error"]
    assert diagnostic["hits"] == []
    assert attempts == [selected_server]


@pytest.mark.parametrize("command", ["start", "__cell-start", "search"])
def test_unconfirmed_lookup_cleanup_propagates_original_custody(
    selected_server: Path, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    process = SimpleNamespace(pid=123, returncode=None)
    job = object()
    resource = object()
    original = OwnedCleanupError(
        process, job, cleanup_resources=(("test", resource),), phase="test",
    )
    attempts: list[Path] = []

    def refuse(python: Path, **_kwargs: Any) -> str:
        attempts.append(python)
        raise original

    monkeypatch.setattr(cli, "resolve_runtime_python", refuse)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_a, **_kw: pytest.fail("unadmitted child"),
    )
    args = [command, "needle"] if command == "search" else [command]
    with pytest.raises(OwnedCleanupError) as caught:
        cli.main(args)
    assert caught.value is original
    assert original.process is process and original.job is job
    assert original.cleanup_resources == (("test", resource),)
    assert attempts == [selected_server]


def test_handoff_does_not_hide_programming_errors(
    selected_server: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> str:
        raise TypeError("invalid application call")

    monkeypatch.setattr(cli, "resolve_runtime_python", broken)
    with pytest.raises(TypeError, match="invalid application call"):
        cli._dispatch_to_server_venv(argparse.Namespace(), "start")


def test_stop_without_client_url_keeps_normal_not_running_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "client_url", lambda: None)
    monkeypatch.setattr(cli, "discovered_endpoint", lambda: None)
    assert cli.cmd_stop(argparse.Namespace()) == 0
    assert json.loads(capsys.readouterr().out) == {"stopped": False, "reason": "not-running"}
