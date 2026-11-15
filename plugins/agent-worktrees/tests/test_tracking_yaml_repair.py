"""Exact agent-text round trips and scoped, fenced legacy tracking recovery."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import health, maintenance_cli, pr_authority, tracking, tracking_yaml_repair as repair

_PREFIX = "worktree_id: wt-agent\nbranch: worktree/wt-agent\nstatus: active\n"
_TEXT = "Standing reviewer.\n\nLoop: read and review.\nKeep 'author' ownership: do not replace it."
_SUFFIX = "pair_id: pair-one\ncodename: quiet-crate\nsessions: []\n"
_RAW = _PREFIX + "bound_agent: " + _TEXT + "\n" + _SUFFIX


def args(**kwargs) -> Namespace:
    return Namespace(
        **{
            "repair_record": "wt-agent", "bound_agent_end_field": "pair_id",
            "fix": False, "json": True, "gc_sessions": False,
            "prune_pivots": False, "apply_daemon_health": False,
            **kwargs,
        }
    )


@pytest.fixture
def record_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(repair.cfg, "project_name", lambda: "test-project")
    monkeypatch.setattr(repair.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(pr_authority.cfg, "install_dir", lambda: tmp_path / "installation")
    path = tmp_path / "wt-agent.yaml"
    path.write_bytes(_RAW.encode())
    return tmp_path, path


@pytest.mark.guard
@pytest.mark.parametrize("agent", [
    "standing-reviewer", "yes", "agent: reviewer", _TEXT,
    'Mixed "quotes": keep\n\nall paragraphs.', "Spaces # and comments",
])
def test_bound_agent_writer_round_trips_arbitrary_text(tmp_path, agent):
    path = tmp_path / "wt-agent.yaml"
    rec = tracking.WorktreeRecord(
        worktree_id="wt-agent", branch="worktree/wt-agent",
        worktree_path=str(tmp_path / "wt-agent"), repo="test-project",
        machine="test-machine", platform="windows", started_at="",
        last_resumed_at="", resume_count=0, title=None,
        status="active", completed_at=None, bound_agent=agent, sessions=[],
    )
    tracking.save_record(rec, path)
    data = yaml.safe_load(path.read_text())
    assert data["bound_agent"] == agent
    assert data["status"] == "active" and data["sessions"] == []
    assert tracking.load_record(path).bound_agent == agent


@pytest.mark.guard
def test_legacy_repair_preserves_exact_agent_text_and_surrounding_fields():
    fixed = repair.repair_bound_agent(_RAW, "pair_id")
    assert fixed.startswith(_PREFIX) and fixed.endswith(_SUFFIX)
    assert repair.strict_mapping(fixed) == {
        "worktree_id": "wt-agent", "branch": "worktree/wt-agent", "status": "active",
        "bound_agent": _TEXT, "pair_id": "pair-one", "codename": "quiet-crate", "sessions": [],
    }


@pytest.mark.guard
@pytest.mark.parametrize("raw,boundary", [
    (_RAW, "status"),
    (_RAW, "sessions"),
    (_RAW, "codename"),
    (_RAW.replace("pair_id:", "foreign:"), "pair_id"),
    (_RAW.replace("Loop:", "pair_id: duplicated\nLoop:"), "pair_id"),
    (_RAW + "sessions: [foreign]\n", "pair_id"),
    (_RAW.replace("codename:", "foreign:"), "pair_id"),
    (_RAW.replace("status: active", "status: active\nstatus: complete"), "pair_id"),
    (_RAW.replace("bound_agent:", "bound_agent: |\n  safe\nbound_agent:"), "pair_id"),
    (_RAW.replace("bound_agent: ", "bound_agent: '"), "pair_id"),
])
def test_ambiguous_or_foreign_boundary_refuses(raw, boundary):
    assert repair.repair_bound_agent(raw, boundary) is None


@pytest.mark.guard
def test_targeted_report_and_apply_do_not_touch_other_records(record_dir, capsys, monkeypatch):
    root, path = record_dir
    sibling = root / "wt-other.yaml"
    sibling.write_bytes(b"unrelated: malformed: record\n")
    monkeypatch.setattr(
        maintenance_cli.inst, "read_projects_registry",
        lambda: pytest.fail("targeted repair must not enumerate projects"),
    )
    before = path.read_bytes()
    assert maintenance_cli.cmd_doctor(args()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["repairable"] and not report["repaired"]
    assert path.read_bytes() == before
    assert maintenance_cli.cmd_doctor(args(fix=True)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["repaired"] and report["scope"] == "record"
    assert (root / report["backup"]).read_bytes() == before
    assert repair.strict_mapping(path.read_text())["bound_agent"] == _TEXT
    assert sibling.read_bytes() == b"unrelated: malformed: record\n"
    assert maintenance_cli.cmd_doctor(args(fix=True)) == 0
    assert not json.loads(capsys.readouterr().out)["repaired"]


@pytest.mark.parametrize("entrypoint", ["bound-agent", "targeted-scalar", "general"])
def test_actual_repair_removes_legacy_naming_but_preserves_original_evidence(
    record_dir, capsys, entrypoint,
):
    root, path = record_dir
    naming = (
        "machine: &old_name obsolete-host\nplatform: wsl\nmachine_identity_version: 1\n"
        "environment: wsl\nexecution_machine: obsolete-guest\nsummary: *old_name\n"
    )
    raw = naming + (
        _RAW if entrypoint == "bound-agent"
        else _PREFIX + "title: original: title\nresources: []\n"
    )
    if entrypoint == "general":
        raw += "future_evidence: {original: keep}\n"
    path.write_bytes(raw.encode())

    def invoke(apply):
        if entrypoint == "general":
            findings = health.repair_yaml_integrity(root, apply=apply)
            assert len(findings) == 1 and findings[0].repairable
            return findings[0].repaired
        assert maintenance_cli.cmd_doctor(args(
            fix=apply, bound_agent_end_field="pair_id" if entrypoint == "bound-agent" else None,
        )) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["repairable"]
        return report["repaired"]

    assert not invoke(False)
    assert path.read_bytes() == raw.encode() and not list(root.glob("*.bak"))
    assert invoke(True)
    repaired = repair.strict_mapping(path.read_text(encoding="utf-8"))
    assert not {
        "machine", "platform", "machine_identity_version", "environment", "execution_machine",
    }.intersection(repaired)
    assert repaired["summary"] == "obsolete-host"
    if entrypoint == "bound-agent":
        assert repaired["bound_agent"] == _TEXT and repaired["pair_id"] == "pair-one"
    else:
        assert repaired["title"] == "original: title" and repaired["resources"] == []
    if entrypoint == "general":
        assert repaired["future_evidence"] == {"original": "keep"}
    assert next(root.glob("*.bak")).read_bytes() == raw.encode()


@pytest.mark.parametrize("entrypoint", ["targeted", "general"])
def test_valid_legacy_record_is_not_proactively_migrated(record_dir, capsys, entrypoint):
    root, path = record_dir
    original = (_PREFIX + "machine: old-host\nplatform: linux\n").encode()
    path.write_bytes(original)
    before = path.stat().st_mtime_ns
    if entrypoint == "general":
        assert health.repair_yaml_integrity(root, apply=True) == []
    else:
        assert repair.targeted_doctor(args(fix=True, bound_agent_end_field=None)) == 0
        report = json.loads(capsys.readouterr().out)
        assert not report["repairable"] and not report["repaired"]
    assert path.read_bytes() == original and path.stat().st_mtime_ns == before
    assert not list(root.glob("*.bak"))


@pytest.mark.parametrize("summary", [
    "&kept original text", "[original, evidence]", "{original: evidence}",
])
def test_general_repair_preserves_explicit_yaml_values(record_dir, summary):
    root, path = record_dir
    raw = _PREFIX + "machine: obsolete\nsummary: " + summary + "\ntitle: original: title\n"
    expected = repair.strict_mapping(raw.replace("title: original: title", "title: 'original: title'"))
    expected.pop("machine")
    path.write_bytes(raw.encode())
    findings = health.repair_yaml_integrity(root, apply=True)
    assert len(findings) == 1 and findings[0].repaired
    assert repair.strict_mapping(path.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("extra", [
    "status: complete\n", "summary: *missing_anchor\n",
])
def test_general_repair_refuses_unverified_mapping_without_replacement(record_dir, extra):
    root, path = record_dir
    raw = _PREFIX + "title: original: title\n" + extra
    path.write_bytes(raw.encode())
    findings = health.repair_yaml_integrity(root, apply=True)
    assert len(findings) == 1 and not findings[0].repairable and not findings[0].repaired
    assert path.read_bytes() == raw.encode() and not list(root.glob("*.bak"))


def test_general_repair_uses_observed_byte_fence(record_dir, monkeypatch):
    root, path = record_dir
    raw = _PREFIX + "machine: obsolete\ntitle: original: title\n"
    replacement = (_PREFIX + "title: changed by another writer\n").encode()
    path.write_bytes(raw.encode())
    fix = health.repair_yaml_text

    def competing_writer(text):
        fixed = fix(text)
        path.write_bytes(replacement)
        return fixed

    monkeypatch.setattr(health, "repair_yaml_text", competing_writer)
    findings = health.repair_yaml_integrity(root, apply=True)
    assert len(findings) == 1 and findings[0].repairable and not findings[0].repaired
    assert path.read_bytes() == replacement and not list(root.glob("*.bak"))


@pytest.mark.guard
@pytest.mark.parametrize("changes", [
    {"repair_record": "../wt-agent"}, {"repair_record": None},
    {"repair_record": "wt..agent"}, {"repair_record": r"wt\agent"},
    {"gc_sessions": True}, {"prune_pivots": True}, {"apply_daemon_health": True},
    {"bound_agent_end_field": None},
])
def test_targeted_repair_refuses_unsafe_requests(record_dir, capsys, changes):
    _, path = record_dir
    before = path.read_bytes()
    assert repair.targeted_doctor(args(fix=True, **changes)) == 1
    assert json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before


@pytest.mark.guard
def test_later_boundary_cannot_remove_pr_authority(record_dir, capsys):
    root, path = record_dir
    raw = _PREFIX + "bound_agent: " + _TEXT + "\n" + (
        "prs:\n- pr_id: original-pr\n  number: 42\n  state: open\n"
        "  branch: pr/original-agent\nsessions: []\n"
    )
    path.write_bytes(raw.encode())
    assert repair.targeted_doctor(args(fix=True, bound_agent_end_field="sessions")) == 1
    assert json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == raw.encode() and not list(root.glob("*.bak"))
    assert repair.targeted_doctor(args(fix=True, bound_agent_end_field="prs")) == 0
    assert json.loads(capsys.readouterr().out)["repaired"]
    data = repair.strict_mapping(path.read_text())
    assert data["bound_agent"] == _TEXT
    assert data["prs"] == [{
        "pr_id": "original-pr", "number": 42, "state": "open", "branch": "pr/original-agent",
    }]
    assert data["sessions"] == []


@pytest.mark.guard
@pytest.mark.parametrize("helper", [
    "session_backend_raw", "dispatch_attempt_raw", "controller_metadata_opaque",
])
def test_runtime_only_dataclass_helpers_are_not_serialized_fields(
    record_dir, capsys, helper,
):
    root, path = record_dir
    raw = _RAW.replace("status: active\n", f"status: active\n{helper}: null\n")
    path.write_bytes(raw.encode())
    assert repair.targeted_doctor(args(fix=True)) == 1
    assert "unknown fields" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == raw.encode() and not list(root.glob("*.bak"))


@pytest.mark.guard
def test_fqdn_machine_worktree_id_is_accepted(record_dir, capsys):
    root, old_path = record_dir
    record_id = "host.example-win-20261013-123456-abcd"
    path = root / f"{record_id}.yaml"
    path.write_bytes(_RAW.replace("wt-agent", record_id).encode())
    assert repair.targeted_doctor(args(fix=True, repair_record=record_id)) == 0
    assert json.loads(capsys.readouterr().out)["repaired"]
    assert repair.strict_mapping(path.read_text())["worktree_id"] == record_id
    assert old_path.read_bytes() == _RAW.encode()


def test_changed_record_refuses_before_backup_or_write(record_dir):
    root, path = record_dir
    observed = path.read_bytes()
    path.write_bytes(b"worktree_id: wt-agent\nstatus: complete\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        repair.apply_repair(path, observed, repair.repair_bound_agent(_RAW, "pair_id"))
    assert path.read_bytes() == before and not list(root.glob("*.bak"))


@pytest.mark.parametrize("held_authority", [False, True])
def test_native_writer_contention_refuses_without_mutation(record_dir, held_authority):
    root, path = record_dir
    ready, release = threading.Event(), threading.Event()

    def hold():
        guard = pr_authority.guard() if held_authority else tracking._RecordLock(path)
        with guard:
            ready.set()
            assert release.wait(timeout=5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert ready.wait(timeout=2)
        with pytest.raises(TimeoutError):
            repair.apply_repair(path, path.read_bytes(), repair.repair_bound_agent(_RAW, "pair_id"))
    finally:
        release.set()
        holder.join(timeout=2)
    assert not holder.is_alive()
    assert path.read_bytes() == _RAW.encode() and not list(root.glob("*.bak"))


def test_actual_competing_process_excludes_repair_without_backup(record_dir):
    root, path = record_dir
    ready, release = root / "writer-ready", root / "writer-release"
    code = """
