"""Real Git/copy/record/permission consumers for the conservative first flow."""

from __future__ import annotations

import json
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as move_state
from agent_worktrees import config as cfg, git_ops, permissions, sessions, tracking
from agent_worktrees import retained_checkout, worktree_lifecycle


def _fixture_home(tmp_path, request):
    if "native_cross_volume" not in request.node.name or not os.environ.get(
        "AGENT_WORKTREES_RELOCATION_SECOND_ROOT",
    ):
        return tmp_path
    value = os.environ.get("AGENT_WORKTREES_RELOCATION_PRIMARY_ROOT")
    if not value:
        raise ValueError("cross-volume qualification requires an enrolled persistent primary root")
    parent = Path(value)
    if not parent.is_absolute() or not parent.is_dir() or parent.resolve() != parent.absolute():
        raise ValueError("persistent primary fixture root is unavailable or redirected")
    home = parent / ("primary-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12])
    home.mkdir(mode=0o700)
    (home / "scratch-owner.json").write_text(json.dumps({
        "owner": request.node.nodeid, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "native relocation primary Git, ledger and recovery custody",
        "retention": "caller-owned evidence; retain on failure, cleanup only after confirmed completion",
    }), encoding="utf-8")
    return home


@pytest.fixture
def checkout(tmp_path, monkeypatch, request):
    from checkout_fixture_process import scope_fixture

    if os.name != "nt":
        pytest.skip("v1 relocation flow requires qualified native Windows; POSIX copy remains gated")
    scope_fixture(tmp_path, monkeypatch)
    tmp_path = _fixture_home(tmp_path, request)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: tmp_path / ".copilot")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path / ".copilot" / "session-state")
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    git_ops.git("init", "--template=", "-b", "main", cwd=anchor)
    (anchor / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
    (anchor / "tracked.txt").write_bytes(b"original\0content\n")
    git_ops.git("add", ".", cwd=anchor)
    git_ops.git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "fixture", cwd=anchor,
    )
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=anchor)
    git_ops.git("remote", "add", "origin", str(anchor), cwd=anchor)
    root = tmp_path / "old-root"
    target_root = tmp_path / "new-root"
    root.mkdir(mode=0o700)
    target_root.mkdir(mode=0o700)
    identity = "fixture-win-20261105-100000-abcd"
    source = root / identity
    git_ops.create_worktree(str(anchor), str(source), f"worktree/{identity}", "HEAD")
    (source / "ignored.bin").write_bytes(bytes(range(256)))
    platform = cfg.detect_platform()
    config = cfg.Config(
        srcroot=str(tmp_path), repo_name="sample", machine=cfg.detect_machine(), platform=platform,
        repos={"sample": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(root), default_branch="main", remote="origin",
        )},
    )
    project = cfg.project_dir("sample")
    project.mkdir()
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "sample", "machine": config.machine, "srcroot": str(tmp_path),
        "repos": {"sample": {
            "anchor": str(anchor), "worktree_root": str(root), "remote": "origin",
            "default_branch": "main",
        }},
    }), encoding="utf-8")
    record = tracking.create_new_record(
        identity, f"worktree/{identity}", str(source), "sample", config.machine,
        platform, cfg.tracking_dir("sample"),
    )
    record.status = "completed"
    record.sessions = []
    tracking.save_record(record)
    copilot = tmp_path / ".copilot"
    copilot.mkdir()
    (copilot / "config.json").write_text(
        json.dumps({"trustedFolders": [str(source), "unrelated"]}), encoding="utf-8",
    )
    (copilot / "permissions-config.json").write_text(
        json.dumps({"locations": {str(source): {"tool_approvals": []}, "unrelated": {"retained": True}}}),
        encoding="utf-8",
    )
    return SimpleNamespace(
        record=record, config=config, source=source, destination=target_root / identity,
        anchor=anchor, home=tmp_path, copilot=copilot,
    )


def _plan(fixture):
    """Exercise strict mutation preflight; pure CLI refusal is qualified separately."""
    return relocation._action_plan(fixture.record.worktree_id, str(fixture.destination), fixture.config)


def _move(fixture):
    return relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)


def test_complete_native_move_preserves_bytes_git_identity_and_operational_permissions(checkout):
    fixture = checkout
    before_record = fixture.record.yaml_path.read_bytes()
    before_git = (fixture.source / ".git").read_bytes()
    plan = _plan(fixture)
    assert plan["phase"] == "planned" and not plan["apply"]
    assert fixture.record.yaml_path.read_bytes() == before_record
    assert not move_state.journal_path(fixture.record.yaml_path).exists()
    assert not fixture.destination.exists()
    assert (fixture.source / ".git").read_bytes() == before_git
    result = _move(fixture)
    assert result["phase"] == "complete"
    assert not fixture.source.exists()
    assert (fixture.destination / "ignored.bin").read_bytes() == bytes(range(256))
    assert (fixture.destination / "tracked.txt").read_bytes() == b"original\0content\n"
    current = tracking.load_record(fixture.record.yaml_path)
    assert current.worktree_path == str(fixture.destination)
    assert current.creation_nonce == fixture.record.creation_nonce
    assert current.worktree_id == fixture.record.worktree_id
    assert retained_checkout.matches(current, fixture.config)
    assert not git_ops.git("status", "--porcelain", cwd=fixture.destination).stdout
    trust = json.loads((fixture.copilot / "config.json").read_text())
    grants = json.loads((fixture.copilot / "permissions-config.json").read_text())
    assert trust["trustedFolders"] == [str(fixture.destination), "unrelated"]
    assert grants["locations"] == {
        str(fixture.destination): {"tool_approvals": []}, "unrelated": {"retained": True},
    }
    assert _move(fixture)["phase"] == "complete"
    with pytest.raises(ValueError, match="stale checkout location"):
        tracking.save_record(fixture.record)


