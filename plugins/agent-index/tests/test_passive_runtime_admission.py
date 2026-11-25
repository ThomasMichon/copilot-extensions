"""Prepared-only admission retains the passive cutover's existing ownership."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import SyncProcessLaunchError
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError
from zdd import breadcrumb, routing
from zdd.cutover_lock import CutoverLock

from agent_index import __main__ as cli
from agent_index.config import Config


@pytest.fixture
def passive_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "host runtime"
    python = tmp_path / "selected server" / "python"
    monkeypatch.setenv("AGENT_INDEX_HOME", str(home))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    for key in ("AGENT_INDEX_INSTALLATION_ID", "AGENT_INDEX_RUN_DIR", "AGENT_INDEX_ROUTING_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "_validated_cell_transaction", lambda: None)
    monkeypatch.setattr(cli, "load_config", lambda: Config(host="127.0.0.1", port=8123))
    monkeypatch.delenv("AGENT_INDEX_LOG_LEVEL", raising=False)
    monkeypatch.setattr(cli, "_routing_endpoint", lambda: None)
    monkeypatch.setattr(cli, "host_runtime_python", lambda: python)
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover", lambda *_a, **_kw: {"recovered": False},
    )
    return home, python


@pytest.mark.parametrize("namespaced", [False, True])
def test_passive_admits_once_before_original_daemon_creation(
    passive_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], namespaced: bool,
) -> None:
    home, python = passive_runtime
    binding = str(home.parent / "bound UV")
    monkeypatch.setenv("AGENT_INDEX_UV", binding)
    monkeypatch.setenv("PYTHONPATH", "foreign imports")
    monkeypatch.setenv("PYTHONHOME", "foreign home")
    if namespaced:
        monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "test-cell/agent-index")
    monkeypatch.setattr(cli, "python_cache_args", lambda *_a, **_kw: ["-B"])
    monkeypatch.setattr(cli, "windowless_python", lambda value: value + "w")
    monkeypatch.setattr(cli, "windowless_python_env", lambda value: {"COMPANION": value})
    inherited = dict(os.environ)
    events: list[str] = []
    callbacks: dict[str, Any] = {}
    handle = SimpleNamespace(pid=4321)

    def select() -> Path:
        events.append("select")
        return python

    def admit(selected: str, *, uv: str | None = None) -> str:
        assert selected == str(python) and uv == binding
        assert not home.exists()
        events.append("admit")
        return selected

    def spawn(command: list[str], **kwargs: Any) -> Any:
        events.append("spawn")
        start = "__cell-start" if namespaced else "start"
        assert command == [
            str(python) + "w", "-I", "-B", "-X", "utf8", "-m", "agent_index",
            start, "--host", "127.0.0.1", "--port", "4555", "--passive",
        ]
        assert home.is_dir()
        assert kwargs["cwd"] == home
        assert kwargs["log_path"] == home / "service-stdio.log"
        assert kwargs["env"] == {
            key: value for key, value in inherited.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        assert kwargs["env_overrides"] == {
            "COMPANION": str(python), "AGENT_INDEX_LOG_LEVEL": "warning",
        }
        assert kwargs["instance"].service == "agent-index"
        assert kwargs["instance"].runtime_root == home
        assert kwargs["instance"].source == Path(cli.__file__).resolve()
        return handle

    class CaptureCutover:
        def __init__(self, _root: Path, **kwargs: Any) -> None:
            callbacks.update(kwargs)

        def run(self, **_kwargs: Any) -> Any:
            assert callbacks["spawn_passive"](4555) is handle
            return SimpleNamespace(ok=False, to_dict=lambda: {"ok": False})

    monkeypatch.setattr(cli, "host_runtime_python", select)
    monkeypatch.setattr(cli, "resolve_runtime_python", admit)
    monkeypatch.setattr(cli, "spawn_daemon", spawn)
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", CaptureCutover)
    assert cli.cmd_deploy(argparse.Namespace(
        json=True, health_timeout=1.0, drain_timeout=1.0, force=False,
    )) == 1
    assert events == ["select", "admit", "spawn"]
    assert dict(os.environ) == inherited
    assert json.loads(capsys.readouterr().out) == {"ok": False}


@pytest.mark.parametrize(
    "failure", ["missing", "different", "deadline", "output", "decode", "os", "native-launch", "cleanup"],
)
def test_passive_admission_failure_keeps_cutover_witness_and_original_custody(
    passive_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    home, python = passive_runtime
    process, job, resource = SimpleNamespace(pid=123, returncode=None), object(), object()
    errors = {
        "missing": FileNotFoundError("Selected UV is unavailable"),
        "different": RuntimeError("different selected runtime"),
        "deadline": subprocess.TimeoutExpired("uv", 10),
        "output": OutputLimitExceeded("stdout", 65536),
        "decode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
        "os": OSError(8, "invalid executable"),
        "native-launch": SyncProcessLaunchError(OSError(13, "execution refused")),
        "cleanup": OwnedCleanupError(
            process, job, cleanup_resources=(("test", resource),), phase="test",
        ),
    }
    original = errors[failure]
    attempts: list[str] = []
    route_root = home.parent / "routing"
    monkeypatch.setenv("AGENT_INDEX_ROUTING_DIR", str(route_root))
    # No socket or daemon is needed to exercise the real serialized cutover.
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator._await_health", lambda *_a: pytest.fail("unadmitted health"))
    monkeypatch.setattr("zdd.cutover.candidate_port", lambda *_a: 4555)

    def refuse(selected: str, **_kwargs: Any) -> str:
        attempts.append(selected)
        assert not home.exists()
        raise original

    monkeypatch.setattr(cli, "resolve_runtime_python", refuse)
    monkeypatch.setattr(cli, "spawn_daemon", lambda *_a, **_kw: pytest.fail("unadmitted daemon"))
    with pytest.raises(type(original)) as caught:
        cli.cmd_deploy(argparse.Namespace(
            json=True, health_timeout=1.0, drain_timeout=1.0, force=False,
        ))
    assert caught.value is original
    assert attempts == [str(python)]
    assert not home.exists()
    witness = breadcrumb.read_breadcrumb(route_root)
    assert witness is not None and witness["state"] == "started"
    assert not witness.get("new_pid")
    assert routing.read_table(route_root) is None
    lock = CutoverLock(route_root)
    lock.acquire(timeout=0)
    lock.release()
    if isinstance(original, OwnedCleanupError):
        assert original.process is process and original.job is job
        assert original.cleanup_resources == (("test", resource),)
