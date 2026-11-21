"""Targeted doctor admission never falls through to broad repair."""

from typing import Any

from contextlib import nullcontext
from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import agenda_doctor as doctor, doctor_cli_parser
from agent_worktrees import maintenance_cli
from agent_worktrees import orphaned_owner_repair
from agent_worktrees import (
    config as cfg, execution_spaces, finalize_lock as fin, launch_admission,
    output, sessions, worktree_data_store as data,
)

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def arguments(*values: Any, agenda: bool = True) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    prefix = ["doctor", "--agenda", "selected", "--json"] if agenda else ["doctor", "--json"]
    return parser.parse_args([*prefix, *values])


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    checkout = tmp_path / "checkout"
    source = checkout / ".effort-agenda"
    source.mkdir(parents=True)
    record = SimpleNamespace(creation_nonce="fixture-incarnation", worktree_path=str(checkout))
    config = SimpleNamespace(
        repo_name="fixture", default_repo=SimpleNamespace(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        ),
    )
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(doctor, "_record", lambda *args: record)
    monkeypatch.setattr(doctor, "_helper", lambda *args: tmp_path / "agenda_doctor.py")
    monkeypatch.setattr(data, "read_worktree_data", lambda *args: None)
    monkeypatch.setattr(output, "_json_output", lambda value: print(json.dumps(value)))
    return source


