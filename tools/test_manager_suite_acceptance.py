"""The native coordinator must settle its VM-owned producers on every outcome."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "manager_suite_acceptance", Path(__file__).with_name("run-manager-suite-acceptance.py"),
)
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)
_ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "manager_adapter_acceptance", Path(__file__).with_name("run-manager-runtime-acceptance.py"),
)
adapter_runner = importlib.util.module_from_spec(_ADAPTER_SPEC)
_ADAPTER_SPEC.loader.exec_module(adapter_runner)


@pytest.mark.parametrize("outcome", ("success", "controls", "prepare", "assertions", "exception"))
def test_runtime_inputs_overlap_construction_and_settle_before_consumers(tmp_path, monkeypatch, outcome):
    calls = []

    class Controls:
        returncode = None

        def wait(self, **kwargs):
            calls.append("settle" if kwargs else "controls")
            self.returncode = int(outcome == "controls")
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            calls.append("terminate")
            self.returncode = -1

    def launch(command, **kwargs):
        assert "--timeout=30" in command
        assert "-p" in command and "pytest_portfolio_guard" in command
        assert command[-2:] == ["-k", " or ".join(adapter_runner.RUNTIME_INPUT_CONTROLS)]
        assert f"--basetemp={tmp_path / 'input-controls'}" in command
        calls.append("launch")
        return Controls()

    def run(command, **kwargs):
        if "--prepare" in command:
            calls.append("prepare")
            if outcome == "exception":
                raise OSError("construction failed")
            return SimpleNamespace(returncode=int(outcome == "prepare"))
        assert calls == ["launch", "prepare", "controls"]
        assert "--timeout=30" in command and "pytest_portfolio_guard" in command
        assert command[-1] == f"not ({' or '.join(adapter_runner.RUNTIME_INPUT_CONTROLS)})"
        assert f"--basetemp={tmp_path / 'assertions'}" in command
        calls.append("assertions")
        return SimpleNamespace(returncode=int(outcome == "assertions"))

    monkeypatch.setattr(adapter_runner.subprocess, "Popen", launch)
    monkeypatch.setattr(adapter_runner.subprocess, "run", run)
    if outcome == "exception":
        with pytest.raises(OSError, match="construction failed"):
            adapter_runner._runtime_group(Path(sys.executable), tmp_path)
    else:
        assert adapter_runner._runtime_group(Path(sys.executable), tmp_path) == int(outcome != "success")
    assert calls == {
        "prepare": ["launch", "prepare", "terminate", "settle"],
        "exception": ["launch", "prepare", "terminate", "settle"],
        "controls": ["launch", "prepare", "controls", "settle"],
    }.get(outcome, ["launch", "prepare", "controls", "assertions", "settle"])


def _adapter_report(path, fault=None):
    root = ET.Element("testsuite")
    for node in adapter_runner._application_adapter_nodes():
        ET.SubElement(root, "testcase", {
            "classname": "tools.test_application_adapter_journey.TestApplicationAdapterJourney",
            "name": node.split("::")[-1],
        })
    if fault == "missing":
        root.remove(root[-1])
    elif fault == "duplicate":
        root[-1].set("name", root[0].get("name"))
    elif fault == "foreign":
        root[-1].set("classname", "unrelated")
    elif fault:
        ET.SubElement(root[-1], fault)
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "foreign", "skipped", "failure", "error"])
def test_adapter_acceptance_requires_every_unique_successful_phase(tmp_path, fault):
    report = tmp_path / "report.xml"
    _adapter_report(report, fault)
    assert adapter_runner._application_adapter_complete(report) is (fault is None)


def test_adapter_acceptance_never_reuses_previous_reports(tmp_path, monkeypatch):
    report = tmp_path / "application-adapter-result.json"
    report.write_text('{"complete":true}')
    monkeypatch.setattr(adapter_runner, "acquire", lambda *args: pytest.fail("stale acceptance"))
    with pytest.raises(ValueError, match="fresh acceptance reports"):
        adapter_runner._application_adapter_acceptance(tmp_path, tmp_path / "venvs")
    assert report.read_text() == '{"complete":true}'


@pytest.mark.parametrize("outcome", ["complete", "partial", "exception", "cleanup-overrun"])
def test_adapter_group_retains_one_bound_and_cleans_up_on_every_outcome(tmp_path, monkeypatch, outcome):
    root = tmp_path / "evidence"
    root.mkdir()
    venv = tmp_path / "venvs" / "agent-worktrees"
    python = venv / ("Scripts/python.exe" if adapter_runner.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    settled = []
    clock = [0.0]
    monkeypatch.setattr(adapter_runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(adapter_runner, "acquire", lambda *args: SimpleNamespace(
        release=lambda: settled.append("lease"),
    ))

    @contextmanager
    def sandbox(**kwargs):
        assert kwargs["dir"] == adapter_runner.REPO
        private = root / "sandbox"
        private.mkdir()
        try:
            yield private
        finally:
            settled.append("sandbox")
            if outcome == "cleanup-overrun":
                clock[0] = 301.0

    def execute(command, *, limits, **kwargs):
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


def test_adapter_later_phase_refuses_before_native_work(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "application_adapter_partial_control", Path(__file__).with_name("test_application_adapter_journey.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = SimpleNamespace(variant="plugin", directory=tmp_path, completed=[])
    with pytest.raises(AssertionError, match="requires completed ordered phases"):
        module._run_application_adapter_phase(state, "install", monkeypatch)
    assert state.completed == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failed", ("prepare", "exercise", "assertions", None))
def test_native_stage_failure_always_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str | None,
) -> None:
    calls = []

    def run(argv: list[str], *, timeout: float) -> int:
        stage = next((name for name in ("prepare", "exercise", "settle")
                      if "--" + name in argv), "assertions")
        calls.append(stage)
        return int(stage == failed)

    monkeypatch.setattr(runner, "_run", run)
    monkeypatch.setenv("MANAGER_SUITE_ACCEPTANCE_DATA", "previous-owner")
    assert runner.qualify(tmp_path, "repair", Path(sys.executable)) == int(failed is not None)
    assert calls == {
        "prepare": ["prepare", "settle"],
        "exercise": ["prepare", "exercise", "settle"],
    }.get(failed, ["prepare", "exercise", "assertions", "settle"])
    assert runner.os.environ["MANAGER_SUITE_ACCEPTANCE_DATA"] == "previous-owner"
    report = json.loads((tmp_path / "repair-runner.json").read_text(encoding="utf-8"))
    assert report["stages"]["settle"] == 0


def test_preparation_timeout_still_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(argv: list[str], *, timeout: float) -> int:
        calls.append("settle" if "--settle" in argv else "prepare")
        if "--prepare" in argv:
            raise subprocess.TimeoutExpired(argv, timeout)
        return 0

    monkeypatch.setattr(runner, "_run", run)
    assert runner.qualify(tmp_path, "normal", Path(sys.executable)) == 1
    assert calls == ["prepare", "settle"]


def test_serial_baseline_preparation_can_finish_before_its_outer_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

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
        assert argv[-1] == "controlled-observer"
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 5
        while not stop.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stop.exists()
        report.write_text('{"stop_observed":true}', encoding="utf-8")
        return 0

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
    monkeypatch.setattr(runner, "_run", lambda *args, **kwargs: 3)
    with pytest.raises(RuntimeError, match="observer"):
        with runner._observe_processes(tmp_path, "normal", lambda *args: "controlled-observer"):
            pytest.fail("Failed process observation cannot qualify a native run")
    assert (tmp_path / "normal-process-trace.stop").exists()


def test_process_observer_report_is_retained_by_the_actual_native_job() -> None:
    workflow = yaml.safe_load((runner.REPO / ".github" / "workflows" / "installation-context-full.yml").read_text(
        encoding="utf-8",
    ))
    upload = next(step for step in workflow["jobs"]["manager-suite-windows"]["steps"]
                  if step.get("name") == "Retain synthetic qualification diagnostics")
    paths = upload["with"]["path"].splitlines()
    for mode in ("normal", "repair"):
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
        return execute([
            *prefix, "-c", "import sys; assert sys.flags.utf8_mode == 1; print(chr(0x2014))",
        ], timeout=30)

    monkeypatch.setattr(runner, "_run", run)
    assert runner.qualify(tmp_path, "normal", Path(sys.executable)) == 0
    assert "\u2014" in capsys.readouterr().out
