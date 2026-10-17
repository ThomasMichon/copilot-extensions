"""Machine label normalization preserves the exact native worktree and its state."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, git_ops, maintenance_cli, pr_authority
from agent_worktrees import machine_record_repair as repair


def arguments(**changes):
    return Namespace(**{
        "repair_machine_record": "old-host-win-example",
        "from_machine": "old-host", "fix": False, "json": True, **changes,
    })


@pytest.fixture(scope="session")
def git_checkout(tmp_path_factory):
    root = tmp_path_factory.mktemp("machine-repair-repository")
    anchor = root / "anchor"
    checkout = root / "worktrees" / "old-host-win-example"
    anchor.mkdir()
    git_ops.git("init", cwd=anchor)
    git_ops.git("-c", "user.name=Example", "-c", "user.email=example@example.com",
                "commit", "--allow-empty", "-m", "fixture", cwd=anchor)
    git_ops.git("worktree", "add", "-b", "worktree/example", str(checkout), cwd=anchor)
    return anchor, checkout


@pytest.fixture
def local_record(tmp_path, monkeypatch, git_checkout):
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    anchor, checkout = git_checkout
    config = cfg.Config(
        srcroot=str(tmp_path), machine="canonical-host", platform=cfg.detect_platform(),
        repo_name="example", repos={"example": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(checkout.parent),
        )},
    )
    records = tmp_path / "records"
    records.mkdir()
    path = records / "old-host-win-example.yaml"
    data = {
        "worktree_id": path.stem, "branch": "worktree/example",
        "worktree_path": str(checkout), "repo": "example",
        "machine": "old-host", "platform": config.platform, "status": "active",
        "owner_ref": "old-host/other/parent#session",
        "pair_ref": "old-host/other/sibling", "head_session": "real-session",
        "sessions": [{"id": "real-session", "state": "active"}],
        "resources": [{"kind": "worktree", "ref": "old-host/other/child", "state": "active"}],
        "unknown_future_field": {"preserve": ["exact", "data"]},
    }
    path.write_text("# keep this comment\n" + yaml.safe_dump(data), encoding="utf-8")
    entries = {"canonical-host": cfg.MachineEntry(
        key="canonical-host", display_name="Canonical host",
        alias="canonical-host", hostname="old-host",
    )}
    monkeypatch.setattr(repair.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(repair.cfg, "load_config", lambda **kwargs: config)
    monkeypatch.setattr(repair.cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(repair.socket, "gethostname", lambda: "old-host")
    monkeypatch.setattr(repair.execution_spaces, "registry_for_config", lambda _: entries)
    monkeypatch.setattr(pr_authority.cfg, "install_dir", lambda: tmp_path / "installation")
    return config, path, data


@pytest.mark.guard
def test_report_apply_idempotency_preserve_every_other_field(local_record, capsys, monkeypatch):
    _, path, data = local_record
    before = path.read_bytes()
    monkeypatch.setattr(
        maintenance_cli.inst, "read_projects_registry",
        lambda: pytest.fail("targeted repair must not enter general doctor"),
    )
    assert maintenance_cli.cmd_doctor(arguments()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["repairable"] and not report["repaired"]
    assert path.read_bytes() == before
    assert maintenance_cli.cmd_doctor(arguments(fix=True)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["repaired"]
    assert (path.parent / report["backup"]).read_bytes() == before
    assert yaml.safe_load(path.read_bytes()) == {**data, "machine": "canonical-host"}
    assert path.read_bytes() == before.replace(b"machine: old-host", b'machine: "canonical-host"')
    applied = path.read_bytes()
    assert maintenance_cli.cmd_doctor(arguments(fix=True)) == 0
    assert not json.loads(capsys.readouterr().out)["repaired"]
    assert path.read_bytes() == applied


@pytest.mark.guard
@pytest.mark.parametrize("changes", [
    {"repair_machine_record": "../foreign"},
    {"repair_machine_record": None},
    {"from_machine": None},
    {"from_machine": "foreign-host"},
    {"gc_sessions": True}, {"prune_pivots": True}, {"apply_daemon_health": True},
    {"repair_record": "other"}, {"repair_creation": "other"},
    {"guest_distribution": "guest"},
])
def test_invalid_or_combined_requests_refuse(local_record, capsys, changes):
    _, path, _ = local_record
    before = path.read_bytes()
    assert repair.targeted_doctor(arguments(fix=True, **changes)) == 1
    assert json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.bak"))


@pytest.mark.guard
@pytest.mark.parametrize("field,value", [
    ("platform", "wsl"), ("repo", "foreign"), ("machine", "foreign-host"),
    ("worktree_id", "different"), ("worktree_path", "relative"),
])
def test_record_identity_and_local_checkout_are_required(local_record, capsys, field, value):
    _, path, data = local_record
    path.write_text(yaml.safe_dump({**data, field: value}), encoding="utf-8")
    before = path.read_bytes()
    assert repair.targeted_doctor(arguments(fix=True)) == 1
    assert json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before


@pytest.mark.guard
def test_checkout_from_another_repository_refuses(local_record, capsys, tmp_path):
    config, path, data = local_record
    other = tmp_path / "other"
    other.mkdir()
    git_ops.git("init", cwd=other)
    git_ops.git("-c", "user.name=Example", "-c", "user.email=example@example.com",
                "commit", "--allow-empty", "-m", "fixture", cwd=other)
    checkout = Path(config.default_repo.worktree_root) / "foreign"
    git_ops.git("worktree", "add", "-b", "foreign", str(checkout), cwd=other)
    path.write_text(yaml.safe_dump({**data, "worktree_path": str(checkout)}), encoding="utf-8")
    before = path.read_bytes()
    assert repair.targeted_doctor(arguments(fix=True)) == 1
    assert "repository" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before


@pytest.mark.guard
def test_guest_execution_and_foreign_observed_host_refuse(local_record, monkeypatch):
    config, _, _ = local_record
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    with pytest.raises(ValueError, match="native"):
        repair._prove_local_alias(replace(config, platform="wsl"), "old-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: config.platform)
    monkeypatch.setattr(socket, "gethostname", lambda: "foreign-host")
    with pytest.raises(ValueError, match="one local machine"):
        repair._prove_local_alias(config, "old-host")


@pytest.mark.guard
def test_no_roster_requires_observed_hostname(local_record, monkeypatch):
    config, _, _ = local_record
    monkeypatch.setattr(repair.execution_spaces, "registry_for_config", lambda _: {})
    repair._prove_local_alias(config, "old-host")
    with pytest.raises(ValueError, match="observed local hostname"):
        repair._prove_local_alias(config, "foreign-host")


@pytest.mark.guard
@pytest.mark.parametrize("target", ["canonical-host-wsl", "canonical-host (WSL)"])
def test_no_roster_guest_target_cannot_relabel_native_record(
    local_record, capsys, monkeypatch, target,
):
    config, path, _ = local_record
    monkeypatch.setattr(repair.execution_spaces, "registry_for_config", lambda _: {})
    monkeypatch.setattr(cfg, "load_config", lambda **kwargs: replace(config, machine=target))
    before = path.read_bytes()
    assert repair.targeted_doctor(arguments(fix=True)) == 1
    assert "environment" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.bak"))


@pytest.mark.guard
def test_ambiguous_roster_refuses(local_record, capsys, monkeypatch):
    _, path, _ = local_record
    entries = {name: cfg.MachineEntry(
        key=name, display_name=name, hostname="old-host",
    ) for name in ("canonical-host", "another-host")}
    monkeypatch.setattr(repair.execution_spaces, "registry_for_config", lambda _: entries)
    before = path.read_bytes()
    assert repair.targeted_doctor(arguments(fix=True)) == 1
    assert json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before


@pytest.mark.guard
def test_duplicate_machine_field_refuses(local_record, capsys):
    _, path, _ = local_record
    with path.open("a", encoding="utf-8") as handle:
        handle.write("machine: old-host\n")
    before = path.read_bytes()
    assert repair.targeted_doctor(arguments(fix=True)) == 1
    assert json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before


@pytest.mark.guard
@pytest.mark.parametrize("line,expected", [
    ("machine: old-host  # operator note\r\n",
     'machine: "canonical-host"  # operator note\r\n'),
    ("machine : 'old-host'   #\tnote\r\n",
     'machine : "canonical-host"   #\tnote\r\n'),
    ('machine: "old-host"   \n', 'machine: "canonical-host"   \n'),
])
def test_only_scalar_source_span_changes(line, expected):
    raw = "# preserved\r\n" + line + "status: active\r\n"
    data = repair.tracking_yaml_repair.strict_mapping(raw)
    fixed = repair._replacement(raw, data, "canonical-host")
    assert fixed == "# preserved\r\n" + expected + "status: active\r\n"


@pytest.mark.guard
@pytest.mark.skipif(
    cfg.detect_platform() not in ("windows", "linux"),
    reason="success contract requires native execution; guest refusal is tested separately",
)
def test_real_cli_uses_selected_project_and_preserves_claims(tmp_path):
    home, anchor = tmp_path / "home", tmp_path / "anchor"
    anchor.mkdir()
    git_ops.git("init", cwd=anchor)
    git_ops.git("-c", "user.name=Example", "-c", "user.email=example@example.com",
                "commit", "--allow-empty", "-m", "fixture", cwd=anchor)
    checkout = tmp_path / "worktrees" / "old-host-example"
    git_ops.git("worktree", "add", "-b", "worktree/example", str(checkout), cwd=anchor)
    state = home / ".example"
    records = state / "worktrees"
    records.mkdir(parents=True)
    registry = home / ".agent-worktrees"
    registry.mkdir()
    config = {
        "machine": "canonical-host", "platform": cfg.detect_platform(), "repo_name": "example",
        "repos": {"example": {"anchor": str(anchor), "worktree_root": str(checkout.parent)}},
    }
    (state / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"example": {"config_dir": str(state)}},
    }), encoding="utf-8")
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "repos": {"example": {"class": "worktree", "windows": str(anchor), "linux": str(anchor)}},
    }), encoding="utf-8")
    path = records / "old-host-example.yaml"
    data = {
        "worktree_id": path.stem, "worktree_path": str(checkout), "branch": "worktree/example",
        "repo": "example", "machine": socket.gethostname(), "platform": cfg.detect_platform(),
        "status": "active", "owner_ref": "old-host/other/parent#session",
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_WORKTREES_", "AGENT_HOME"))}
    env.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
               AGENT_WORKTREES_STATUS_MONITOR="0",
               PYTHONPATH=str(Path(repair.__file__).resolve().parents[1]))
    command = [
        sys.executable, "-m", "agent_worktrees", "--project", "example", "doctor",
        "--repair-machine-record", path.stem, "--from-machine", socket.gethostname(), "--json",
    ]
    for apply in (False, True):
        result = subprocess.run(
            [*command, *(["--fix"] if apply else [])], cwd=anchor,
            env=env, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        report = json.loads(result.stdout)
        assert report["repairable"] and report["repaired"] is apply
        if not apply:
            assert path.read_bytes() == before
    assert yaml.safe_load(path.read_bytes()) == {**data, "machine": "canonical-host"}
