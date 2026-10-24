"""Mux metadata dispatch keeps installed retention and development entry modes."""

import sys

import pytest

from worktree_manager import mux_callback


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("relative", [False, True, "parent"])
def test_callback_command_preserves_entry_mode(tmp_path, monkeypatch, installed, relative):
    prefix = tmp_path / "versions" / "1.2.3+repair.fixture" if installed else tmp_path / "source" / ".venv"
    prefix.mkdir(parents=True)
    if installed:
        (prefix / ".runtime-bootstrap").mkdir()
        (prefix / ".runtime-bootstrap" / "runtime_launcher.py").touch()
        (prefix / ".install-complete.json").touch()
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(prefix / "python"))
    monkeypatch.setattr(mux_callback, "direct_daemon_python", lambda value: (value, {}))
    cwd = tmp_path / "caller" if relative == "parent" else tmp_path
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    selected = "../source-root" if relative == "parent" else "source-root"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", selected if relative else str(tmp_path / "source-root"))
    monkeypatch.setenv("PYTHONPATH", "development")
    command, env, root = mux_callback.callback_command(["remove", "--project=example", "--worktree-id=one"])
    assert command[-5:] == ["worktree_manager", "mux-daemon", "remove", "--project=example", "--worktree-id=one"]
    if installed:
        assert command[1:6] == ["-I", "-S", "-B", "-X", "utf8"]
        assert str(prefix / ".runtime-bootstrap" / "runtime_launcher.py") in command
        assert "PYTHONPATH" not in env
        assert root == tmp_path
    else:
        assert command[1] == "-m"
        assert env["PYTHONPATH"] == "development"
        assert root == tmp_path / "source-root"
    assert env["WORKTREE_MANAGER_ROOT"] == str(root)
    assert root.is_absolute()


def test_incomplete_numbered_callback_cannot_fall_back_to_source(tmp_path, monkeypatch):
    prefix = tmp_path / "versions" / "1.2.3"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    with pytest.raises(RuntimeError, match="constructed"):
        mux_callback.callback_command(["remove"])


def test_dispatch_reuses_canonical_background_primitive(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(mux_callback, "callback_command", lambda arguments: (
        ["python", "-m", "fixture"], {"FIXTURE": "yes"}, tmp_path,
    ))
    monkeypatch.setattr(mux_callback, "spawn_finite_worker", lambda command, **kwargs: captured.update(
        command=command, **kwargs,
    ))
    assert mux_callback.main(["register"]) == 0
    assert captured["cwd"] == tmp_path
    assert captured["log_path"] == tmp_path / "mux-callback.log"
    assert captured["console_children"] is True
    assert captured["instance"].lifecycle == "finite-worker"
