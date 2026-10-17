"""Synthetic receiving-side provenance and explicit owner-repair contracts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from agent_procutil import no_window_kwargs
from machine_transport.registry import MachineEntry, SSHEnvironment

from agent_worktrees import config as cfg, doctor_cli_parser, maintenance_cli
from agent_worktrees import owner_migration as migration
from agent_worktrees import pr_authority, repos, tracking

OLD, NEW, PROJECT, RECORD = "old-host", "local-space", "sample", "historical-identity"


def args(*flags):
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args([
        "doctor", "--migrate-owner", RECORD, "--from-machine", OLD,
        "--to-machine", NEW, "--json", *flags,
    ])


@pytest.fixture
def receiving(tmp_path, monkeypatch):
    directories = {name: tmp_path / name / "tracking" for name in (PROJECT, "knowledge")}
    for directory in directories.values():
        directory.mkdir(parents=True)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    root = tmp_path / "checkouts"
    config = cfg.Config(
        srcroot=str(tmp_path), machine=NEW, platform="windows", repo_name=PROJECT,
        repos={PROJECT: cfg.RepoConfig(anchor=str(anchor), worktree_root=str(root))},
    )
    entries = {NEW: MachineEntry(key=NEW, display_name=NEW, hostname=OLD, execution_platform="windows")}
    registry = repos.ReposRegistry(repos={
        PROJECT: repos.RepoEntry(PROJECT, repo_class="worktree", paths={"windows": str(anchor)}),
        "knowledge": repos.RepoEntry("knowledge", repo_class="knowledge", paths={"windows": str(tmp_path / "notes")}),
    })
    monkeypatch.setattr(cfg, "project_name", lambda: PROJECT)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **kw: config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: directories[name or PROJECT])
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "installation")
    monkeypatch.setattr(migration.execution_spaces, "registry_for_config", lambda config: entries)
    monkeypatch.setattr(repos, "read_registry", lambda **kw: registry)
    # Only sample is adopted. Knowledge must be found through canonical repos.
    monkeypatch.setattr(pr_authority, "_ledgers", lambda project: {directories[PROJECT].resolve(): PROJECT})
    path = directories[PROJECT] / f"{RECORD}.yaml"
    data = dict(
        worktree_id=RECORD, repo=PROJECT, machine=OLD, platform="windows",
        worktree_path=str(root / RECORD), branch=f"worktree/{RECORD}",
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=3, title="Retained title", status="completed",
        completed_at="2026-01-02T00:00:00",
        head_session="session-one", sessions=[{"session_id": "session-one"}],
        paused=True, follow_up=True,
        custom_note="Retain passive extension data",
    )

    def write(**changes):
        data.update(changes)
        path.write_text("# retained comment\n" + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path.read_bytes()

    write()
    return tmp_path, path, data, config, entries, registry, directories, write


def run(capsys, *flags):
    code = maintenance_cli.cmd_doctor(args(*flags))
    return code, json.loads(capsys.readouterr().out)


def snapshot(root):
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
        for path in [root, *root.rglob("*")]
    }


@pytest.mark.parametrize("exists", [False, True])
def test_report_is_read_only_and_apply_preserves_record(receiving, capsys, exists):
    root, path, data, _, _, _, _, _ = receiving
    if exists:
        Path(data["worktree_path"]).mkdir(parents=True)
    before = snapshot(root)
    code, report = run(capsys)
    assert code == 0 and report["status"] == "eligible"
    assert report["scanned_records"] == 1 and report["checkout_exists"] is exists
    assert snapshot(root) == before
    original = path.read_bytes()
    code, report = run(capsys, "--fix")
    assert code == 0 and report["status"] == "migrated" and report["changed"]
    assert Path(report["backup"]).read_bytes() == original
    audit = json.loads(Path(report["audit"]).read_text())
    assert audit["state"] == "committed"
    assert audit["from_machine"] == OLD and audit["to_machine"] == NEW
    assert yaml.safe_load(path.read_bytes()) == {**data, "machine": NEW}
    assert path.read_bytes() == original.replace(b"machine: old-host", b'machine: "local-space"')
    after = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 0 and report["status"] == "unchanged" and not report["changed"]
    assert snapshot(root) == after


@pytest.mark.parametrize("changes", [
    {"repo": "foreign"}, {"platform": "wsl"}, {"worktree_id": "other"},
    {"worktree_path": "relative"}, {"machine": "foreign"},
])
def test_foreign_record_provenance_is_refused(receiving, capsys, changes):
    root, path, _, _, _, _, _, write = receiving
    write(**changes)
    before = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 1 and report["status"] == "blocked" and not report["changed"]
    assert snapshot(root) == before and path.exists()


@pytest.mark.parametrize("case", ["foreign-old", "ambiguous-old", "ambiguous-new", "alias-target", "platform", "configured", "foreign-transport"])
def test_exact_canonical_labels_and_independent_execution_authority(receiving, capsys, monkeypatch, case):
    root, _, _, config, entries, _, _, _ = receiving
    if case == "foreign-old":
        entries[NEW] = replace(entries[NEW], hostname="")
        entries[OLD] = MachineEntry(key=OLD, display_name=OLD, execution_platform="windows")
    elif case == "ambiguous-old":
        entries[OLD] = MachineEntry(key=OLD, display_name=OLD, execution_platform="windows")
    elif case == "ambiguous-new":
        entries["other"] = MachineEntry(key="other", display_name="other", alias=NEW, execution_platform="windows")
    elif case == "alias-target":
        entries.clear()
        entries["canonical"] = MachineEntry(key="canonical", display_name="canonical", alias=NEW, hostname=OLD, execution_platform="windows")
    elif case == "platform":
        entries[NEW] = replace(entries[NEW], execution_platform="wsl")
    elif case == "foreign-transport":
        entries[NEW] = replace(entries[NEW], ssh_environments=[SSHEnvironment(name="wsl", alias=OLD)])
    else:
        monkeypatch.setattr(cfg, "load_project_config", lambda *a, **kw: replace(config, machine=OLD))
    before = snapshot(root)
    assert run(capsys, "--fix")[0] == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("field,value", [
    ("owner_ref", f"{OLD}/knowledge/parent"),
    ("pair_ref", f"{NEW}/knowledge/sibling"),
    ("controllers", [{"controller_ref": f"{NEW}/knowledge/controller"}]),
    ("resources", [{"kind": "worktree", "ref": f"{NEW}/knowledge/child"}]),
    ("resources", [{"kind": "task", "ref": "task-one", "state": "active"}]),
    ("execution_leg", {"provider": "external"}),
    ("custom_ref", f"cref1:session:{OLD}:knowledge/child#session"),
    ("owner_refs", [f"{NEW}/knowledge/parent"]),
    ("resources", [{"kind": "pr", "ref": "example/project#1", "context": {"owner_machine": OLD}}]),
])
def test_dependent_references_refuse_instead_of_partial_migration(receiving, capsys, field, value):
    root, _, _, _, _, _, _, write = receiving
    write(**{field: value})
    before = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 1 and ("dependent" in report["error"] or "embedded" in report["error"])
    assert snapshot(root) == before


def test_knowledge_only_store_inbound_reference_and_misplaced_record_are_fenced(receiving, capsys):
    root, _, _, _, _, _, directories, _ = receiving
    other = directories["knowledge"] / "dependent.yaml"
    other.write_text(yaml.safe_dump({
        "repo": "misplaced", "worktree_id": "dependent",
        "owner_ref": f"{OLD}/{PROJECT}/{RECORD}",
    }), encoding="utf-8")
    before = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 1 and "inbound reference in knowledge/dependent" in report["error"]
    assert snapshot(root) == before


def test_structured_peer_owner_reference_is_fenced(receiving, capsys):
    root, _, _, _, _, _, directories, _ = receiving
    (directories["knowledge"] / "structured.yaml").write_text(yaml.safe_dump({
        "owner": {"machine": OLD, "project": PROJECT, "worktree_id": RECORD},
    }), encoding="utf-8")
    before = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 1 and "inbound reference" in report["error"]
    assert snapshot(root) == before


def test_malformed_peer_inventory_fails_closed(receiving, capsys):
    root, _, _, _, _, _, directories, _ = receiving
    (directories["knowledge"] / "bad.yaml").write_text("machine: a\nmachine: b\n", encoding="utf-8")
    before = snapshot(root)
    assert run(capsys, "--fix")[0] == 1
    assert snapshot(root) == before


def test_duplicate_identity_in_another_store_is_not_migrated(receiving, capsys):
    root, path, _, _, _, _, directories, _ = receiving
    (directories["knowledge"] / path.name).write_bytes(path.read_bytes())
    before = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 1 and "duplicate or misplaced" in report["error"]
    assert snapshot(root) == before


def test_busy_record_lock_refuses_without_record_or_audit_write(receiving, capsys):
    _, path, _, _, _, _, _, _ = receiving
    ready, release = threading.Event(), threading.Event()

    def holder():
        with tracking._RecordLock(path, require_sidecar=True):
            ready.set()
            assert release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert ready.wait(5)
        original = path.read_bytes()
        code, report = run(capsys, "--fix")
        assert code == 1 and "busy" in report["error"]
        assert path.read_bytes() == original
        assert not list(path.parent.glob("*.json"))
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


def test_changed_inventory_refuses_under_real_locks(receiving, capsys, monkeypatch):
    _, path, _, _, _, _, _, _ = receiving
    inspect = migration._inspect
    calls = 0

    def change_before_locked_revalidation(*a):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(path.read_bytes() + b"# concurrent stamp\n")
        return inspect(*a)

    monkeypatch.setattr(migration, "_inspect", change_before_locked_revalidation)
    code, report = run(capsys, "--fix")
    assert code == 1 and "inventory changed" in report["error"]
    assert yaml.safe_load(path.read_bytes())["machine"] == OLD
    assert not list(path.parent.glob("*.bak"))


def test_stale_full_record_writer_cannot_restore_old_owner(receiving, capsys):
    _, path, _, _, _, _, _, _ = receiving
    stale = tracking.load_record(path)
    assert run(capsys, "--fix")[0] == 0
    with pytest.raises(ValueError, match="owner machine changed"):
        tracking.save_record(stale, path)
    assert yaml.safe_load(path.read_bytes())["machine"] == NEW


def test_publication_authority_fences_new_edges_not_unrelated_creation(receiving):
    _, path, _, _, _, _, _, _ = receiving
    ready, release = threading.Event(), threading.Event()

    def holder():
        with pr_authority.guard(timeout=0):
            ready.set()
            assert release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert ready.wait(5)
        record = replace(tracking.load_record(path), worktree_id="unlinked", machine=NEW)
        tracking.save_record(record, path.with_name("unlinked.yaml"))
        linked = replace(record, worktree_id="linked", owner_ref=f"{OLD}/{PROJECT}/{RECORD}")
        with pytest.raises(TimeoutError):
            tracking.save_record(linked, path.with_name("linked.yaml"))
        assert not path.with_name("linked.yaml").exists()
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


def test_audit_failure_reports_actual_committed_change(receiving, capsys, monkeypatch):
    _, path, _, _, _, _, _, _ = receiving
    atomic_write = tracking._atomic_write
    calls = 0

    def fail_final_audit(target, content):
        nonlocal calls
        if target.suffix == ".json":
            calls += 1
            if calls == 2:
                raise OSError("synthetic audit failure")
        atomic_write(target, content)

    monkeypatch.setattr(tracking, "_atomic_write", fail_final_audit)
    code, report = run(capsys, "--fix")
    assert code == 1 and report["status"] == "migrated-audit-failed" and report["changed"]
    assert yaml.safe_load(path.read_bytes())["machine"] == NEW
    assert list(path.parent.glob("*.bak"))
    after = snapshot(receiving[0])
    code, report = run(capsys, "--fix")
    assert code == 1 and "incomplete audit" in report["error"]
    assert snapshot(receiving[0]) == after


@pytest.mark.parametrize("flags", [
    ["--gc-sessions"], ["--repair-record", RECORD],
    ["--guest-distribution", "Example"], ["--from-machine", NEW],
    ["--migrate-owner", "../escape"], ["--to-machine", OLD],
])
def test_invalid_or_mixed_scope_never_runs_general_doctor(receiving, capsys, flags):
    root = receiving[0]
    before = snapshot(root)
    assert run(capsys, "--fix", *flags)[0] == 1
    assert snapshot(root) == before


def test_native_public_cli_report_uses_only_synthetic_registered_state(tmp_path):
    home = tmp_path / "home"
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    anchor = tmp_path / "sample"
    anchor.mkdir()
    (anchor / ".agent-worktrees").mkdir()
    platform = "windows" if sys.platform == "win32" else "linux"
    checkout_root = tmp_path / "sample.worktrees"
    store = home / ".sample" / "worktrees"
    store.mkdir(parents=True)
    for path, data in (
        (registry / "config.yaml", {"machine": NEW, "platform": platform}),
        (registry / "repos.yaml", {"schema_version": 1, "repos": {
            PROJECT: {"class": "worktree", platform: str(anchor), "default_branch": "dev"},
        }}),
        (registry / "projects.yaml", {"schema_version": 2, "projects": {
            PROJECT: {"config_dir": str(home / ".sample"), "expose_agent": False},
        }}),
        (anchor / ".agent-worktrees" / "machines.yaml", {"machines": {
            NEW: {"hostname": OLD, "execution_platform": platform},
        }}),
        (home / ".sample" / "config.yaml", {"repo_name": PROJECT, "repos": {
            PROJECT: {"anchor": str(anchor), "worktree_root": str(checkout_root)},
        }}),
        (store / f"{RECORD}.yaml", {
            "worktree_id": RECORD, "repo": PROJECT, "machine": OLD,
            "platform": platform, "worktree_path": str(checkout_root / RECORD),
            "branch": f"worktree/{RECORD}", "status": "completed",
        }),
    ):
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = snapshot(home)
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "-p", PROJECT, "doctor",
         "--migrate-owner", RECORD, "--from-machine", OLD, "--to-machine", NEW, "--json"],
        env={**os.environ, "AGENT_HOME": str(home)},
        cwd=anchor, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "eligible"
    assert snapshot(home) == before
