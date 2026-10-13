from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("run-standalone-tests.py")
SPEC = importlib.util.spec_from_file_location("run_standalone_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_fleet_manager_preparation_has_no_service_or_plugin_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    calls = []
    python = runner.default_python("fleet-manager")
    def prepare_command(command, **kwargs):
        calls.append(command)
        if command[:2] == ["uv", "venv"]:
            assert Path(command[-1]) == python.parent.parent
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", prepare_command)
    python.parent.parent.mkdir(parents=True)
    runner.prepare("fleet-manager", python)
    assert calls[-1] == [
        "uv", "pip", "install", "--python", str(python), str(tmp_path / "fleet-manager") + "[dev]",
    ]
    assert "fleet-manager" in runner.STANDALONE_CONSUMERS


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
            assert override.read_text() == (
                f"agent-index{core_extra} @ {metadata.parent.as_uri()}\n"
                f"agent-procutil @ {(tmp_path / 'libs' / 'agent-procutil').as_uri()}\n"
            )

    monkeypatch.setattr(runner.subprocess, "run", install)
    python = tmp_path / ".test-venvs" / "service" / "bin" / "python"
    python.parent.parent.mkdir(parents=True)

    runner.prepare("services/index", python, smoke=smoke)

    assert calls[0] == ["uv", "venv", "--allow-existing", str(python.parent.parent)]
    assert calls[1][:5] == ["uv", "pip", "install", "--python", str(python)]
    core_extra = "" if smoke else "[store,server]"
    service_extra = "[test]" if smoke else "[native,test]"
    assert str(tmp_path / "plugins" / "agent-index") + core_extra in calls[1]
    assert calls[1][-1] == str(tmp_path / "services/index") + service_extra
    assert not Path(calls[1][calls[1].index("--override") + 1]).exists()


def test_prepare_rejects_host_interpreter_before_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    called = []
    monkeypatch.setattr(runner, "acquire", lambda wait, root: called.append(wait))

    with pytest.raises(SystemExit) as exc:
        runner.main([
            "services/index", "--prepare", "--python", str(tmp_path / "host" / "python")
        ])

    assert exc.value.code == 2
    assert called == []


def test_prepare_rejects_managed_launcher_with_host_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / ".test-venvs" / "fake" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    released = []

    class Lease:
        def release(self):
            released.append(True)

    monkeypatch.setattr(runner, "acquire", lambda *_: Lease())
    monkeypatch.setattr(runner, "prepare", lambda *_a, **_k: pytest.fail("unexpected preparation"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", "--prepare", "--python", str(python)])
    assert exc.value.code == 2
    assert released == [True]


@pytest.mark.parametrize("smoke", [False, True])
def test_run_is_contained_and_releases_interpreter_environment_lease(tmp_path, monkeypatch, smoke):
    root = tmp_path / "services/index"
    (root / "tests").mkdir(parents=True)
    python = tmp_path / "test-python"
    python.touch()
    monkeypatch.setattr(runner, "REPO", tmp_path)
    released = []

    class Lease:
        def release(self):
            released.append(True)

    environment_root = tmp_path / "actual-environment"
    environment_root.mkdir()
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
            assert command[5:16] == [
                str(root / "tests" / "test_cli.py"), str(root / "tests" / "test_config.py"),
                str(root / "tests" / "test_release.py"),
                str(root / "tests" / "test_release_cli.py"),
                str(root / "tests" / "test_staging.py"),
                str(root / "tests" / "test_staging_cli.py"),
                str(root / "tests" / "test_staging_cleanup_review.py"),
                str(root / "tests" / "test_staging_processes.py"),
                str(root / "tests" / "test_runtime_resource_distribution.py"),
                str(root / "tests" / "test_distribution.py"),
                str(root / "tests" / "test_release_distribution.py"),
            ]
        else:
            assert command[5] == str(root / "tests")
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert Path(kwargs["env"]["HOME"]) != Path.home()
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    args = [
        "services/index", "--python", str(python),
        "--environment-root", str(environment_root),
    ]
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
        runner.main(["services/index", f"--timeout={timeout}"])
    assert exc.value.code == 2


@pytest.mark.parametrize("wait", ["nan", "inf", "-inf", "-1", "bad"])
def test_invalid_admission_wait_rejected_before_preparation(monkeypatch, wait):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    monkeypatch.setattr(runner, "prepare", lambda *_a, **_k: pytest.fail("unexpected preparation"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", "--prepare", f"--admission-wait={wait}"])
    assert exc.value.code == 2


def test_nonstandard_custom_launcher_requires_explicit_root_without_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / "invalid-python"
    python.touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))

    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: pytest.fail("candidate executed"))
    assert runner.main(["services/index", "--python", str(python)]) == 1


@pytest.mark.parametrize("arguments", [
    ["--environment-root", "environment"],
    ["--python", "launcher", "--environment-root", "environment", "--prepare"],
])
def test_explicit_root_does_not_bypass_preparation_guard(monkeypatch, arguments):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", *arguments])
    assert exc.value.code == 2


def test_standard_root_discovery_never_executes_candidate(tmp_path, monkeypatch):
    python = tmp_path / "environment" / "bin" / "python"
    python.parent.parent.mkdir()
    (python.parent.parent / "pyvenv.cfg").touch()
    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: pytest.fail("candidate executed"))
    assert runner.interpreter_environment(python) == python.parent.parent.resolve()
    assert not python.exists()


def test_explicit_root_cannot_override_discoverable_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / "environment" / "bin" / "python"
    python.parent.parent.mkdir()
    (python.parent.parent / "pyvenv.cfg").touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main([
            "services/index", "--python", str(python),
            "--environment-root", str(tmp_path / "wrong-environment"),
        ])
    assert exc.value.code == 2


@pytest.mark.parametrize("directory", ["bin", "Scripts"])
def test_opaque_standard_named_launcher_accepts_explicit_environment(tmp_path, monkeypatch, directory):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / "wrappers" / directory / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    actual = tmp_path / "actual-environment"
    actual.mkdir()
    acquired = []

    class Lease:
        def release(self):
            pass

    monkeypatch.setattr(runner, "acquire", lambda wait, root: acquired.append(root) or Lease())
    monkeypatch.setattr(runner, "run_contained", lambda *_a, **_k: 0)
    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: pytest.fail("candidate probed"))
    assert runner.main([
        "services/index", "--python", str(python), "--environment-root", str(actual),
    ]) == 0
    assert acquired == [actual]
    acquired.clear()
    assert runner.main(["services/index", "--python", str(python)]) == 1
    assert acquired == []


