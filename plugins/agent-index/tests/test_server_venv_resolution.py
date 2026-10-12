"""Slot-local server resolution and explicit unavailable-host outcomes."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index import __main__, config


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_returns_none_with_no_env_override_and_no_sibling_venv(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    fake_python = tmp_path / ".venv" / "bin" / "python3"
    _make_executable(fake_python)
    monkeypatch.setattr(sys, "executable", str(fake_python))

    assert config.server_venv_python() is None


def test_resolves_sibling_server_venv_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    fake_python = tmp_path / ".venv" / "bin" / "python3"
    _make_executable(fake_python)
    server_python = tmp_path / ".venv" / "server" / "bin" / "python3"
    _make_executable(server_python)
    monkeypatch.setattr(sys, "executable", str(fake_python))

    resolved = config.server_venv_python()
    assert resolved == server_python.resolve()


def test_resolves_sibling_server_venv_windows_shape(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    fake_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    _make_executable(fake_python)
    server_python = tmp_path / ".venv" / "server" / "Scripts" / "python.exe"
    _make_executable(server_python)
    monkeypatch.setattr(sys, "executable", str(fake_python))

    resolved = config.server_venv_python()
    assert resolved == server_python.resolve()


def test_resolves_sibling_server_venv_under_real_deployed_versions_layout(
    monkeypatch, tmp_path
):
    """A real installed runtime is pinned directly to
    ``versions/<version>/Scripts`` (or ``.../bin``) via marker-only
    activation -- there is no ``.venv``-named directory anywhere in a
    deployed process's own path. The resolver must work from this shape
    too, not just a plain local dev venv."""
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    fake_python = tmp_path / "versions" / "1.2.3" / "Scripts" / "python.exe"
    _make_executable(fake_python)
    server_python = tmp_path / "versions" / "1.2.3" / "server" / "Scripts" / "python.exe"
    _make_executable(server_python)
    monkeypatch.setattr(sys, "executable", str(fake_python))

    resolved = config.server_venv_python()
    assert resolved == server_python.resolve()


def test_returns_none_when_no_server_subdirectory_exists_regardless_of_root_name(
    monkeypatch, tmp_path
):
    # No installer has provisioned a "server" sibling yet -- never guess.
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    fake_python = tmp_path / "some-other-venv" / "bin" / "python3"
    _make_executable(fake_python)
    monkeypatch.setattr(sys, "executable", str(fake_python))

    assert config.server_venv_python() is None


def test_env_override_takes_precedence(monkeypatch, tmp_path):
    fake_python = tmp_path / ".venv" / "bin" / "python3"
    _make_executable(fake_python)
    override_python = tmp_path / "elsewhere" / "python3"
    _make_executable(override_python)
    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(override_python))

    assert config.server_venv_python() == override_python


def test_env_override_to_missing_path_falls_back_to_none(monkeypatch, tmp_path):
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(tmp_path / "nonexistent"))

    assert config.server_venv_python() is None


def test_current_runtime_override_normalizes_parent_segments(monkeypatch, tmp_path):
    current = tmp_path / "venv" / "bin" / "python3"
    _make_executable(current)
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(current))
    monkeypatch.setenv(
        config.SERVER_VENV_PYTHON_ENV, str(current.parent / ".." / "bin" / current.name)
    )
    assert config.host_runtime_python() == current


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv interpreter symlink contract")
def test_resolves_slot_sibling_without_following_base_interpreter_symlink(monkeypatch, tmp_path):
    current = tmp_path / "versions" / "1.2.3" / "bin" / "python3"
    server = current.parent.parent / "server" / "bin" / "python3"
    current.parent.mkdir(parents=True)
    server.parent.mkdir(parents=True)
    current.symlink_to(sys.executable)
    server.symlink_to(sys.executable)
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(current))
    assert config.server_venv_python() == server
    assert config.host_runtime_python() == server


def test_missing_host_dependencies_refuse_light_runtime_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python3"))
    monkeypatch.setattr(config.importlib.util, "find_spec", lambda _name: None)
    with pytest.raises(RuntimeError, match="host runtime is unavailable"):
        config.host_runtime_python()


def test_missing_explicit_server_is_not_replaced_by_complete_current_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="configured agent-index server interpreter"):
        config.host_runtime_python()


def test_complete_owned_host_runtime_needs_no_sibling(monkeypatch, tmp_path):
    current = tmp_path / "bin" / "python3"
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(current))
    monkeypatch.setattr(config.importlib.util, "find_spec", lambda _name: object())
    assert config.host_runtime_python() == current


def test_spawn_passive_uses_server_venv_python_when_provisioned(
    monkeypatch, tmp_path, capsys
):
    """cmd_deploy's spawn_passive dispatches into the SERVER venv's own
    interpreter when one is provisioned, instead of always reusing
    ``sys.executable`` -- so the passive instance never loads fastapi/
    uvicorn/pydantic into a client-shaped venv once the split is complete."""
    captured = {}
    spawned = {}

    class FakeResult:
        ok = True
        steps = ("ok",)
        new_port = 4444
        rolled_back = False
        error = None

        def to_dict(self):
            return {"ok": True, "new_port": self.new_port, "steps": self.steps}

    class FakeOrchestrator:
        def __init__(self, config_dir, **kwargs):
            captured["config_dir"] = config_dir
            captured.update(kwargs)

        def run(self, **kwargs):
            captured["run"] = kwargs
            return FakeResult()

    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    override_python = tmp_path / "server-venv" / "python3"
    _make_executable(override_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(override_python))
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover", lambda *a, **k: {"recovered": False}
    )
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", FakeOrchestrator)
    routes = iter(
        [
            None,
            SimpleNamespace(
                base_url="http://127.0.0.1:4444",
                pid=None,
                version="test",
            ),
        ]
    )
    monkeypatch.setattr(__main__, "_routing_endpoint", lambda: next(routes))
    monkeypatch.setattr(
        __main__,
        "_owned_service_status",
        lambda *_args, **_kwargs: {"installationId": "", "version": "test"},
    )

    class Handle:
        pid = 9911

    def popen(command, **kwargs):
        spawned["command"] = command
        spawned["kwargs"] = kwargs
        return Handle()

    monkeypatch.setattr(__main__.subprocess, "Popen", popen)

    rc = __main__.cmd_deploy(
        __main__.build_parser().parse_args(["deploy", "--json"])
    )
    assert rc == 0

    handle = captured["spawn_passive"](4555)
    assert handle.pid == 9911
    assert str(override_python) in spawned["command"][0]
