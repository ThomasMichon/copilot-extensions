"""The native coordinator must settle its VM-owned producers on every outcome."""

from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "manager_suite_acceptance", Path(__file__).with_name("run-manager-suite-acceptance.py"),
)
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


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