import sys, time
from pathlib import Path
from agent_worktrees import tracking
path, ready, release = map(Path, sys.argv[1:])
with tracking._RecordLock(path, require_sidecar=True):
    ready.touch()
    deadline = time.monotonic() + 10
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert release.exists(), 'parent did not release its fixture writer'
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path), str(ready), str(release)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert ready.exists(), "fixture writer did not acquire its native sidecar"
        with pytest.raises(TimeoutError):
            repair.apply_repair(path, path.read_bytes(), repair.repair_bound_agent(_RAW, "pair_id"))
        assert path.read_bytes() == _RAW.encode() and not list(root.glob("*.bak"))
    finally:
        release.touch()
        try:
            out, err = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
    assert process.returncode == 0, out + err


def test_repair_holds_authority_and_record_locks_through_replacement(record_dir, monkeypatch):
    _, path = record_dir
    atomic = tracking._atomic_write
    results = []

    def write_under_guards(target, text):
        def compete():
            with tracking._RecordLock(target, blocking=False) as lock:
                results.append(lock.acquired)
            try:
                with pr_authority.guard(timeout=0):
                    results.append("admitted")
            except TimeoutError:
                results.append("blocked")

        competitor = threading.Thread(target=compete)
        competitor.start()
        competitor.join(timeout=2)
        assert not competitor.is_alive()
        atomic(target, text)

    monkeypatch.setattr(tracking, "_atomic_write", write_under_guards)
    repair.apply_repair(path, path.read_bytes(), repair.repair_bound_agent(_RAW, "pair_id"))
    assert results == [False, "blocked"]


