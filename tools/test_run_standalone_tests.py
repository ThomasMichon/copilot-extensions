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


@pytest.mark.parametrize("smoke", [False, True])
def test_run_is_contained_and_releases_shared_host_lease(tmp_path, monkeypatch, smoke):
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
        if smoke:
            assert command[5:7] == [
                str(root / "tests" / "test_cli.py"), str(root / "tests" / "test_config.py"),
            ]
        else:
            assert command[5] == str(root / "tests")
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert Path(kwargs["env"]["HOME"]) != Path.home()
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    args = ["agent-index-service", "--python", str(python)]
    if smoke:
        args.append("--smoke")
    assert runner.main(args) == 0
    assert captured["cwd"] == root
    assert released == [True]


def test_smoke_rejects_undefined_component_before_admission(monkeypatch):
    monkeypatch.setattr(runner, "acquire", lambda wait: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["worktree-manager", "--smoke"])
    assert exc.value.code == 2


def test_ci_path_gates_smoke_and_promotion_keeps_exhaustive():
    import yaml

    workflows = SCRIPT.parent.parent / ".github" / "workflows"
    ci = yaml.safe_load((workflows / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    promotion = yaml.safe_load(
        (workflows / "validate-and-promote.yml").read_text(encoding="utf-8")
    )["jobs"]
    discovery = next(step["run"] for step in ci["discover"]["steps"] if step.get("id") == "set")
    for path in ("agent-index-service/", "plugins/agent-index/", "libs/", "tools/"):
        assert path in discovery
    for name in ("agent-index-service-linux", "agent-index-service-windows"):
        assert ci[name]["needs"] == "discover"
        assert "needs.discover.outputs.standalone_service == 'true'" in ci[name]["if"]
        command = next(step["run"] for step in ci[name]["steps"] if "run" in step)
        assert 'MODE="--smoke"' in command
        assert "workflow_dispatch" in command
        full = next(step["run"] for step in promotion[name]["steps"] if "run-standalone-tests" in step.get("run", ""))
        assert "--smoke" not in full