def test_custom_absent_root_is_not_created(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    root = tmp_path / "absent-custom-root"
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main([
            "services/index", "--python", str(tmp_path / "launcher"),
            "--environment-root", str(root),
        ])
    assert exc.value.code == 2
    assert not root.exists()


def test_custom_symlink_without_configuration_requires_explicit_address(tmp_path, monkeypatch):
    python = tmp_path / "environment" / "bin" / "python"
    python.parent.mkdir(parents=True)
    # Portable symlink-contract simulation: following the executable would
    # reach the base interpreter, not identify the missing-config venv.
    real_resolve = Path.resolve
    monkeypatch.setattr(
        Path, "resolve",
        lambda path, *args, **kwargs: (
            Path(sys.executable) if path == python
            else real_resolve(path, *args, **kwargs)
        ),
    )
    with pytest.raises(ValueError, match="provide --environment-root"):
        runner.interpreter_environment(python)


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX outer containment regression uses Linux procfs")
def test_posix_owned_child_survives_worker_exit_only_until_outer_cleanup(tmp_path):
    from plugin_test_containment import Limits, isolated_environment, run_contained

    repo = SCRIPT.parent.parent
    receipt = tmp_path / "identity.json"
    script = f"""
import json,os,sys,subprocess
from pathlib import Path
sys.path[:0] = {[
    str(repo / "services/index" / "tests"),
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
    for path in ("services/index/", "plugins/agent-index/", "libs/", "tools/"):
        assert path in discovery
    for name in ("agent-index-service-linux", "agent-index-service-windows"):
        assert ci[name]["needs"] == "discover"
        assert "needs.discover.outputs.standalone_service == 'true'" in ci[name]["if"]
        command = next(step["run"] for step in ci[name]["steps"] if "run" in step)
        assert 'MODE="--smoke"' in command
        assert "workflow_dispatch" in command
        full = next(step["run"] for step in promotion[name]["steps"] if "run-standalone-tests" in step.get("run", ""))
        assert "--smoke" not in full


def test_sandbox_cleanup_retries_only_transient_windows_lock(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.time, "sleep", lambda delay: None)
    attempts = []

    class Temporary:
        def cleanup(self):
            attempts.append(True)
            if len(attempts) == 1:
                error = OSError("directory not empty")
                error.winerror = 145
                raise error

    runner.cleanup_sandbox(Temporary())
    assert len(attempts) == 2


def test_sandbox_cleanup_does_not_hide_permanent_failure(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")

    class Temporary:
        def cleanup(self):
            raise OSError("permanent failure")

    with pytest.raises(OSError, match="permanent failure"):
        runner.cleanup_sandbox(Temporary())


@pytest.mark.skipif(runner.os.name != "nt", reason="Windows extended-length cleanup")
def test_sandbox_cleanup_removes_deep_owned_paths():
    temporary = runner.tempfile.TemporaryDirectory(prefix="standalone-cleanup-test-")
    root = Path(temporary.name)
    deep = root
    for index in range(8):
        deep = deep / (f"nested-{index}-" + "a" * 30)
    extended = Path("\\\\?\\" + str(deep.resolve()))
    extended.mkdir(parents=True)
    (extended / "fixture.txt").write_text("fixture")
    runner.cleanup_sandbox(temporary)
    assert not root.exists()


@pytest.mark.parametrize(("path", "expected"), [
    ("C:\\temp\\owned", "\\\\?\\C:\\temp\\owned"),
    ("\\\\server\\share\\owned", "\\\\?\\UNC\\server\\share\\owned"),
    ("\\\\?\\C:\\temp\\owned", "\\\\?\\C:\\temp\\owned"),
    ("\\\\?\\UNC\\server\\share\\owned", "\\\\?\\UNC\\server\\share\\owned"),
])
def test_extended_windows_cleanup_normalizes_unc_without_double_prefix(path, expected):
    assert runner.extended_windows_path(path) == expected
