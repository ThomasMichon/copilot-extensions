"""Case-routing contracts without real workers, native APIs or cadence waits."""

from __future__ import annotations

import importlib.util
import contextlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "tools/clean-room/scenarios/manager-automatic-updates"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("maintenance_probe_selection", directory / "probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux", argv=[]))
    monkeypatch.setattr(module.lifecycle, "case", Mock(return_value={"passed": True}))
    monkeypatch.setattr(module.lifecycle, "signal_drain_case", Mock(return_value={"passed": True}))
    return module


@pytest.mark.parametrize(("selected", "ordinary", "signal"), [
    ("handover", ["handover"], False),
    ("failed-successor", ["failed-successor"], False),
    ("signal-drain", [], True),
    ("all", ["handover", "failed-successor"], True),
])
def test_selected_case_does_not_run_other_lifecycle_work(
    probe: ModuleType, tmp_path: Path, selected: str, ordinary: list[str], signal: bool,
) -> None:
    report = probe._qualify_cases(
        tmp_path, tmp_path, {}, cycles=1, native_startup=True,
        instance_environment={}, case=selected,
    )
    calls = probe.lifecycle.case.call_args_list
    assert [call.args[1].name for call in calls] == ordinary
    assert probe.lifecycle.signal_drain_case.called is signal
    assert report["complete_case_set"] is (selected == "all")
    assert report["passed"] is True
    assert json.loads((tmp_path / "qualification.json").read_text()) == report


@pytest.mark.parametrize(("selected", "cycles", "platform", "native"), [
    ("unknown", 1, "linux", False),
    ("failed-successor", 2, "linux", True),
    ("signal-drain", 1, "linux", False),
    ("signal-drain", 1, "win32", True),
    ("handover", 1, "win32", True),
])
def test_invalid_case_combination_refuses_before_preflight_or_preparation(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    selected: str, cycles: int, platform: str, native: bool,
) -> None:
    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform=platform))
    preflight, prepared = Mock(), Mock()
    monkeypatch.setattr(probe.native, "preflight", preflight)
    monkeypatch.setattr(probe.fixtures, "payload_path", prepared)
    with pytest.raises((ValueError, probe.native.UnsupportedNative)):
        probe.qualify(tmp_path, tmp_path / "absent", cycles=cycles, native_startup=native, case=selected)
    preflight.assert_not_called()
    prepared.assert_not_called()
    assert not (tmp_path / "absent").exists()