def test_generic_owned_ordinary_component_move_preserves_original_parent_responsibility(checkout, monkeypatch):
    fixture = checkout
    stopped = relocation._stopped

    def fixture_admission(record, path):
        allowed = {fixture.source, fixture.destination}
        journal = move_state.read(fixture.record.yaml_path)
        if (
            journal and journal["source"] == str(fixture.source)
            and journal["destination"] == str(fixture.destination) and journal.get("stage")
        ):
            allowed.add(fixture.source.with_name(
                fixture.source.name + ".retiring-" + Path(journal["stage"]).name.rsplit("-", 1)[1],
            ))
        if (
            record.yaml_path == fixture.record.yaml_path
            and record.creation_nonce == fixture.record.creation_nonce
            and Path(path) in allowed
        ):
            return
        stopped(record, path)

    # This component control qualifies the new owned fixture, not host absence.
    monkeypatch.setattr(relocation, "_stopped", fixture_admission)
    records = fixture.record.yaml_path.parent
    parent_checkout = fixture.source.parent / "original-creator"
    git_ops.create_worktree(
        str(fixture.anchor), str(parent_checkout), "worktree/original-creator", "HEAD",
    )
    parent = tracking.create_new_record(
        "original-creator", "worktree/original-creator", str(parent_checkout),
        "sample", fixture.record.machine, fixture.record.platform, records,
    )
    child_ref = tracking.format_claim_ref(
        fixture.record.execution_machine, fixture.record.repo, fixture.record.worktree_id,
    )
    parent.resources = [tracking.ResourceClaim(kind="worktree", ref=child_ref, state="at-rest")]
    tracking.save_record(parent)
    parent_path = parent.yaml_path
    fixture.record.owner_ref = tracking.format_claim_ref(
        fixture.record.execution_machine, fixture.record.repo, "original-creator",
    )
    tracking.save_record(fixture.record)
    parent_before = parent_path.read_bytes()
    journal = records.parent / "workspace-creation.sqlite3"
    connection = sqlite3.connect(journal)
    connection.execute("CREATE TABLE creations(key_digest TEXT,version INTEGER,facts TEXT,status TEXT,worktree TEXT)")
    connection.execute("INSERT INTO creations VALUES(?,?,?,?,?)", (
        "unrelated", 1, "{}", "terminal", json.dumps({"worktree": {"id": "unrelated"}}),
    ))
    connection.commit()
    connection.close()
    journal_before = journal.read_bytes()
    journal.chmod(0o444)
    assert _plan(fixture)["phase"] == "planned"
    assert _move(fixture)["phase"] == "complete"
    current = tracking.load_record(fixture.record.yaml_path)
    assert current.owner_ref == fixture.record.owner_ref
    assert current.creation_nonce == fixture.record.creation_nonce
    assert current.worktree_path == str(fixture.destination)
    assert not current.receiving_allocation_present
    assert parent_path.read_bytes() == parent_before
    assert journal.read_bytes() == journal_before
    assert (fixture.destination / "tracked.txt").read_bytes() == b"original\0content\n"


@pytest.mark.parametrize("readonly", [False, True])
def test_copy_interruption_fences_launch_and_retries_original_operation(checkout, monkeypatch, readonly):
    if readonly and os.name == "nt":
        pytest.skip("POSIX preserved read-only-file recovery")
    if readonly:
        (checkout.source / "tracked.txt").chmod(0o444)
    copy = relocation.transfer.copy
    def interrupted(source, stage):
        copy(source, stage)
        raise OSError("injected post-copy interruption")
    monkeypatch.setattr(relocation.transfer, "copy", interrupted)
    with pytest.raises(OSError, match="injected"):
        _move(checkout)
    assert checkout.source.exists() and not checkout.destination.exists()
    journal = move_state.read(checkout.record.yaml_path)
    assert journal["phase"] == "copying"
    with pytest.raises(TimeoutError, match="unfinished"), worktree_lifecycle.guard(checkout.record.yaml_path):
        pytest.fail("an unfinished move must fence a subsequent launch")
    with pytest.raises(ValueError, match="unfinished"):
        tracking.save_record(tracking.load_record(checkout.record.yaml_path))
    from agent_worktrees import execution_spaces

    with pytest.raises(execution_spaces.ExecutionSpaceError, match="unfinished"):
        execution_spaces.require_record_mutation(
            tracking.load_record(checkout.record.yaml_path), checkout.config,
        )
    monkeypatch.setattr(relocation.transfer, "copy", copy)
    assert _move(checkout)["phase"] == "complete"


def test_new_responsibility_during_planning_refuses_before_custody(checkout, monkeypatch):
    from contextvars import Context

    capture = relocation.permission_paths.capture
    def concurrent_capture(*args):
        result = capture(*args)
        def claim():
            record = tracking.load_record(checkout.record.yaml_path)
            record.status = "active"
            record.resources = [tracking.ResourceClaim(kind="worktree", ref="new-responsibility", state="active")]
            tracking.save_record(record)
        Context().run(claim)
        return result
    monkeypatch.setattr(relocation.permission_paths, "capture", concurrent_capture)
    with pytest.raises(ValueError, match="changed before admission"):
        _move(checkout)
    current = tracking.load_record(checkout.record.yaml_path)
    assert current.status == "active" and current.resources[0].ref == "new-responsibility"
    assert checkout.source.exists() and not checkout.destination.exists()
    assert not move_state.journal_path(current.yaml_path).exists()


