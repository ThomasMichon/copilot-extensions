"""The native coordinator must settle its VM-owned producers on every outcome."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
import xml.etree.ElementTree as ET

import pytest
import yaml

if TYPE_CHECKING:
    from plugin_test_containment import Limits

_SPEC = importlib.util.spec_from_file_location(
    "manager_suite_acceptance", Path(__file__).with_name("run-manager-suite-acceptance.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)
_ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "manager_adapter_acceptance", Path(__file__).with_name("run-manager-runtime-acceptance.py"),
)
assert _ADAPTER_SPEC is not None and _ADAPTER_SPEC.loader is not None
adapter_runner = importlib.util.module_from_spec(_ADAPTER_SPEC)
_ADAPTER_SPEC.loader.exec_module(adapter_runner)


def test_suite_cli_bootstraps_process_helpers_without_site_packages(tmp_path: Path) -> None:
    from agent_procutil import no_window_kwargs

    (tmp_path / "agent_procutil.py").write_text(
        "raise RuntimeError('untrusted cwd source imported')\n", encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable, "-E", "-S", "-B",
            str(Path(__file__).with_name("run-manager-suite-acceptance.py")), "--help",
        ],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert "--mode" in result.stdout


@pytest.mark.parametrize("outcome", (
    "success", "controls", "constructed", "legacy", "finish", "assertions", "exception", "launch",
))
def test_runtime_inputs_overlap_construction_and_settle_before_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    calls: list[str] = []

    class Controls:
        returncode: int | None = None

        def __init__(self, label: str) -> None:
            self.label = label
            self.settled = False

        def wait(self, **kwargs: object) -> int:
            calls.append(("settle:" if kwargs else "wait:") + self.label)
            self.returncode = int(outcome == self.label)
            self.settled = True
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            calls.append("terminate:" + self.label)
            self.returncode = -1

    children: list[Controls] = []

    def launch(command: list[str], **kwargs: object) -> Controls:
        assert kwargs["stdout"] is sys.stdout and kwargs["stderr"] is sys.stderr
        if "--prepare-step" in command:
            label = command[-1]
            assert command[-3:] == [str(tmp_path), "--prepare-step", label]
        else:
            label = "controls"
            assert "--timeout=30" in command
            assert "-p" in command and "pytest_portfolio_guard" in command
            assert command[-2:] == ["-k", " or ".join(adapter_runner.RUNTIME_INPUT_CONTROLS)]
            assert f"--basetemp={tmp_path / 'input-controls'}" in command
        calls.append("launch:" + label)
        if outcome == "launch" and label == "legacy":
            raise OSError("construction failed")
        child = Controls(label)
        children.append(child)
        return child

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert kwargs["stdout"] is sys.stdout and kwargs["stderr"] is sys.stderr
        if "--prepare" in command:
            assert calls[:5] == [
                "launch:controls", "launch:constructed", "launch:legacy", "wait:constructed", "wait:legacy",
            ]
            assert command[-2:] == ["--prepare-step", "finish"]
            calls.append("finish")
            if outcome == "exception":
                raise OSError("construction failed")
            return SimpleNamespace(returncode=int(outcome == "finish"))
        assert calls[-2:] == ["finish", "wait:controls"]
        assert "--timeout=30" in command and "pytest_portfolio_guard" in command
        assert command[-1] == f"not ({' or '.join(adapter_runner.RUNTIME_INPUT_CONTROLS)})"
        assert f"--basetemp={tmp_path / 'assertions'}" in command
        calls.append("assertions")
        return SimpleNamespace(returncode=int(outcome == "assertions"))

    monkeypatch.setattr(adapter_runner.subprocess, "Popen", launch)
    monkeypatch.setattr(adapter_runner.subprocess, "run", run)
    if outcome in {"exception", "launch"}:
        with pytest.raises(OSError, match="construction failed"):
            adapter_runner._runtime_group(Path(sys.executable), tmp_path)
    else:
        assert adapter_runner._runtime_group(Path(sys.executable), tmp_path) == int(outcome != "success")
    assert all(child.settled for child in children)
    assert ("assertions" in calls) is (outcome in {"success", "assertions"})
    assert ("finish" in calls) is (outcome not in {"constructed", "legacy", "launch"})


def _adapter_report(path: Path, fault: str | None = None) -> None:
    root = ET.Element("testsuite")
    for node in adapter_runner._application_adapter_nodes():
        ET.SubElement(root, "testcase", {
            "classname": "tools.test_application_adapter_journey.TestApplicationAdapterJourney",
            "name": node.split("::")[-1],
        })
    if fault == "missing":
        root.remove(root[-1])
    elif fault == "duplicate":
        root[-1].set("name", cast(str, root[0].get("name")))
    elif fault == "foreign":
        root[-1].set("classname", "unrelated")
    elif fault:
        ET.SubElement(root[-1], fault)
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "foreign", "skipped", "failure", "error"])
def test_adapter_acceptance_requires_every_unique_successful_phase(
    tmp_path: Path, fault: str | None,
) -> None:
    report = tmp_path / "report.xml"
    _adapter_report(report, fault)
    assert adapter_runner._application_adapter_complete(report) is (fault is None)


def test_adapter_acceptance_never_reuses_previous_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = tmp_path / "application-adapter-result.json"
    report.write_text('{"complete":true}')
    monkeypatch.setattr(adapter_runner, "acquire", lambda *args: pytest.fail("stale acceptance"))
    with pytest.raises(ValueError, match="fresh acceptance reports"):
        adapter_runner._application_adapter_acceptance(tmp_path, tmp_path / "venvs")
    assert report.read_text() == '{"complete":true}'


@pytest.mark.parametrize("outcome", ["complete", "partial", "exception", "cleanup-overrun"])
def test_adapter_group_retains_one_bound_and_cleans_up_on_every_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    venv = tmp_path / "venvs" / "agent-worktrees"
    python = venv / ("Scripts/python.exe" if adapter_runner.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    settled: list[str] = []
    clock = [0.0]
    monkeypatch.setattr(adapter_runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(adapter_runner, "acquire", lambda *args: SimpleNamespace(
        release=lambda: settled.append("lease"),
    ))

    @contextmanager
    def sandbox(**kwargs: object) -> Iterator[Path]:
        assert kwargs["dir"] == adapter_runner.REPO
        private = root / "sandbox"
        private.mkdir()
        try:
            yield private
        finally:
            settled.append("sandbox")
            if outcome == "cleanup-overrun":
                clock[0] = 301.0

    def execute(command: list[str], *, limits: Limits, **kwargs: object) -> int:
        assert 0 < limits.wall_seconds <= 300
        assert "--timeout=30" in command
        nodes = adapter_runner._application_adapter_nodes()
        assert command[-len(nodes):] == nodes
        if outcome == "exception":
            raise RuntimeError("controlled group failure")
        report = Path(next(value.split("=", 1)[1] for value in command if value.startswith("--junitxml=")))
        _adapter_report(report, "missing" if outcome == "partial" else None)
        return 0

    monkeypatch.setattr(adapter_runner, "TestSandbox", sandbox)
    monkeypatch.setattr(adapter_runner, "isolated_environment", lambda *args, **kwargs: {})
    monkeypatch.setattr(adapter_runner, "run_contained", execute)
    expected = 124 if outcome == "cleanup-overrun" else int(outcome != "complete")
    assert adapter_runner._application_adapter_acceptance(root, venv.parent) == expected
    assert settled == ["sandbox", "lease"]
    result = json.loads((root / "application-adapter-result.json").read_text())
    assert result["complete"] is (outcome == "complete")


def test_adapter_later_phase_refuses_before_native_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "application_adapter_partial_control", Path(__file__).with_name("test_application_adapter_journey.py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = SimpleNamespace(variant="plugin", directory=tmp_path, completed=[])
    with pytest.raises(AssertionError, match="requires completed ordered phases"):
        module._run_application_adapter_phase(state, "install", monkeypatch)
    assert state.completed == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failed", ("prepare", "exercise", "assertions", None))
@pytest.mark.parametrize("mode", ("normal", "repair", "fresh"))
def test_native_stage_failure_always_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str | None, mode: str,
) -> None:
    calls: list[str] = []

    def run(argv: list[str], *, timeout: float) -> int:
        stage = next((name for name in ("prepare", "exercise", "settle")
                      if "--" + name in argv), "assertions")
        calls.append(stage)
        if mode == "fresh":
            assert str(runner.FRESH_FIXTURE) in argv
        assert timeout == {"prepare": 1800, "exercise": 900, "assertions": 300, "settle": 90}[stage]
        return int(stage == failed)

    monkeypatch.setattr(runner, "_run", run)
    monkeypatch.setenv("MANAGER_SUITE_ACCEPTANCE_DATA", "previous-owner")
    assert runner.qualify(tmp_path, mode, Path(sys.executable)) == int(failed is not None)
    expected: dict[str | None, list[str]] = {
        "prepare": ["prepare", "settle"],
        "exercise": ["prepare", "exercise", "settle"],
    }
    assert calls == expected.get(failed, ["prepare", "exercise", "assertions", "settle"])
    assert runner.os.environ["MANAGER_SUITE_ACCEPTANCE_DATA"] == "previous-owner"
    report = json.loads((tmp_path / f"{mode}-runner.json").read_text(encoding="utf-8"))
    assert report["stages"]["settle"] == 0


@pytest.mark.parametrize("mode", ("normal", "fresh"))
def test_preparation_timeout_still_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    calls: list[str] = []

    def run(argv: list[str], *, timeout: float) -> int:
        calls.append("settle" if "--settle" in argv else "prepare")
        if "--prepare" in argv:
            raise subprocess.TimeoutExpired(argv, timeout)
        return 0

    monkeypatch.setattr(runner, "_run", run)
    assert runner.qualify(tmp_path, mode, Path(sys.executable)) == 1
    assert calls == ["prepare", "settle"]


def test_serial_baseline_preparation_can_finish_before_its_outer_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def run(argv: list[str], *, timeout: float) -> int:
        stage = next((name for name in ("prepare", "exercise", "settle")
                      if "--" + name in argv), "assertions")
        calls.append(stage)
        if stage == "prepare" and timeout < 1050:
            raise subprocess.TimeoutExpired(argv, timeout)
        return 0

    monkeypatch.setattr(runner, "_run", run)
    assert runner.qualify(tmp_path, "repair", Path(sys.executable)) == 0
    assert calls == ["prepare", "exercise", "assertions", "settle"]


def test_failed_settlement_never_reports_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_run", lambda argv, **kwargs: int("--settle" in argv))
    assert runner.qualify(tmp_path, "normal", Path(sys.executable)) == 1


def test_local_refusal_precedes_any_fixture_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "must-not-exist"
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys, "argv", [str(runner.__file__), "--root", str(root), "--mode", "normal"])
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted Windows VM"):
        runner.main()
    assert not root.exists()


@pytest.mark.parametrize("body_error", (False, True))
def test_process_observer_stops_and_retains_evidence_on_every_body_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body_error: bool,
) -> None:
    ready = tmp_path / "repair-process-trace.ready"
    stop = tmp_path / "repair-process-trace.stop"
    report = tmp_path / "repair-process-trace.json"

    def run(argv: list[str], *, timeout: float) -> int:
        assert argv[0] == "controlled-pwsh"
        assert argv[-1] == "controlled-observer"
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 5
        while not stop.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stop.exists()
        report.write_text('{"stop_observed":true}', encoding="utf-8")
        return 0

    monkeypatch.setattr(runner.shutil, "which", lambda executable: (
        "controlled-pwsh" if executable == "pwsh" else None
    ))
    monkeypatch.setattr(runner, "_run", run)
    with pytest.raises(ValueError, match="body") if body_error else nullcontext():
        with runner._observe_processes(tmp_path, "repair", lambda *args: "controlled-observer"):
            assert ready.exists() and not stop.exists()
            if body_error:
                raise ValueError("body")
    assert stop.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["stop_observed"]


def test_process_observer_failure_cannot_enter_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda executable: (
        "controlled-pwsh" if executable == "pwsh" else None
    ))
    monkeypatch.setattr(runner, "_run", lambda *args, **kwargs: 3)
    with pytest.raises(RuntimeError, match="observer"):
        with runner._observe_processes(tmp_path, "normal", lambda *args: "controlled-observer"):
            pytest.fail("Failed process observation cannot qualify a native run")
    assert (tmp_path / "normal-process-trace.stop").exists()


def test_process_observer_missing_executable_refuses_before_any_observer_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda executable: None)
    monkeypatch.setattr(runner, "_run", lambda *args, **kwargs: pytest.fail(
        "Missing PowerShell must refuse before starting a process",
    ))
    with pytest.raises(RuntimeError, match="requires pwsh"):
        with runner._observe_processes(tmp_path, "normal", lambda *args: pytest.fail(
            "Missing PowerShell must refuse before constructing the observer",
        )):
            pytest.fail("Missing PowerShell cannot qualify a native run")
    assert list(tmp_path.iterdir()) == []


def test_process_observer_report_is_retained_by_the_actual_native_job() -> None:
    workflow = yaml.safe_load((runner.REPO / ".github" / "workflows" / "installation-context-full.yml").read_text(
        encoding="utf-8",
    ))
    upload = next(step for step in workflow["jobs"]["manager-suite-windows"]["steps"]
                  if step.get("uses") == "actions/upload-artifact@v4")
    paths = upload["with"]["path"].splitlines()
    for mode in ("normal", "repair", "fresh"):
        report = Path(".v") / f"{mode}-process-trace.json"
        assert any(report.match(pattern.strip()) for pattern in paths), str(report)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows legacy pipe encoding")
def test_isolated_stage_diagnostics_use_explicit_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    execute = runner._run

    def run(argv: list[str], *, timeout: float) -> int:
        if "--prepare" not in argv:
            return 0
        prefix = argv[:argv.index(str(runner.FIXTURE))]
        return cast(int, execute([
            *prefix, "-c", "import sys; assert sys.flags.utf8_mode == 1; print(chr(0x2014))",
        ], timeout=30))

    monkeypatch.setattr(runner, "_run", run)
    assert runner.qualify(tmp_path, "normal", Path(sys.executable)) == 0
    assert "\u2014" in capsys.readouterr().out
