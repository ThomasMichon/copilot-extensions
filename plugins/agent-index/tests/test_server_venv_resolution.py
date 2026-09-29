"""agent-index-server-venv-split: sibling SERVER venv interpreter resolution.

``config.server_venv_python()`` is deliberately inert scaffolding today --
no installer yet provisions a ``server`` sibling, so every existing
single-venv layout must keep resolving to ``None`` and every caller must keep
falling back to its current behavior unchanged. These tests pin both that
inertness and the resolution convention itself, so a future installer change
can start actually provisioning the sibling venv with no further code changes
needed here.

The convention is deliberately name-agnostic about the venv root (a
``server`` subdirectory of whatever directory contains the current
interpreter's own ``Scripts``/``bin`` folder) -- a real deployed process
never runs from a directory literally named ``.venv`` (marker-only
activation pins the interpreter straight to ``versions/<version>/Scripts``
or ``.../bin``; see ``Invoke-VersionedActivate``'s own ``--no-link``), so
the resolver must not assume that name, only the ``Scripts``/``bin`` shape
one level up.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

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