def test_readonly_permission_metadata_refuses_without_custody(checkout):
    path = checkout.copilot / "config.json"
    path.chmod(0o444)
    try:
        with pytest.raises(ValueError, match="metadata"):
            _plan(checkout)
        assert not move_state.journal_path(checkout.record.yaml_path).exists()
        assert checkout.source.exists() and not checkout.destination.exists()
    finally:
        path.chmod(0o600)


def test_unrelated_readonly_permission_file_is_not_rewritten_or_opened_for_write(checkout):
    path = checkout.copilot / "config.json"
    original = b'// unrelated settings\n{"trustedFolders":["unrelated"],"preserved":true}\n'
    path.write_bytes(original)
    path.chmod(0o444)
    try:
        assert _move(checkout)["phase"] == "complete"
        assert path.read_bytes() == original
        assert not path.stat().st_mode & 0o200
    finally:
        path.chmod(0o600)


@pytest.mark.skipif(os.name == "nt", reason="POSIX source-directory admission")
@pytest.mark.parametrize("location", ["checkout", "parent"])
def test_readonly_source_directories_refuse_before_custody(checkout, location):
    path = checkout.source if location == "checkout" else checkout.source.parent
    path.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="writable source"):
            _plan(checkout)
        assert not move_state.journal_path(checkout.record.yaml_path).exists()
    finally:
        path.chmod(0o700)


@pytest.mark.skipif(os.name != "nt", reason="Windows effective mutation-access proof")
@pytest.mark.parametrize("location", ["source-file", "destination-directory", "source-parent"])
def test_required_mutation_access_refuses_before_custody(checkout, location):
    import ctypes
    from ctypes import wintypes
    from agent_procutil import _daemon_instance_owner as owner

    api, kernel = owner._apis()
    user = owner._current_sids(os.getpid())[0]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    api.SetFileSecurityW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    path = (
        checkout.source / "tracked.txt" if location == "source-file"
        else checkout.source.parent if location == "source-parent" else checkout.destination.parent
    )
    denied = "0x10002" if location == "source-file" else "0x4" if location == "source-parent" else "0x2"
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P(D;;{denied};;;{user})(A;;FA;;;{user})", 1, ctypes.byref(descriptor), None,
    )
    try:
        assert api.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor)
    finally:
        kernel.LocalFree(descriptor)
    with pytest.raises(ValueError, match="delete/write"):
        _plan(checkout)
    assert not move_state.journal_path(checkout.record.yaml_path).exists()


def test_prepublication_abort_preserves_new_source_edits_and_releases_only_move_custody(checkout, monkeypatch):
    copy = relocation.transfer.copy
    def interrupted(source, stage):
        copy(source, stage)
        raise OSError("injected before publication")
    monkeypatch.setattr(relocation.transfer, "copy", interrupted)
    with pytest.raises(OSError, match="injected"):
        _move(checkout)
    stage = Path(move_state.read(checkout.record.yaml_path)["stage"])
    (checkout.source / "tracked.txt").write_bytes(b"new source work must survive")
    cancelled = relocation.abort(
        checkout.record.worktree_id, str(checkout.destination), checkout.config,
    )
    assert cancelled["phase"] == "cancelled" and not stage.exists()
    assert (checkout.source / "tracked.txt").read_bytes() == b"new source work must survive"
    assert tracking.load_record(checkout.record.yaml_path).worktree_path == str(checkout.source)
    with worktree_lifecycle.guard(checkout.record.yaml_path):
        pass


def test_second_move_preserves_the_completed_receipt_and_restores_original_location(checkout):
    source = checkout.source
    assert _move(checkout)["phase"] == "complete"
    original_receipt = move_state.journal_path(checkout.record.yaml_path).read_bytes()
    checkout.destination = source
    assert _move(checkout)["phase"] == "complete"
    current = tracking.load_record(checkout.record.yaml_path)
    assert current.worktree_path == str(source)
    assert current.creation_nonce == checkout.record.creation_nonce
    history = list((current.yaml_path.parent / "checkout-relocations" / current.worktree_id).glob("*.json"))
    assert len(history) == 1 and history[0].read_bytes() == original_receipt
    assert (source / "ignored.bin").read_bytes() == bytes(range(256))


def test_archiving_never_removes_the_current_stale_location_fence(checkout, monkeypatch):
    import hashlib

    assert _move(checkout)["phase"] == "complete"
    journal = move_state.journal_path(checkout.record.yaml_path)
    original = journal.read_bytes()
    synchronized = []
    synchronize = relocation.durability.directory
    def observe(path):
        synchronized.append(path)
        synchronize(path)
    monkeypatch.setattr(relocation.durability, "directory", observe)
    move_state.archive_terminal(checkout.record.yaml_path, hashlib.sha256(original).hexdigest())
    assert journal.read_bytes() == original
    assert checkout.record.yaml_path.parent / "checkout-relocations" in synchronized
    assert checkout.record.yaml_path.parent in synchronized
    with pytest.raises(ValueError, match="stale checkout location"):
        tracking.save_record(checkout.record)