def windows_case(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    selected: str, *, quiet: bool = True, lifetime: bool = True,
) -> dict:
    import windows_api
    import windows_native

    class Observer:
        def __init__(self, _api: object) -> None:
            self.thread = SimpleNamespace(is_alive=lambda: False)

        def __enter__(self) -> Observer:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def packet(self) -> dict:
            return {"passed": quiet}

    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(windows_api, "WinAPI", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(windows_api, "WindowObserver", Observer)
    monkeypatch.setattr(windows_native, "observation_passed", Mock(return_value=lifetime))
    return probe._qualify_cases(
        tmp_path, tmp_path, {}, cycles=2 if selected == "handover" else 1,
        native_startup=True, instance_environment={}, case=selected,
    )


def test_windows_failure_case_cannot_claim_two_cycle_job_qualification(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import windows_native

    report = windows_case(probe, monkeypatch, tmp_path, "failed-successor")
    assert "handover" not in report
    assert report["complete_case_set"] is False
    assert report["windows_window_and_job_lifetime_proved"] is False
    windows_native.observation_passed.assert_not_called()
    call = probe.lifecycle.case.call_args
    assert call.kwargs["cycles"] == 1 and call.kwargs["failing_standby"] is True
    assert call.kwargs["native_startup"] is True


def test_windows_handover_keeps_real_cadence_and_lifetime_assessment(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import windows_native

    report = windows_case(probe, monkeypatch, tmp_path, "handover")
    assert "failed_successor" not in report
    assert report["windows_window_and_job_lifetime_proved"] is True
    assert report["intentional_cadence_wait_seconds"] == 300
    windows_native.observation_passed.assert_called_once_with(
        report["windows_observation"], report["handover"],
    )
    assert probe.lifecycle.case.call_args.kwargs["cycles"] == 2
    assert probe.lifecycle.case.call_args.kwargs["native_startup"] is True


@pytest.mark.parametrize(("selected", "quiet", "lifetime"), [
    ("failed-successor", False, True),
    ("handover", True, False),
])
def test_failed_native_observation_cannot_pass_selected_case(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    selected: str, quiet: bool, lifetime: bool,
) -> None:
    import windows_api

    with pytest.raises(windows_api.NativeBlocked, match="windows-observation-failed"):
        windows_case(probe, monkeypatch, tmp_path, selected, quiet=quiet, lifetime=lifetime)
    report = json.loads((tmp_path / "qualification.json").read_text())
    assert report["passed"] is False
    assert report["windows_window_and_job_lifetime_proved"] is False


def test_cli_forwards_single_case_selection(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    qualify = Mock(return_value={"passed": True})
    monkeypatch.setattr(probe, "qualify", qualify)
    monkeypatch.setattr(sys, "argv", [
        "probe.py", "qualify", "--source", str(tmp_path), "--workdir", str(tmp_path / "result"),
        "--case", "failed-successor",
    ])
    assert probe.main() == 0
    qualify.assert_called_once_with(
        tmp_path, tmp_path / "result", cycles=1, native_startup=False, case="failed-successor",
    )


@pytest.mark.parametrize("fault", ["missing", "malformed", "retained", "cancel"])
def test_failed_case_preserves_primary_stack_and_report_availability(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    primary = KeyboardInterrupt("cancelled lifecycle") if fault == "cancel" else RuntimeError("primary lifecycle")

    def failed(source: Path, root: Path, manager: dict, **kwargs: object) -> dict:
        if fault in {"malformed", "retained"}:
            (root / "probe").mkdir(parents=True)
            (root / "probe/result.json").write_text(
                '{"partial":' if fault == "malformed" else json.dumps({
                    "passed": False, "cleanup_verified": False,
                    "cleanup_failure": {"message": "cleanup failed"},
                })
            )
        raise primary

    monkeypatch.setattr(probe.lifecycle, "case", failed)
    with pytest.raises(BaseException) as raised:
        probe._qualify_cases(tmp_path, tmp_path, {}, cycles=1, native_startup=False,
                             instance_environment={}, case="handover")
    assert raised.value is primary
    report = json.loads((tmp_path / "qualification.json").read_text())
    assert report["passed"] is False
    assert report["active_case"] == "handover"
    assert report["failure"]["traceback_available"] is True
    assert report["failure"]["message"] == str(primary)
    assert report["case_results"]["handover"]["status"] == (
        "retained-failure-report" if fault == "retained" else
        "unreadable" if fault == "malformed" else "missing"
    )
    if fault == "retained":
        assert report["handover"]["cleanup_failure"]["message"] == "cleanup failed"


@pytest.mark.parametrize("error", [OSError("probe failure"), KeyboardInterrupt("cancelled")])
def test_cli_failure_has_bounded_stack_without_creating_outputs(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture, error: BaseException,
) -> None:
    def failed(*args: object, **kwargs: object) -> dict:
        raise error

    monkeypatch.setattr(probe, "qualify", failed)
    monkeypatch.setattr(sys, "argv", [
        "probe.py", "qualify", "--source", str(tmp_path), "--workdir", str(tmp_path / "absent"),
    ])
    assert probe.main() == (130 if isinstance(error, KeyboardInterrupt) else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is False
    assert report["failure"]["message"] == str(error)
    assert report["failure"]["traceback_available"] is True
    assert len(report["failure"]["traceback"]) <= 16384
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("fault", ["cleanup", "cancel", "pin", "report"])
def test_case_finalization_attempts_all_pins_and_preserves_causal_failures(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    from identity import failure_details

    (tmp_path / "probe").mkdir()
    closed = []

    def close(index: int) -> None:
        closed.append(index)
        if fault == "pin" and index == 0:
            raise OSError("pin close failed")

    def finalize() -> None:
        if fault in {"cleanup", "report"}:
            raise RuntimeError("cleanup failed")
        if fault == "cancel":
            raise KeyboardInterrupt("cleanup cancelled")

    result = {"passed": False, "cleanup_verified": False}
    if fault == "report":
        def no_report(*args: object, **kwargs: object) -> int:
            raise OSError("report write failed")
        monkeypatch.setattr(Path, "write_text", no_report)
    try:
        raise ValueError("primary body failure")
    except ValueError as primary:
        result["failure"] = failure_details(primary)
        with pytest.raises(BaseException) as raised:
            probe.lifecycle.finish_case(tmp_path, result, [
                SimpleNamespace(close=lambda: close(0)), SimpleNamespace(close=lambda: close(1)),
            ], finalize)
    assert closed == [0, 1]
    assert result["passed"] is False
    assert result["cleanup_verified"] is False
    stack = failure_details(raised.value)["traceback"]
    assert "primary body failure" in stack
    assert "pin close failed" in stack if fault == "pin" else "cleanup" in stack
    if fault != "report":
        retained = json.loads((tmp_path / "probe/result.json").read_text())
        assert retained["failure"]["message"] == "primary body failure"
    else:
        assert result["report_failure"]["message"] == "report write failed"


@pytest.mark.parametrize("inherited", ["expired", "late", "invalid", "absent", "sleep"])
def test_polling_consumes_inherited_work_deadline(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, inherited: str,
) -> None:
    import identity

    now = [0.0]
    calls = []
    monkeypatch.setattr(identity.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(identity.time, "time", lambda: 1000)
    monkeypatch.setattr(identity.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    if inherited == "absent":
        monkeypatch.delenv("APERTURE_TEST_DEADLINE", raising=False)
    else:
        monkeypatch.setenv("APERTURE_TEST_DEADLINE", {
            "expired": "999", "late": "1000.05", "invalid": "nan", "sleep": "1000.05",
        }[inherited])

    def action() -> bool:
        calls.append("called")
        if inherited == "late":
            now[0] = 0.06
        return inherited != "sleep"

    if inherited == "absent":
        assert identity.until(action, bool, timeout=1, label="test") is True
    else:
        with pytest.raises(ValueError if inherited == "invalid" else TimeoutError):
            identity.until(action, bool, timeout=1, label="test")
    if inherited in {"expired", "invalid"}:
        assert not calls
    if inherited == "sleep":
        assert now[0] == pytest.approx(0.05)
        assert len(calls) == 1


def test_disposable_lifecycle_finalizer_cannot_leave_a_success_report(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import agent_procutil.daemon_instances as instances

    @contextlib.contextmanager
    def lifecycle(**kwargs: object):
        yield {}
        raise RuntimeError("disposable lifecycle finalization failed")

    output = tmp_path / "qualification"
    monkeypatch.setattr(instances, "daemon_test_lifecycle", lifecycle)
    monkeypatch.setattr(probe.fixtures, "payload_path", lambda source: source)
    monkeypatch.setattr(probe.fixtures, "verify_prepared", lambda source: None)
    monkeypatch.setattr(probe.fixtures, "private_directory", lambda root: (root.mkdir(), root)[1])
    monkeypatch.setattr(probe.fixtures, "load_manager", lambda source: {})
    with pytest.raises(RuntimeError, match="disposable lifecycle"):
        probe.qualify(tmp_path, output, cycles=1, native_startup=False, case="handover")
    report = json.loads((output / "qualification.json").read_text())
    assert report["passed"] is False
    assert report["lifecycle_finalization_failure"]["message"] == (
        "disposable lifecycle finalization failed"
    )
    assert report["lifecycle_finalization_failure"]["traceback_available"] is True
