from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("run-standalone-tests.py")
SPEC = importlib.util.spec_from_file_location("run_standalone_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("smoke", [False, True])
def test_prepare_composes_local_core_and_declared_service_extras(tmp_path, monkeypatch, smoke):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    calls = []
    metadata = tmp_path / "plugins" / "agent-index" / "pyproject.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('[project]\nversion = "0.1.0-dev210"\n')

    def install(command, **kwargs):
        calls.append(command)
        if "--override" in command:
            override = Path(command[command.index("--override") + 1])
            core_extra = "" if smoke else "[store,server]"
            assert override.read_text() == f"agent-index{core_extra} @ {metadata.parent.as_uri()}\n"

    monkeypatch.setattr(runner.subprocess, "run", install)
    python = tmp_path / ".test-venvs" / "service" / "bin" / "python"

    runner.prepare("agent-index-service", python, smoke=smoke)

    assert calls[0] == ["uv", "venv", str(python.parent.parent)]
    assert calls[1][:5] == ["uv", "pip", "install", "--python", str(python)]
    core_extra = "" if smoke else "[store,server]"
    service_extra = "[test]" if smoke else "[native,test]"
    assert str(tmp_path / "plugins" / "agent-index") + core_extra in calls[1]
    assert calls[1][-1] == str(tmp_path / "agent-index-service") + service_extra
    assert not Path(calls[1][calls[1].index("--override") + 1]).exists()


def test_prepare_rejects_host_interpreter_before_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    called = []
    monkeypatch.setattr(runner, "acquire", lambda wait, root: called.append(wait))

    with pytest.raises(SystemExit) as exc:
        runner.main([
            "agent-index-service", "--prepare", "--python", str(tmp_path / "host" / "python")
        ])

    assert exc.value.code == 2
    assert called == []


def test_prepare_rejects_managed_launcher_with_host_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "agent-index-service" / "tests").mkdir(parents=True)
    python = tmp_path / ".test-venvs" / "fake" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(runner, "interpreter_environment", lambda _: tmp_path / "host-environment")
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    monkeypatch.setattr(runner, "prepare", lambda *_a, **_k: pytest.fail("unexpected preparation"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["agent-index-service", "--prepare", "--python", str(python)])
    assert exc.value.code == 2


@pytest.mark.parametrize("smoke", [False, True])
def test_run_is_contained_and_releases_interpreter_environment_lease(tmp_path, monkeypatch, smoke):
    root = tmp_path / "agent-index-service"
    (root / "tests").mkdir(parents=True)
    python = tmp_path / "test-python"
    python.touch()
    monkeypatch.setattr(runner, "REPO", tmp_path)
    released = []

    class Lease:
        def release(self):
            released.append(True)

    environment_root = tmp_path / "actual-environment"
    monkeypatch.setattr(runner, "interpreter_environment", lambda path: environment_root)
    acquisitions = []
    monkeypatch.setattr(
        runner, "acquire",
        lambda wait, root: acquisitions.append((wait, root)) or Lease(),
    )
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        assert command[1:4] == ["-I", "-m", "pytest"]
        if smoke:
            assert command[5:9] == [
                str(root / "tests" / "test_cli.py"), str(root / "tests" / "test_config.py"),
                str(root / "tests" / "test_release.py"),
                str(root / "tests" / "test_release_cli.py"),
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
    assert acquisitions == [(0.0, environment_root)]


def test_smoke_rejects_undefined_component_before_admission(monkeypatch):
    monkeypatch.setattr(runner, "acquire", lambda wait, root: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["worktree-manager", "--smoke"])
    assert exc.value.code == 2


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1"])
def test_nonfinite_or_nonpositive_timeout_rejected_before_admission(monkeypatch, timeout):
    monkeypatch.setattr(runner, "acquire", lambda wait, root: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["agent-index-service", f"--timeout={timeout}"])
    assert exc.value.code == 2


@pytest.mark.parametrize("wait", ["nan", "inf", "-inf", "-1", "bad"])
def test_invalid_admission_wait_rejected_before_preparation(monkeypatch, wait):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    monkeypatch.setattr(runner, "prepare", lambda *_a, **_k: pytest.fail("unexpected preparation"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["agent-index-service", "--prepare", f"--admission-wait={wait}"])
    assert exc.value.code == 2


def test_custom_interpreter_probe_failure_is_not_silent_success(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "agent-index-service" / "tests").mkdir(parents=True)
    python = tmp_path / "invalid-python"
    python.touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))

    def fail(_):
        raise ValueError("invalid prefix")

    monkeypatch.setattr(runner, "interpreter_environment", fail)
    assert runner.main(["agent-index-service", "--python", str(python)]) == 1


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX outer containment regression uses Linux procfs")
def test_posix_owned_child_survives_worker_exit_only_until_outer_cleanup(tmp_path):
    from plugin_test_containment import Limits, isolated_environment, run_contained

    repo = SCRIPT.parent.parent
    receipt = tmp_path / "identity.json"
    script = f"""
import json,os,sys,subprocess
from pathlib import Path
sys.path[:0] = {[
    str(repo / "agent-index-service" / "tests"),
    str(repo / "libs" / "agent-procutil" / "src"),
]!r}
from _service_process import owned_python
with owned_python(['-c', 'import time; time.sleep(120)'],
                  cwd={str(tmp_path)!r}, stdout=subprocess.DEVNULL) as child:
    Path({str(receipt)!r}).write_text(json.dumps({{
        'pid': child.pid, 'group': os.getpgid(child.pid), 'owner_group': os.getpgrp(),
    }}))
    os._exit(0)
"""
    sandbox = tmp_path / "sandbox"
    env = isolated_environment(os.environ, sandbox)
    assert run_contained(
        [sys.executable, "-c", script], cwd=repo, env=env, sandbox=sandbox,
        limits=Limits(wall_seconds=15, poll_seconds=0.1),
    ) == 0
    identity = json.loads(receipt.read_text())
    assert identity["group"] == identity["owner_group"]
    state = Path(f"/proc/{identity['pid']}/stat")
    assert not state.exists() or state.read_text().split(") ", 1)[1].startswith("Z ")


def test_ci_path_gates_smoke_and_promotion_keeps_exhaustive():
    import yaml

    workflows = SCRIPT.parent.parent / ".github" / "workflows"
    ci = yaml.safe_load((workflows / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    promotion = yaml.safe_load(
        (workflows / "validate-and-promote.yml").read_text(encoding="utf-8")
    )["jobs"]
    discovery = next(step["run"] for step in ci["discover"]["steps"] if step.get("id") == "set")
    detector = next(
        step["run"] for step in ci["checks"]["steps"] if step.get("id") == "version-bump-engine"
    )
    assert r"standalone_consumers\.py" in detector
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