def test_publication_never_replaces_an_occupied_empty_destination(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "preserved").write_bytes(b"owned stage")
    with pytest.raises(OSError):
        relocation.durability.replace(source, target, existing=False)
    assert source.is_dir() and target.is_dir()
    assert (source / "preserved").read_bytes() == b"owned stage"
    assert not list(target.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only descriptor durability")
def test_durability_preserves_a_read_only_posix_file(tmp_path):
    path = tmp_path / "preserved"
    path.write_bytes(b"ordinary read-only data")
    path.chmod(0o444)
    try:
        relocation.durability.files([path])
        assert path.stat().st_mode & 0o777 == 0o444
        assert path.read_bytes() == b"ordinary read-only data"
    finally:
        path.chmod(0o600)


def test_old_published_writer_refuses_move_without_activation_or_data_effects(checkout, monkeypatch):
    from agent_worktrees import status_monitor_runtime

    legacy = checkout.home / "legacy-resident.json"
    legacy.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(status_monitor_runtime, "_monitor_lock_path", lambda: legacy)
    before = checkout.record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="published resident writer"):
        _plan(checkout)
    assert checkout.record.yaml_path.read_bytes() == before
    assert checkout.source.exists() and not checkout.destination.exists()
    assert not move_state.journal_path(checkout.record.yaml_path).exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows ACE semantics")
def test_destination_access_keeps_inherit_only_semantics(tmp_path):
    import ctypes
    from ctypes import wintypes
    from agent_procutil import _daemon_instance_owner as owner

    api, kernel = owner._apis()
    user = owner._current_sids(os.getpid())[0]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    api.SetFileSecurityW.restype = wintypes.BOOL
    directories = []
    for flags in ("OICIIO", "OICI"):
        path = tmp_path / flags
        path.mkdir()
        descriptor = ctypes.c_void_p()
        sddl = f"D:P(A;;FA;;;{user})(A;{flags};FR;;;{user})"
        assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None,
        )
        try:
            assert api.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor)
        finally:
            kernel.LocalFree(descriptor)
        directories.append(path)
    assert relocation.transfer._access(directories[0]) != relocation.transfer._access(directories[1])


@pytest.mark.parametrize("blocker", ["dirty", "session", "unknown", "open-pr", "claim", "submodule", "git-lock", "foreign-machine", "source-alias", "source-parent-alias", "source-record-alias", "source-basename", "seed"])
def test_refusals_leave_original_and_no_journal(checkout, monkeypatch, blocker):
    record = checkout.record
    if blocker == "dirty":
        (checkout.source / "tracked.txt").write_bytes(b"dirty")
    elif blocker == "session":
        monkeypatch.setattr(sessions, "verify_worktree_active", lambda _, **kw: sessions.LiveVerdict(active=True))
    elif blocker == "unknown":
        monkeypatch.setattr(sessions, "verify_worktree_active", lambda _, **kw: sessions.LiveVerdict(probes_ok=False))
    elif blocker == "open-pr":
        record.prs = [tracking.PRRecord(state="open")]
        tracking.save_record(record)
    elif blocker == "claim":
        record.resources = [tracking.ResourceClaim(kind="worktree", ref="held", state="active")]
        tracking.save_record(record)
    elif blocker == "submodule":
        (checkout.source / ".gitmodules").write_text("opaque", encoding="utf-8")
    elif blocker == "git-lock":
        git_ops.git("worktree", "lock", str(checkout.source), cwd=checkout.anchor)
    elif blocker == "foreign-machine":
        record.machine = "another-host"
        tracking.save_record(record)
    elif blocker == "source-alias":
        config_path = checkout.copilot / "config.json"
        alias = str(checkout.source).replace("\\", "/").lower() if os.name == "nt" else str(checkout.source) + "/."
        config_path.write_text(json.dumps({"trustedFolders": [alias]}))
    elif blocker == "source-parent-alias":
        config_path = checkout.copilot / "config.json"
        alias = checkout.source.parent / "sibling" / ".." / checkout.source.name
        config_path.write_text(json.dumps({"trustedFolders": [str(alias)]}))
    elif blocker == "source-record-alias":
        record.worktree_path = str(checkout.source.parent / "sibling" / ".." / checkout.source.name)
        tracking.save_record(record)
    elif blocker == "source-basename":
        renamed = checkout.source.with_name("another-basename")
        git_ops.git("worktree", "move", str(checkout.source), str(renamed), cwd=checkout.anchor)
        checkout.source = renamed
        record.worktree_path = str(renamed)
        tracking.save_record(record)
    elif blocker == "seed":
        from agent_worktrees import launch_seed_state

        launch_seed_state.stage(record.yaml_path, kind="new", text="fixture-only intent")
        record.yaml_path.with_suffix(".lock").unlink(missing_ok=True)
    with pytest.raises((ValueError, RuntimeError)):
        _plan(checkout)
    assert checkout.source.exists() and not checkout.destination.exists()
    assert not move_state.journal_path(record.yaml_path).exists()
    if blocker == "seed":
        assert not record.yaml_path.with_suffix(".lock").exists()


