"""Native bootstrap dependency handoff from the contained runner."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_previous_path = sys.path.copy()
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    _spec = importlib.util.spec_from_file_location(
        "bootstrap_prerequisite_runner", Path(__file__).with_name("run-plugin-tests.py"),
    )
    runner = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(runner)
finally:
    sys.path[:] = _previous_path


@pytest.mark.parametrize("name", ["agent-worktrees", "example"])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
def test_bootstrap_cache_is_prepared_through_seasoned_install(
    tmp_path, monkeypatch, name, cached, prepared,
):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    monkeypatch.setattr(runner, "_has_pyproject", lambda _name: True)
    monkeypatch.setattr(runner, "_has_dev_extra", lambda _name: True)
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda _name: "current")
    venv = runner.VENV_ROOT / name
    venv.mkdir(parents=True)
    python = runner._venv_python(name)
    cache = venv / ".bootstrap-uv-cache"
    if prepared:
        cache.mkdir()
    if cached:
        python.parent.mkdir(parents=True)
        python.touch()
        (venv / ".dep-fingerprint").write_text("current", encoding="utf-8")
    installations = []

    def command(argv, **kwargs):
        assert argv[:2] == ["uv", "venv"]
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        return subprocess.CompletedProcess(argv, 0)

    def seasoned_install(uv, arguments, **kwargs):
        assert uv == "uv"
        assert kwargs["trusted_source_roots"] == (runner.REPO,)
        assert kwargs["cwd"] == plugin
        assert kwargs["check"]
        installations.append(arguments)
        if name == "agent-worktrees":
            assert arguments[-2:] == ["--cache-dir", str(cache)]
            cache.mkdir()
        else:
            assert "--cache-dir" not in arguments

    monkeypatch.setattr(runner.subprocess, "run", command)
    monkeypatch.setattr(runner, "run_uv_install", seasoned_install)
    assert runner._ensure_venv(name, "uv", reinstall=False) == python
    rebuild = not cached or (name == "agent-worktrees" and not prepared)
    assert len(installations) == int(rebuild)
    assert (venv / ".dep-fingerprint").read_text() == "current"
    if name == "agent-worktrees":
        assert cache.is_dir()


def test_bootstrap_cache_changes_dependency_fingerprint(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    assert runner._dep_fingerprint("agent-worktrees") != runner._dep_fingerprint("example")