def test_invalid_fixed_mapping_cannot_mutate(record_dir):
    root, path = record_dir
    with pytest.raises((ValueError, yaml.YAMLError)):
        repair.apply_repair(path, path.read_bytes(), "status: active\nstatus: complete\n")
    assert path.read_bytes() == _RAW.encode() and not list(root.glob("*.bak"))


@pytest.mark.guard
def test_unverifiable_existing_backup_refuses_without_replacement(record_dir):
    root, path = record_dir
    observed = path.read_bytes()
    digest = hashlib.sha256(observed).hexdigest()[:16]
    backup = root / f"wt-agent.yaml.repair-{digest}.bak"
    backup.write_bytes(b"foreign backup")
    with pytest.raises(ValueError, match="backup"):
        repair.apply_repair(path, observed, repair.repair_bound_agent(_RAW, "pair_id"))
    assert path.read_bytes() == observed and backup.read_bytes() == b"foreign backup"


@pytest.mark.guard
def test_failed_replacement_retains_exact_backup_and_original(record_dir, monkeypatch):
    root, path = record_dir
    observed = path.read_bytes()

    def fail(*_args):
        raise OSError("fixture atomic replacement failure")

    monkeypatch.setattr(tracking, "_atomic_write", fail)
    with pytest.raises(OSError, match="replacement"):
        repair.apply_repair(path, observed, repair.repair_bound_agent(_RAW, "pair_id"))
    assert path.read_bytes() == observed
    assert next(root.glob("*.bak")).read_bytes() == observed