@pytest.mark.parametrize("marker_case", [".git", ".GIT"])
def test_ignored_nested_linked_worktree_refuses_without_changing_either_registration(checkout, marker_case):
    if marker_case != ".git" and os.name != "nt":
        pytest.skip("native Windows Git path case equivalence")
    nested = checkout.source / "nested"
    (checkout.anchor / ".git" / "info").mkdir(exist_ok=True)
    (checkout.anchor / ".git" / "info" / "exclude").write_text("nested/\n")
    git_ops.create_worktree(str(checkout.anchor), str(nested), "worktree/nested", "HEAD")
    if marker_case != ".git":
        (nested / ".git").rename(nested / marker_case)
    before = git_ops.git("worktree", "list", "--porcelain", cwd=checkout.anchor).stdout
    with pytest.raises(ValueError, match="nested Git"):
        _plan(checkout)
    assert git_ops.git("worktree", "list", "--porcelain", cwd=checkout.anchor).stdout == before
    assert nested.is_dir() and not move_state.journal_path(checkout.record.yaml_path).exists()


@pytest.mark.parametrize("state", ["open", "pending-transfer"])
def test_itemized_followup_refuses_without_legacy_boolean(checkout, state):
    record = checkout.record
    record.follow_ups = [tracking.FollowUpRecord(id="item", summary="remaining work", state=state)]
    tracking.save_record(record)
    assert not tracking.load_record(record.yaml_path).follow_up
    with pytest.raises(ValueError, match="completed ordinary"):
        _plan(checkout)
    assert not move_state.journal_path(record.yaml_path).exists()


@pytest.mark.parametrize("kind", ["trust", "permissions"])
@pytest.mark.parametrize("phase", ["capture", "apply"])
def test_nonobject_permission_schema_is_a_controlled_refusal(checkout, kind, phase):
    paths = relocation.permission_paths
    captured = paths.capture(str(checkout.source), str(checkout.destination)) if phase == "apply" else None
    path = checkout.copilot / ("config.json" if kind == "trust" else "permissions-config.json")
    path.write_text("[]" if kind == "trust" else "null")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="permission object schema"):
        if phase == "capture":
            paths.capture(str(checkout.source), str(checkout.destination))
        else:
            paths.apply(str(checkout.source), str(checkout.destination), captured)
    assert path.read_bytes() == before
    assert not move_state.journal_path(checkout.record.yaml_path).exists()


def test_invalid_capability_effect_combination_uses_existing_error_output(capsys):
    from agent_worktrees import worktree_ops_cli

    args = SimpleNamespace(
        capabilities=True, worktree_id=None, destination=None, apply=True, abort=False,
    )
    assert worktree_ops_cli.cmd_move(args) == 1
    assert "accepts no target or effects" in capsys.readouterr().out


