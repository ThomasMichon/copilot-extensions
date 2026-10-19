"""Canonical label repair can prove removed records without transferring links."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import claim_handoff_accept_support, config as cfg, doctor_cli_parser
from agent_worktrees import execution_spaces, git_ops, legacy_wsl_record_locality, maintenance_cli
from agent_worktrees import machine_record_repair as repair
from agent_worktrees import repos, tracking

pytestmark = pytest.mark.guard
OLD, NEW, PROJECT, RECORD = "old-host", "canonical-host", "example", "old-host-win-history"


def arguments(*flags):
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args([
        "doctor", "--repair-machine-record", RECORD, "--from-machine", OLD,
        "--to-machine", NEW, "--json", *flags,
    ])


@pytest.fixture(scope="session")
def history_anchor(tmp_path_factory):
    anchor = tmp_path_factory.mktemp("history-repair-anchor")
    git_ops.git("init", cwd=anchor)
    return anchor


@pytest.fixture
def history(tmp_path, monkeypatch, history_anchor):
    root = tmp_path / "trees"
    state = tmp_path / PROJECT
    directory = state / "worktrees"
    directory.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_path), machine=NEW, platform="windows", repo_name=PROJECT,
        repos={PROJECT: cfg.RepoConfig(anchor=str(history_anchor), worktree_root=str(root))},
    )
    entries = {NEW: cfg.MachineEntry(
        key=NEW, display_name=NEW, hostname=OLD, execution_platform="windows",
    )}
    registration = repos.ReposRegistry(repos={PROJECT: repos.RepoEntry(
        PROJECT, repo_class="knowledge", paths={"windows": str(history_anchor)},
    )})
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "project_name", lambda: PROJECT)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path / (project or PROJECT))
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: tmp_path / (name or PROJECT) / "worktrees")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **kw: config)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "installation")
    monkeypatch.setattr(repair.socket, "gethostname", lambda: OLD)
    monkeypatch.setattr(execution_spaces, "registry_for_config", lambda config: entries)
    monkeypatch.setattr(repos, "read_registry", lambda **kw: registration)
    path = directory / f"{RECORD}.yaml"
    data = {
        "worktree_id": RECORD, "worktree_path": str(root / RECORD), "branch": f"worktree/{RECORD}",
        "repo": PROJECT, "machine": OLD, "platform": "windows", "status": "completed",
        "owner_ref": f"{OLD}/{PROJECT}/parent#session", "pair_ref": f"{OLD}/{PROJECT}/parent",
        "pair_id": "retained-pair", "head_session": "retained-head",
        "controllers": [{
            "kind": "worktree", "source": "explicit", "relation_revision": 1,
            "created_at": "now", "controller_ref": f"{OLD}/{PROJECT}/parent",
        }],
        "resources": [{
            "kind": "pr", "ref": "example/project#1", "handoff_bundle": "retained-bundle",
            "producer": {"version": 99, "owner_ref": f"{OLD}/{PROJECT}/{RECORD}"},
        }],
        "follow_ups": [{"id": "one", "summary": "retained", "refs": [
            {"kind": "resource-claim", "ref": f"{OLD}/{PROJECT}/parent"},
        ]}],
        "unknown_future_field": {"keep": True}, "paused": True,
    }
    for record_id, values in ((RECORD, data), ("parent", {
        "worktree_id": "parent", "worktree_path": str(root / "parent"),
        "branch": "worktree/parent", "repo": PROJECT, "machine": OLD,
        "platform": "windows", "status": "completed",
    })):
        (directory / f"{record_id}.yaml").write_bytes(
            ("# retained comment\r\n" + yaml.safe_dump(values, sort_keys=False).replace("\n", "\r\n")).encode("utf-8"),
        )
    return tmp_path, config, path, data, entries, registration


def run(capsys, *flags):
    code = maintenance_cli.cmd_doctor(arguments(*flags))
    return code, json.loads(capsys.readouterr().out)


def snapshot(root):
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
        for path in [root, *root.rglob("*")]
    }


@pytest.mark.parametrize("flags", [(), ("--fix",)])
def test_multiline_machine_scalar_refuses_preview_and_apply_without_writes(history, capsys, flags):
    root, _, path, _, _, _ = history
    original = path.read_bytes()
    path.write_bytes(original.replace(b"machine: old-host\r\n", b"machine: |-\r\n  old-host\r\n"))
    assert yaml.safe_load(path.read_bytes())["machine"] == OLD
    before = snapshot(root)
    code, report = run(capsys, *flags)
    assert code == 1 and "not a standalone scalar" in report["error"]
    assert not report["repairable"] and not report["repaired"]
    assert snapshot(root) == before


def test_removed_linked_record_report_apply_audit_and_same_alias_readers(history, capsys):
    root, config, path, data, _, _ = history
    before = snapshot(root)
    old_ref = tracking.parse_claim_ref(f"{OLD}/{PROJECT}/{RECORD}")
    new_ref = tracking.parse_claim_ref(f"{NEW}/{PROJECT}/{RECORD}")

    def prove_reference_semantics():
        assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(old_ref, config) == path
        assert legacy_wsl_record_locality.claim_refs_equivalent(old_ref, new_ref, config=config)
        record = tracking.load_record(path)
        execution_spaces.require_record_mutation(record, config)
        claim_handoff_accept_support.require_handoff_record(record, RuntimeError)
        assert claim_handoff_accept_support.local_record_ref(old_ref, machine=NEW)
        assert legacy_wsl_record_locality.claim_refs_equivalent(
            record.pair_claim_ref, tracking.parse_claim_ref(f"{NEW}/{PROJECT}/parent"), config=config,
        )

    prove_reference_semantics()
    code, report = run(capsys)
    assert code == 0 and report["repairable"] and not report["repaired"]
    assert report["proof"] == "removed-registered-namespace"
    assert snapshot(root) == before
    original = path.read_bytes()
    code, report = run(capsys, "--fix")
    assert code == 0 and report["repaired"] and report["references"] == "preserved"
    assert path.read_bytes() == original.replace(b"machine: old-host", b'machine: "canonical-host"')
    assert (path.parent / report["backup"]).read_bytes() == original
    receipt = json.loads((path.parent / report["audit"]).read_text())
    assert receipt["state"] == "committed" and receipt["proof"] == report["proof"]
    assert receipt["from_machine"] == OLD and receipt["to_machine"] == NEW
    assert yaml.safe_load(path.read_bytes()) == {**data, "machine": NEW}
    prove_reference_semantics()
    after = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 0 and not report["repaired"]
    assert snapshot(root) == after


@pytest.mark.parametrize("field,value", [
    ("repo", "foreign"), ("platform", "wsl"), ("worktree_id", "other"),
    ("machine", "foreign"), ("worktree_path", "relative"),
])
def test_history_wrong_record_scope_refuses_without_writes(history, capsys, field, value):
    root, _, path, data, _, _ = history
    path.write_text(yaml.safe_dump({**data, field: value}), encoding="utf-8")
    before = snapshot(root)
    assert run(capsys, "--fix")[0] == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("case", ["unregistered", "wrong-root", "legacy-space", "foreign-space", "wrong-target"])
def test_history_requires_explicit_receiving_registration(history, capsys, case):
    root, _, _, _, entries, registration = history
    flags = []
    if case == "unregistered":
        registration.repos.clear()
    elif case == "wrong-root":
        registration.repos[PROJECT].paths["windows"] = str(root / "foreign")
    elif case == "legacy-space":
        entries[NEW] = replace(entries[NEW], execution_platform="")
    elif case == "foreign-space":
        entries[NEW] = replace(entries[NEW], execution_platform="wsl")
    else:
        flags = ["--to-machine", "foreign"]
    before = snapshot(root)
    assert run(capsys, "--fix", *flags)[0] == 1
    assert snapshot(root) == before


def test_existing_non_git_directory_never_uses_history_fallback(history, capsys):
    root, _, _, data, _, _ = history
    Path(data["worktree_path"]).mkdir(parents=True)
    before = snapshot(root)
    code, report = run(capsys, "--fix")
    assert code == 1 and "existing local linked worktree" in report["error"]
    assert snapshot(root) == before


def test_changed_record_is_revalidated_before_commit(history, capsys, monkeypatch):
    _, _, path, _, _, _ = history
    inspect = repair._inspect
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(path.read_bytes() + b"# concurrent stamp\n")
        return inspect(*args)

    monkeypatch.setattr(repair, "_inspect", changed)
    code, report = run(capsys, "--fix")
    assert code == 1 and "changed" in report["error"]
    assert yaml.safe_load(path.read_bytes())["machine"] == OLD
    assert not list(path.parent.glob("*.bak"))


@pytest.mark.parametrize("existing", [False, True])
def test_git_proof_never_runs_under_a_record_lock(history, capsys, monkeypatch, existing):
    _, config, _, data, _, _ = history
    if existing:
        anchor = config.default_repo.anchor
        if git_ops.git("rev-parse", "--verify", "HEAD", cwd=anchor, check=False).returncode:
            git_ops.git(
                "-c", "user.name=Example", "-c", "user.email=example@example.com",
                "commit", "--allow-empty", "-m", "fixture", cwd=anchor,
            )
        git_ops.git("worktree", "add", "--detach", data["worktree_path"], cwd=anchor)
    record_lock, run_git = tracking._RecordLock, git_ops.git
    held = []

    class ObservedLock(record_lock):
        def __enter__(self):
            result = super().__enter__()
            if self.acquired:
                held.append(self)
            return result

        def __exit__(self, *args):
            if self in held:
                held.remove(self)
            return super().__exit__(*args)

    def proof_git(*args, **kwargs):
        assert not held, "Git proof must precede the short record lock"
        return run_git(*args, **kwargs)

    monkeypatch.setattr(tracking, "_RecordLock", ObservedLock)
    monkeypatch.setattr(git_ops, "git", proof_git)
    assert run(capsys, "--fix")[0] == 0


def test_incidental_configuration_change_does_not_change_receiving_authority(history, capsys, monkeypatch):
    config = history[1]
    calls = 0

    def preferences_changed(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs == {"include_control_plane_related_pr": False}
        return replace(config, headless=calls > 1)

    monkeypatch.setattr(cfg, "load_config", preferences_changed)
    assert run(capsys, "--fix")[0] == 0
    assert calls == 2


def test_bytes_changed_after_proof_are_refused_before_audit(history, capsys, monkeypatch):
    _, _, path, _, _, _ = history
    inspect, record_lock = repair._inspect, tracking._RecordLock
    calls = 0

    def counted(*args):
        nonlocal calls
        calls += 1
        return inspect(*args)

    def another_writer_finished(target, **kwargs):
        assert calls == 2
        path.write_bytes(path.read_bytes() + b"# newer record stamp\n")
        return record_lock(target, **kwargs)

    monkeypatch.setattr(repair, "_inspect", counted)
    monkeypatch.setattr(tracking, "_RecordLock", another_writer_finished)
    code, report = run(capsys, "--fix")
    assert code == 1 and "changed after proof" in report["error"]
    assert yaml.safe_load(path.read_bytes())["machine"] == OLD
    assert not list(path.parent.glob("*.json"))


def test_path_key_retains_native_case_semantics(history):
    anchor = Path(history[1].default_repo.anchor)
    same = repair._path_key(anchor) == repair._path_key(Path(str(anchor).swapcase()))
    assert same is (os.name == "nt")


def test_history_physical_fence_does_not_follow_both_final_components(history, capsys, monkeypatch):
    root, _, record_path, data, _, _ = history
    checkout = Path(data["worktree_path"])
    checkout.parent.mkdir(parents=True)
    target = root / "foreign-removed"
    target.mkdir()
    try:
        checkout.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"contained host cannot create directory link: {exc}")
    target.rmdir()
    is_symlink = Path.is_symlink
    # Model a directory redirect whose classification is not a symbolic link.
    monkeypatch.setattr(Path, "is_symlink", lambda path: False if path == checkout else is_symlink(path))
    before = record_path.read_bytes()
    entries = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    code, report = run(capsys, "--fix")
    assert code == 1 and "redirected namespace descendant" in report["error"]
    assert record_path.read_bytes() == before
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == entries


def test_busy_selected_record_refuses_before_receipt(history, capsys):
    _, _, path, _, _, _ = history
    ready, release = threading.Event(), threading.Event()

    def holder():
        with tracking._RecordLock(path, require_sidecar=True):
            ready.set()
            assert release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert ready.wait(5)
        code, report = run(capsys, "--fix")
        assert code == 1 and "busy" in report["error"]
        assert not list(path.parent.glob("*.json"))
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.parametrize("clone", [False, True, "readonly"])
def test_stale_current_code_writer_cannot_restore_old_label(history, capsys, clone):
    _, _, path, _, _, _ = history
    stale = tracking.load_record(path, copy_result=clone != "readonly")
    if clone:
        stale = replace(stale)
    assert run(capsys, "--fix")[0] == 0
    with pytest.raises(ValueError, match="owner machine changed"):
        tracking.save_record(stale, path)


@pytest.mark.parametrize("clone", [False, True, "readonly"])
def test_explicit_internal_update_from_a_current_snapshot_is_not_stale(history, clone):
    _, _, path, _, _, _ = history
    record = tracking.load_record(path, copy_result=clone != "readonly")
    if clone:
        record = replace(record)
    record.machine = NEW
    tracking.save_record(record, path)
    record.title = "Subsequent ordinary update"
    tracking.save_record(record, path)
    assert tracking.load_record(path).machine == NEW
    assert "_loaded_machine" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("owner", [f"{OLD}/{PROJECT}/missing-parent", f"foreign/{PROJECT}/missing-parent"])
def test_existing_unresolved_parent_does_not_gain_authority_or_block_label_repair(history, capsys, owner):
    _, config, path, data, _, _ = history
    path.write_text(yaml.safe_dump({**data, "owner_ref": owner}), encoding="utf-8")
    with pytest.raises(execution_spaces.ExecutionSpaceError) as before:
        execution_spaces.require_record_mutation(tracking.load_record(path), config)
    original = path.read_bytes()
    assert run(capsys, "--fix")[0] == 0
    assert path.read_bytes() == original.replace(b"machine: old-host", b'machine: "canonical-host"')
    with pytest.raises(execution_spaces.ExecutionSpaceError) as after:
        execution_spaces.require_record_mutation(tracking.load_record(path), config)
    assert str(before.value) == str(after.value)


def test_audit_failure_reports_committed_record_truthfully(history, capsys, monkeypatch):
    _, _, path, _, _, _ = history
    write = tracking._atomic_write
    calls = 0

    def failed_audit(target, content):
        nonlocal calls
        if target.suffix == ".json":
            calls += 1
            if calls == 2:
                raise OSError("synthetic final receipt failure")
        write(target, content)

    monkeypatch.setattr(tracking, "_atomic_write", failed_audit)
    code, report = run(capsys, "--fix")
    assert code == 1 and report["repaired"] and "final audit failed" in report["error"]
    assert yaml.safe_load(path.read_bytes())["machine"] == NEW
    assert (path.parent / report["backup"]).exists()


def test_committed_audit_uses_the_backup_returned_by_the_shared_writer(history, capsys, monkeypatch):
    path = history[2]
    apply_repair = repair.tracking_yaml_repair.apply_repair

    def differently_named_backup(*args):
        audit = next(path.parent.glob(f"{path.name}.machine-label-*.json"))
        assert json.loads(audit.read_text())["backup"] is None
        backup = apply_repair(*args)
        actual = backup.with_name("actual-original-bytes.bak")
        backup.rename(actual)
        return actual

    monkeypatch.setattr(repair.tracking_yaml_repair, "apply_repair", differently_named_backup)
    code, report = run(capsys, "--fix")
    assert code == 0 and report["backup"] == "actual-original-bytes.bak"
    receipt = json.loads((path.parent / report["audit"]).read_text())
    assert receipt["state"] == "committed" and receipt["backup"] == report["backup"]
    assert (path.parent / receipt["backup"]).exists()


def test_no_competing_migration_parser_surface():
    with pytest.raises(SystemExit):
        arguments("--migrate-owner", RECORD)


@pytest.mark.skipif(
    cfg.detect_platform() not in ("windows", "linux"),
    reason="canonical repair retains its native-only execution boundary",
)
def test_real_cli_removed_knowledge_record_without_project_adoption(tmp_path):
    home, anchor = tmp_path / "home", tmp_path / "knowledge"
    anchor.mkdir()
    git_ops.git("init", cwd=anchor)
    platform = cfg.detect_platform()
    previous = socket.gethostname()
    (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        NEW: {"hostname": previous, "execution_platform": platform},
    }}), encoding="utf-8")
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    (registry / "config.yaml").write_text(yaml.safe_dump({
        "machine": NEW, "platform": platform,
    }), encoding="utf-8")
    (registry / "repos.yaml").write_text(yaml.safe_dump({"repos": {
        "knowledge": {"class": "knowledge", platform: str(anchor)},
    }}), encoding="utf-8")
    directory = home / ".knowledge" / "worktrees"
    directory.mkdir(parents=True)
    path = directory / f"{RECORD}.yaml"
    data = {
        "worktree_id": RECORD, "worktree_path": str(Path(str(anchor) + ".worktrees") / RECORD),
        "repo": "knowledge", "machine": previous, "platform": platform,
        "status": "completed", "branch": f"worktree/{RECORD}",
        "pair_ref": f"{previous}/example/parent", "unknown_field": "preserved",
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = snapshot(home)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_WORKTREES_", "AGENT_HOME"))}
    env.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        AGENT_WORKTREES_STATUS_MONITOR="0",
        PYTHONPATH=str(Path(repair.__file__).resolve().parents[1]),
    )
    command = [
        sys.executable, "-m", "agent_worktrees", "-p", "knowledge", "doctor",
        "--repair-machine-record", RECORD, "--from-machine", previous,
        "--to-machine", NEW, "--json",
    ]
    for apply in (False, True):
        result = subprocess.run(
            [*command, *(["--fix"] if apply else [])], cwd=anchor, env=env,
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["proof"] == "removed-registered-namespace" and report["repaired"] is apply
        assert report["resident_projection"] == "not-verified"
        if not apply:
            assert snapshot(home) == before
    assert yaml.safe_load(path.read_bytes()) == {**data, "machine": NEW}
    assert not (home / ".knowledge" / "config.yaml").exists()
    assert not (registry / "projects.yaml").exists()
