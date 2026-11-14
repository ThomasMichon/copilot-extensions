"""Retired label repair cannot restamp records or enter another doctor pass."""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from agent_worktrees import config as cfg, maintenance_cli
from agent_worktrees import machine_record_repair as repair


def arguments(**changes) -> Namespace:
    return Namespace(**{
        "repair_machine_record": "opaque-record", "from_machine": "former-name",
        "to_machine": "requested-name", "fix": False, "json": True, **changes,
    })


@pytest.mark.parametrize("apply", [False, True])
def test_legacy_repair_is_read_only_even_with_fix(tmp_path, monkeypatch, capsys, apply):
    records = tmp_path / "worktrees"
    records.mkdir()
    path = records / "opaque-record.yaml"
    path.write_bytes(
        b"# retained\r\nworktree_id: opaque-record\r\nmachine: former-name\r\n"
        b"platform: wsl\r\nmachine_identity_version: 99\r\nenvironment: wsl\r\n"
        b"execution_machine: former-guest\r\nowner_ref: remote/project/owner\r\n"
        b"unknown_future_field: preserved\r\n"
    )
    audit = records / "opaque-record.yaml.machine-label-history.json"
    audit.write_bytes(b'{"state":"prepared","from_machine":"former-name"}')
    before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in records.iterdir()}
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    for owner, name in (
        (cfg, "load_config"), (cfg, "tracking_dir"), (maintenance_cli.inst, "read_projects_registry"),
    ):
        monkeypatch.setattr(
            owner, name, lambda *args, **kwargs: pytest.fail("retired repair must not inspect or mutate state"),
        )
    assert maintenance_cli.cmd_doctor(arguments(fix=apply)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["retired"] and result["error"] is None
    assert not result["repairable"] and not result["repaired"]
    assert result["resident_projection"] == "not-verified"
    assert "derived live" in result["reason"]
    assert {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in records.iterdir()} == before


@pytest.mark.parametrize("changes", [
    {"repair_machine_record": "../other"},
    {"repair_machine_record": None},
    {"from_machine": None},
    {"from_machine": " "},
    {"repair_creation": "another-record"},
    {"gc_sessions": True},
    {"apply_daemon_health": True},
])
def test_legacy_selectors_remain_strict_and_cannot_combine_passes(monkeypatch, capsys, changes):
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    assert repair.targeted_doctor(arguments(fix=True, **changes)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] and not report["repaired"]


def test_retired_notice_requires_selected_project(monkeypatch, capsys):
    monkeypatch.setattr(cfg, "project_name", lambda: None)
    assert repair.targeted_doctor(arguments()) == 1
    assert "selected project" in json.loads(capsys.readouterr().out)["error"]
