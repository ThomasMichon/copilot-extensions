"""Case-routing contracts without real workers, native APIs or cadence waits."""

from __future__ import annotations

import importlib.util
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