def test_report_negotiates_actual_owner_without_mutation(owner: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = []
    def invoke(script: Any, values: Any) -> Any:
        calls.append(values)
        if values == ["info"]:
            return {"capabilities": ["agenda-placement-doctor-v1"]}
        return {"state": "legacy-local", "store_id": "a" * 32, "revision": 3}
    monkeypatch.setattr(doctor, "_invoke", invoke)
    monkeypatch.setattr(launch_admission, "guard", lambda *args: pytest.fail("report locked"))
    assert doctor.targeted_doctor(arguments()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["agenda"]["state"] == "legacy-local"
    assert [value[0] for value in calls] == ["info", "inspect"]
    assert owner.is_dir() and not list(owner.iterdir())


@pytest.mark.parametrize("status", [0, 1])
@pytest.mark.parametrize("scope", ["agenda", "orphaned-owner"])
def test_targeted_scope_status_never_falls_through_to_broad_maintenance(
    owner: Path, monkeypatch: pytest.MonkeyPatch, status: int, scope: str,
) -> None:
    monkeypatch.setattr(doctor, "targeted_doctor", lambda args: status)
    monkeypatch.setattr(orphaned_owner_repair, "targeted_doctor", lambda args: status)
    monkeypatch.setattr(cfg, "project_name", lambda: pytest.fail("targeted scope fell through"))
    selected = (
        arguments() if scope == "agenda"
        else arguments("--repair-orphaned-owner", "child", agenda=False)
    )
    assert maintenance_cli.cmd_doctor(selected) == status


@pytest.mark.parametrize("state", ["external-correct", "external-missing", "external-corrupt"])
def test_report_inspects_exact_published_external_store(owner: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], state: str) -> None:
    @dataclass
    class Placement:
        state_home: str
        store_id: str

    @dataclass
    class Inventory:
        placements: tuple[Placement, ...]

    home = str(owner.parent.parent / "external")
    monkeypatch.setattr(data, "read_worktree_data", lambda *args: Inventory((
        Placement(home, "a" * 32),
    )))
    calls = []

    def invoke(script: Any, values: Any) -> Any:
        calls.append(values)
        if values == ["info"]:
            return {"capabilities": ["agenda-placement-doctor-v1"]}
        return {"state": state if values[0] == "inspect-external" else "absent"}

    monkeypatch.setattr(doctor, "_invoke", invoke)
    assert doctor.targeted_doctor(arguments()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["agenda"]["state"] == state
    assert result["external"] == [{"state": state}]
    assert calls[-1] == [
        "inspect-external", "--source-home", home, "--worktree", "selected",
        "--store-id", "a" * 32,
    ]


@pytest.mark.parametrize("option", [
    "--repair-record", "--repair-creation", "--recovery-report", "--repair-orphaned-owner",
])
def test_mixed_scopes_refuse_before_selecting_domain(owner: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str) -> None:
    monkeypatch.setattr(doctor, "_helper", lambda *args: pytest.fail("mixed scope selected owner"))
    assert doctor.targeted_doctor(arguments(option, "other")) == 1
    assert "another repair scope" in json.loads(capsys.readouterr().out)["error"]


def test_busy_or_unknown_writer_refuses_before_domain_calls(owner: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def busy(*args: Any) -> Any:
        raise ValueError("fixture liveness unknown")
    monkeypatch.setattr(doctor, "_record", busy)
    monkeypatch.setattr(doctor, "_helper", lambda *args: pytest.fail("busy writer selected owner"))
    assert doctor.targeted_doctor(arguments("--fix")) == 1
    assert "liveness unknown" in json.loads(capsys.readouterr().out)["error"]


def test_fix_requires_exact_recovery_selection_and_holds_admission(owner: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[list[str]] = []
    admitted: list[str] = []
    def invoke(script: Any, values: Any) -> Any:
        calls.append(values)
        if values == ["info"]:
            return {"capabilities": ["agenda-placement-doctor-v1"]}
        assert admitted == ["lifecycle", "launch"]
        return {"state": "external-correct", "source_retired": True}
    monkeypatch.setattr(doctor, "_invoke", invoke)
    def lifecycle(*args: Any, **kwargs: Any) -> Any:
        admitted.append("lifecycle")
        return nullcontext()

    def launch(*args: Any) -> Any:
        admitted.append("launch")
        return nullcontext()

    monkeypatch.setattr(fin, "FinalizeLock", lifecycle)
    monkeypatch.setattr(launch_admission, "guard", launch)
    assert doctor.targeted_doctor(arguments("--fix")) == 1
    assert "requires export home" in json.loads(capsys.readouterr().out)["error"]
    assert admitted == []
    assert doctor.targeted_doctor(arguments(
        "--fix", "--agenda-export-home", str(owner.parent.parent / "export"),
        "--agenda-lane", "main", "--agenda-store-id", "a" * 32,
        "--agenda-expected-revision", "3", "--agenda-operation-id", "fixture-move",
    )) == 0
    assert json.loads(capsys.readouterr().out)["agenda"]["source_retired"]
    assert calls[-1][0] == "repair"


def test_real_selected_helper_can_negotiate_over_isolated_python() -> None:
    helper = (
        Path(__file__).resolve().parents[2] / "efforts" / "skills"
        / "driving-effort-agendas" / "scripts" / "agenda_doctor.py"
    )
    assert doctor._invoke(helper, ["info"])["capabilities"] == ["agenda-placement-doctor-v1"]


@pytest.mark.parametrize("active,probes_ok,mux_ok", [
    (True, True, True), (False, False, True), (False, True, False), (False, True, True),
])
def test_record_admission_requires_strict_cold_local_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active: Any, probes_ok: Any, mux_ok: Any,
) -> None:
    record: Any = SimpleNamespace(platform="fixture")
    config: Any = SimpleNamespace(repo_name="fixture")
    policy, probes = [], []
    monkeypatch.setattr(doctor, "detect_platform", lambda: "fixture")
    monkeypatch.setattr(execution_spaces, "require_current_execution_space", lambda *args: None)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda *args, **kwargs: policy.append(kwargs))
    monkeypatch.setattr(data, "_project_root", lambda *args: (tmp_path, None))
    monkeypatch.setattr(data, "_canonical_record", lambda *args: record)
    def observe(selected: Any, **kwargs: Any) -> Any:
        probes.append(kwargs)
        return SimpleNamespace(active=active, probes_ok=probes_ok, mux_probe_ok=mux_ok)
    monkeypatch.setattr(sessions, "verify_worktree_active", observe)
    if active or not probes_ok or not mux_ok:
        with pytest.raises(ValueError, match="confirmed stopped"):
            doctor._record(config, tmp_path / "selected.yaml")
    else:
        assert doctor._record(config, tmp_path / "selected.yaml") is record
    assert policy == [{"recover_receiving": False}] and probes == [{"strict": True}]