@pytest.mark.guard
def test_backup_creation_is_exclusive_and_private(record_dir, monkeypatch):
    _, path = record_dir
    observed = path.read_bytes()
    original = os.open
    modes = []

    def opened(file, flags, mode=0o777, **kwargs):
        if str(file).endswith(".bak"):
            assert flags & os.O_CREAT and flags & os.O_EXCL
            modes.append(mode)
        return original(file, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    backup = repair.apply_repair(path, observed, repair.repair_bound_agent(_RAW, "pair_id"))
    assert modes == [0o600] and backup.read_bytes() == observed
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.guard
@pytest.mark.skipif(os.name == "nt", reason="POSIX file permission enforcement")
def test_existing_public_backup_refuses_without_mutation(record_dir):
    root, path = record_dir
    observed = path.read_bytes()
    digest = hashlib.sha256(observed).hexdigest()[:16]
    backup = root / f"wt-agent.yaml.repair-{digest}.bak"
    backup.write_bytes(observed)
    backup.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        repair.apply_repair(path, observed, repair.repair_bound_agent(_RAW, "pair_id"))
    assert path.read_bytes() == observed and backup.read_bytes() == observed


def test_real_cli_reports_and_repairs_only_named_record(tmp_path):
    home, anchor = tmp_path / "home", tmp_path / "anchor"
    anchor.mkdir()
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    state = home / ".test-project"
    records = state / "worktrees"
    records.mkdir(parents=True)
    config = {
        "repo_name": "test-project",
        "repos": {"test-project": {"anchor": str(anchor), "remote": "origin",
                                  "default_branch": "dev", "worktree_root": str(anchor)}},
    }
    (state / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"test-project": {"config_dir": str(state)}},
    }), encoding="utf-8")
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "repos": {"test-project": {"class": "worktree", sys.platform: str(anchor),
                                   "windows": str(anchor), "linux": str(anchor)}},
    }), encoding="utf-8")
    path = records / "wt-agent.yaml"
    path.write_bytes(_RAW.encode())
    sibling = records / "wt-other.yaml"
    sibling.write_bytes(b"unrelated: malformed: record\n")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("COPILOT_") or key.startswith("AGENT_WORKTREES_"):
            env.pop(key)
    env.update({"AGENT_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
                "AGENT_WORKTREES_STATUS_MONITOR": "0"})
    source = Path(repair.__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(source)
    command = [
        sys.executable, "-m", "agent_worktrees", "--project", "test-project",
        "doctor", "--repair-record", "wt-agent", "--bound-agent-end-field", "pair_id", "--json",
    ]
    report = subprocess.run(
        command, cwd=anchor, env=env, capture_output=True, text=True, timeout=30,
    )
    assert report.returncode == 0, report.stderr
    assert json.loads(report.stdout)["repairable"]
    assert path.read_bytes() == _RAW.encode()
    applied = subprocess.run(
        [*command, "--fix"], cwd=anchor, env=env, capture_output=True, text=True, timeout=30,
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["repaired"]
    assert repair.strict_mapping(path.read_text())["bound_agent"] == _TEXT
    assert sibling.read_bytes() == b"unrelated: malformed: record\n"