@pytest.mark.timeout(120)
def test_malformed_workspace_yaml_returns_structured_cli_refusal(checkout):
    directory = checkout.copilot / "session-state" / "unrelated"
    directory.mkdir(parents=True)
    (directory / "workspace.yaml").write_text("cwd: [\n")
    env = dict(os.environ, AGENT_HOME=str(checkout.home), USERPROFILE=str(checkout.home), HOME=str(checkout.home))
    result = run_caller_owned(
        [sys.executable, "-m", "agent_worktrees", "--project", "sample", "move",
         checkout.record.worktree_id, "--to", str(checkout.destination), "--json", "--apply"],
        env=env, cwd=checkout.home, timeout=60,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 1
    assert "malformed YAML" in json.loads(result.stdout)["error"]
    assert "Traceback" not in result.stderr
    assert not move_state.journal_path(checkout.record.yaml_path).exists()


@pytest.mark.parametrize("content", ["[]", "worktree_id: ["])
@pytest.mark.parametrize("pending", [False, True])
def test_malformed_ledger_returns_structured_cli_refusal(checkout, monkeypatch, content, pending):
    journal = move_state.journal_path(checkout.record.yaml_path)
    if pending:
        def interrupted(*args):
            raise OSError("injected copy interruption")
        with monkeypatch.context() as injection:
            injection.setattr(relocation.transfer, "copy", interrupted)
            with pytest.raises(OSError, match="injected"):
                _move(checkout)
    before = journal.read_bytes() if journal.exists() else None
    checkout.record.yaml_path.write_text(content)
    env = dict(os.environ, AGENT_HOME=str(checkout.home), USERPROFILE=str(checkout.home), HOME=str(checkout.home))
    result = run_caller_owned(
        [sys.executable, "-m", "agent_worktrees", "--project", "sample", "move",
         checkout.record.worktree_id, "--to", str(checkout.destination), "--json", *(["--apply"] if pending else [])],
        env=env, cwd=checkout.home, timeout=60,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["error"] and report["complete"] is False
    assert "Traceback" not in result.stderr
    assert (journal.read_bytes() if journal.exists() else None) == before
    assert checkout.record.yaml_path.read_text() == content
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.parametrize("binding", ["root", "directory", "file"])
def test_mount_inventory_refuses_same_device_bindings_with_escaped_paths(tmp_path, monkeypatch, binding):
    root = tmp_path / "checkout with space"
    root.mkdir()
    nested = root if binding == "root" else root / binding
    if binding == "directory":
        nested.mkdir()
    elif binding == "file":
        nested.write_bytes(b"preserved")
    assert nested.stat().st_dev == root.stat().st_dev
    mount = nested.as_posix().replace(" ", r"\040")
    raw = f"1 0 0:1 / {root.anchor} rw - rootfs rootfs rw\n2 1 0:1 /backing {mount} rw - rootfs rootfs rw\n".encode()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.BytesIO(raw))
    with pytest.raises(ValueError, match="mounted checkout"):
        relocation.transfer._require_no_mounts(root)


def test_mount_inventory_failure_and_unrelated_mounts(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    raw = f"1 0 0:1 / {root.anchor} rw - rootfs rootfs rw\n".encode()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.BytesIO(raw))
    relocation.transfer._require_no_mounts(root)
    raw = b"malformed\n"
    with pytest.raises(ValueError, match="malformed"):
        relocation.transfer._require_no_mounts(root)
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("native table unavailable")
    monkeypatch.setattr(Path, "open", unavailable)
    with pytest.raises(ValueError, match="readable native mount inventory"):
        relocation.transfer._require_no_mounts(root)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux private mount-namespace qualification")
def test_native_bind_mount_refuses_without_touching_backing_data(tmp_path, monkeypatch):
    from checkout_fixture_process import scope_fixture

    scope_fixture(tmp_path, monkeypatch)
    unshare, mount, umount = (shutil.which(name) for name in ("unshare", "mount", "umount"))
    if not all((unshare, mount, umount)):
        pytest.skip("native private mount-namespace tools are unavailable")
    root, backing = tmp_path / "checkout", tmp_path / "backing"
    root.mkdir()
    backing.mkdir()
    (root / "ignored").mkdir()
    (backing / "keep").write_bytes(b"outside checkout")
    script = """
import subprocess, sys
from pathlib import Path
from agent_worktrees.checkout_transfer import manifest
root, backing, mount, umount = sys.argv[1:]
root, backing = Path(root), Path(backing)
binding = root / "ignored"
subprocess.run([mount, "--bind", str(backing), str(binding)], check=True)
try:
    assert binding.stat().st_dev == root.stat().st_dev
    try:
        manifest(root)
    except ValueError as exc:
        assert "mounted checkout" in str(exc)
    else:
        raise AssertionError("same-device bind mount was admitted")
    assert (backing / "keep").read_bytes() == b"outside checkout"
finally:
    subprocess.run([umount, str(binding)], check=True)
"""
    result = run_caller_owned(
        [unshare, "--user", "--map-root-user", "--mount", "--propagation", "private", "--fork",
         sys.executable, "-c", script, str(root), str(backing), mount, umount],
        cwd=tmp_path, timeout=20, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    if result.returncode and "unshare:" in result.stderr and "Operation not permitted" in result.stderr:
        pytest.skip("kernel policy does not admit an unprivileged private mount namespace")
    assert result.returncode == 0, result.stderr
    assert (backing / "keep").read_bytes() == b"outside checkout"
    assert not list((root / "ignored").iterdir())


@pytest.mark.parametrize("phase", ["git-repair", "permissions", "ledger", "retirement", "durability"])
def test_effect_interruption_retries_without_losing_source_or_identity(checkout, monkeypatch, phase):
    fixture = checkout
    with monkeypatch.context() as injection:
        if phase == "git-repair":
            original = relocation._git
            def failing(path, *args):
                result = original(path, *args)
                if args[:2] == ("worktree", "repair"):
                    raise OSError("injected repair checkpoint loss")
                return result
            injection.setattr(relocation, "_git", failing)
        elif phase == "permissions":
            original = relocation.permission_paths.apply
            def failing(*args):
                original(*args)
                raise OSError("injected permission checkpoint loss")
            injection.setattr(relocation.permission_paths, "apply", failing)
        elif phase == "ledger":
            original = tracking.save_record
            def failing(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("injected ledger checkpoint loss")
            injection.setattr(tracking, "save_record", failing)
        elif phase == "retirement":
            original = relocation.shutil.rmtree
            def failing(path, *args, **kwargs):
                if Path(path).name.startswith(fixture.source.name + ".retiring-"):
                    (Path(path) / "ignored.bin").unlink()
                    raise OSError("injected partial source retirement")
                return original(path, *args, **kwargs)
            injection.setattr(relocation.shutil, "rmtree", failing)
        else:
            original = relocation.durability.files
            def failing(paths):
                selected = list(paths)
                if fixture.record.yaml_path in selected:
                    raise OSError("injected durability barrier failure")
                return original(selected)
            injection.setattr(relocation.durability, "files", failing)
        with pytest.raises(OSError, match="injected"):
            _move(fixture)
    retirement = fixture.source.with_name(
        fixture.source.name + ".retiring-" + Path(move_state.read(fixture.record.yaml_path)["stage"]).name.rsplit("-", 1)[1],
    )
    if phase == "retirement":
        assert not fixture.source.exists() and retirement.exists()
    else:
        assert fixture.source.exists()
    assert fixture.destination.exists()
    assert (fixture.destination / "ignored.bin").read_bytes() == bytes(range(256))
    assert _move(fixture)["phase"] == "complete"
    assert not fixture.source.exists()
    assert not retirement.exists()
    assert tracking.load_record(fixture.record.yaml_path).creation_nonce == fixture.record.creation_nonce


def test_fresh_process_recovery_accepts_the_exited_original_caller(checkout):
    env = dict(os.environ, AGENT_HOME=str(checkout.home), USERPROFILE=str(checkout.home), HOME=str(checkout.home))
    script = (
        "import sys; from agent_worktrees import config as cfg,checkout_relocation as move; "
        "cfg.set_active_project('sample'); real=move.transfer.copy\n"
        "def fail(source,stage):\n"
        " real(source,stage)\n"
        " raise OSError('injected child exit')\n"
        "move.transfer.copy=fail\n"
        "try: move.move(sys.argv[1],sys.argv[2],cfg.load_config())\n"
        "except OSError: sys.exit(7)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script, checkout.record.worktree_id, str(checkout.destination)],
        env=env, cwd=checkout.home, text=True, capture_output=True, timeout=60, **no_window_kwargs(),
    )
    assert child.returncode == 7, (child.stdout, child.stderr)
    assert _move(checkout)["phase"] == "complete"


def test_changed_allocation_root_retains_real_git_and_ledger_authority(checkout):
    from agent_worktrees import legacy_wsl_record_locality, pr_source_publication

    fixture = checkout
    fixture.config = replace(fixture.config, repos={
        "sample": replace(fixture.config.default_repo, worktree_root=str(fixture.home / "future-root")),
    })
    assert legacy_wsl_record_locality.record_namespace_matches(fixture.record, fixture.config)
    fixture.record.status = "active"
    tracking.save_record(fixture.record)
    caller = pr_source_publication._caller(fixture.record.worktree_id, fixture.config)
    assert caller.worktree_path == str(fixture.source)
    altered = tracking.load_record(fixture.record.yaml_path)
    altered.worktree_path = str(fixture.destination)
    assert not retained_checkout.matches(altered, fixture.config)
    git_ops.git("symbolic-ref", "HEAD", "refs/heads/another", cwd=fixture.source)
    assert not retained_checkout.matches(fixture.record, fixture.config)


@pytest.mark.timeout(120)
def test_actual_cli_parser_process_plan_move_and_post_move_consumer(checkout):
    env = dict(os.environ, AGENT_HOME=str(checkout.home), USERPROFILE=str(checkout.home), HOME=str(checkout.home))
    command = [sys.executable, "-m", "agent_worktrees", "--project", "sample"]
    def invoke(*args):
        result = run_caller_owned(
            [*command, *args], env=env, cwd=checkout.home, timeout=60,
            max_stdout_bytes=1024 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)
    capability = invoke("get", "checkout-relocation")
    assert capability["schema"] == relocation.SCHEMA
    settings = checkout.home / ".sample" / "config.yaml"
    configured = json.loads(settings.read_text(encoding="utf-8"))
    future = checkout.home / "future-allocation-root"
    configured["repos"]["sample"]["worktree_root"] = str(future)
    settings.write_text(json.dumps(configured), encoding="utf-8")
    allocated = invoke("create", "--no-owner", "--no-pair", "--json")
    assert Path(allocated["worktree"]["path"]).parent == future
    assert checkout.record.yaml_path.exists() and checkout.source.exists()
    before = checkout.record.yaml_path.read_bytes()
    report = invoke("move", checkout.record.worktree_id, "--to", str(checkout.destination), "--json")
    assert report["phase"] == "planned" and not report["apply"]
    assert checkout.record.yaml_path.read_bytes() == before
    assert not move_state.journal_path(checkout.record.yaml_path).exists()
    report = invoke("move", checkout.record.worktree_id, "--to", str(checkout.destination), "--apply", "--json")
    assert report["phase"] == "complete"
    assert tracking.load_record(checkout.record.yaml_path).worktree_path == str(checkout.destination)
    assert retained_checkout.matches(tracking.load_record(checkout.record.yaml_path), checkout.config)


def test_read_only_plan_never_recovers_receipts_or_creates_owner_sidecars(checkout, monkeypatch):
    from agent_worktrees import execution_spaces, receiving_allocation

    record = checkout.record
    record.owner_ref = "creator/sample/parent"
    tracking.save_record(record)
    def forbidden(*args, **kwargs):
        pytest.fail("read-only relocation must not recover receiving metadata")
    monkeypatch.setattr(receiving_allocation, "recover_missing", forbidden)
    before = {
        str(path.relative_to(checkout.home)): path.read_bytes()
        for path in cfg.tracking_dir("sample").rglob("*") if path.is_file()
    }
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="receiving allocation inspection is unproven"):
        _plan(checkout)
    after = {
        str(path.relative_to(checkout.home)): path.read_bytes()
        for path in cfg.tracking_dir("sample").rglob("*") if path.is_file()
    }
    assert before == after


def test_original_creation_receipt_replays_after_only_the_allocation_default_changes(checkout):
    from agent_worktrees import creation_replay, creation_requests

    record, config = checkout.record, checkout.config
    worktree = {
        "id": record.worktree_id, "repo": record.repo, "path": record.worktree_path,
        "branch": record.branch, "machine": record.machine, "platform": record.platform,
    }
    facts = {
        "project": "sample", "anchor": str(checkout.anchor),
        "worktree_root": creation_requests._canonical_path(config.default_repo.worktree_root),
    }
    def allocate(reserve):
        reserve(worktree)
        return {"worktree": worktree}
    def validate(retained, receipt):
        creation_replay.require_completed_record(retained, config, receipt)
    creation_requests.create(config, "original-operation", facts, allocate, validate_replay=validate)
    before = creation_requests.inspect(config, "original-operation")
    config = replace(config, repos={
        "sample": replace(config.default_repo, worktree_root=str(checkout.home / "future")),
    })
    changed = {**facts, "worktree_root": creation_requests._canonical_path(config.default_repo.worktree_root)}
    def forbidden(*args):
        pytest.fail("changed defaults cannot create a replacement for the original operation")
    replay = creation_requests.create(
        config, "original-operation", changed, forbidden, validate_replay=validate,
    )
    assert replay["creation"]["replayed"] and replay["worktree"] == worktree
    assert creation_requests.inspect(config, "original-operation") == before
    with pytest.raises(creation_requests.CreationRequestFailure, match="different allocation facts"):
        creation_requests.create(
            config, "original-operation", {**changed, "anchor": "another-anchor"},
            forbidden, validate_replay=validate,
        )


def test_unrelated_live_session_database_is_not_rewritten(checkout):
    database = checkout.copilot / "session-store.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sessions (cwd TEXT)")
        connection.execute("CREATE TABLE session_files (file_path TEXT)")
        connection.execute("INSERT INTO sessions VALUES ('unrelated-workspace')")
        connection.commit()
        assert _move(checkout)["phase"] == "complete"
        assert connection.execute("SELECT cwd FROM sessions").fetchall() == [("unrelated-workspace",)]
        assert connection.execute("SELECT file_path FROM session_files").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("alias", [False, True])
def test_retained_session_database_reference_refuses_before_mutation(checkout, alias):
    database = checkout.copilot / "session-store.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE sessions (cwd TEXT)")
        connection.execute("CREATE TABLE session_files (file_path TEXT)")
        value = checkout.source.parent / "sibling" / ".." / checkout.source.name if alias else checkout.source
        connection.execute("INSERT INTO sessions VALUES (?)", (str(value),))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match="retained Copilot database"):
        _plan(checkout)
    assert not move_state.journal_path(checkout.record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


def test_workspace_parent_alias_refuses_before_mutation(checkout):
    directory = checkout.copilot / "session-state" / "retained"
    directory.mkdir(parents=True)
    alias = checkout.source.parent / "sibling" / ".." / checkout.source.name
    (directory / "workspace.yaml").write_text(json.dumps({"cwd": str(alias)}))
    with pytest.raises(ValueError, match="retained Copilot workspaces"):
        _plan(checkout)
    assert not move_state.journal_path(checkout.record.yaml_path).exists()


@pytest.mark.parametrize("kind", ["trust", "permissions"])
def test_capture_modify_apply_refuses_new_nested_permission_bindings(checkout, kind):
    paths = relocation.permission_paths
    captured = paths.capture(str(checkout.source), str(checkout.destination))
    filename = "config.json" if kind == "trust" else "permissions-config.json"
    path = checkout.copilot / filename
    data = json.loads(path.read_text())
    nested = str(checkout.source / "new-nested-binding")
    if kind == "trust":
        data["trustedFolders"].append(nested)
    else:
        data["locations"][nested] = {"tool_approvals": []}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="nested permission bindings"):
        paths.apply(str(checkout.source), str(checkout.destination), captured)
    assert nested in (json.loads(path.read_text())["trustedFolders" if kind == "trust" else "locations"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_apply_preserves_a_permission_file_link_created_after_capture(checkout, kind):
    paths = relocation.permission_paths
    captured = paths.capture(str(checkout.source), str(checkout.destination))
    path = checkout.copilot / "config.json"
    original = path.read_bytes()
    external = checkout.home / "external-config.json"
    external.write_bytes(original)
    path.unlink()
    try:
        os.symlink(external, path) if kind == "symlink" else os.link(external, path)
    except OSError as exc:
        if kind == "symlink" and os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("native symlink creation privilege is unavailable")
        raise
    with pytest.raises(ValueError, match="redirected or linked"):
        paths.apply(str(checkout.source), str(checkout.destination), captured)
    assert path.samefile(external)
    assert external.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only staged-directory recovery")
def test_posix_copy_retry_preserves_readonly_files_and_directory_modes(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "stage"
    (source / "nested").mkdir(parents=True)
    destination.mkdir(mode=0o700)
    file = source / "nested" / "data"
    file.write_bytes(b"read-only preserved content")
    file.chmod(0o444)
    (source / "nested").chmod(0o500)
    source.chmod(0o500)
    before = relocation.transfer.manifest(source)
    try:
        relocation.transfer.copy(source, destination)
        relocation.transfer.copy(source, destination)
        assert relocation.transfer.manifest(source) == before
        assert relocation.transfer.manifest(destination) == before
    finally:
        for root in (source, destination):
            root.chmod(0o700)
            (root / "nested").chmod(0o700)


@pytest.mark.skipif(os.name != "nt", reason="Windows native cross-volume qualification")
@pytest.mark.timeout(300)
def test_native_cross_volume_move_requires_an_explicit_owned_second_root(checkout, request):
    from checkout_cross_volume_fixture import qualify

    qualify(checkout, request)


def test_external_fixture_keeps_primary_git_and_journal_outside_runner_teardown(tmp_path, monkeypatch):
    primary = tmp_path / "enrolled-primary"
    primary.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_WORKTREES_RELOCATION_SECOND_ROOT", str(tmp_path / "second"))
    monkeypatch.setenv("AGENT_WORKTREES_RELOCATION_PRIMARY_ROOT", str(primary))
    request = SimpleNamespace(node=SimpleNamespace(
        name="test_native_cross_volume_fixture", nodeid="fixture-owner",
    ))
    runner_home = tmp_path / "runner"
    home = _fixture_home(runner_home, request)
    assert home.parent == primary and home != runner_home
    owner = json.loads((home / "scratch-owner.json").read_text())
    assert owner["owner"] == request.node.nodeid and "retain on failure" in owner["retention"]
    monkeypatch.delenv("AGENT_WORKTREES_RELOCATION_PRIMARY_ROOT")
    with pytest.raises(ValueError, match="persistent primary root"):
        _fixture_home(runner_home, request)
