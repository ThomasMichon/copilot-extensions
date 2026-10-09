from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("run-standalone-tests.py")
SPEC = importlib.util.spec_from_file_location("run_standalone_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_prepare_composes_local_core_and_declared_service_extras(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: calls.append(command))
    python = tmp_path / ".test-venvs" / "service" / "bin" / "python"

    runner.prepare("agent-index-service", python)

    assert calls[0] == ["uv", "venv", str(python.parent.parent)]
    assert calls[1][:5] == ["uv", "pip", "install", "--python", str(python)]
    assert str(tmp_path / "plugins" / "agent-index") + "[store,server]" in calls[1]
    assert calls[1][-1] == str(tmp_path / "agent-index-service") + "[native,test]"


def test_prepare_rejects_host_interpreter_before_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    called = []
    monkeypatch.setattr(runner, "acquire", lambda wait: called.append(wait))

    with pytest.raises(SystemExit) as exc:
        runner.main([
            "agent-index-service", "--prepare", "--python", str(tmp_path / "host" / "python")
        ])

    assert exc.value.code == 2
    assert called == []


def test_run_is_contained_and_releases_shared_host_lease(tmp_path, monkeypatch):
    root = tmp_path / "agent-index-service"
    (root / "tests").mkdir(parents=True)
    python = tmp_path / "test-python"
    python.touch()
    monkeypatch.setattr(runner, "REPO", tmp_path)
    released = []

    class Lease:
        def release(self):
            released.append(True)

    monkeypatch.setattr(runner, "acquire", lambda wait: Lease())
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        assert command[1:4] == ["-I", "-m", "pytest"]
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert Path(kwargs["env"]["HOME"]) != Path.home()
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.main(["agent-index-service", "--python", str(python)]) == 0
    assert captured["cwd"] == root
    assert released == [True]
