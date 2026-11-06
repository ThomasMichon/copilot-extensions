"""Real Git/copy/record/permission consumers for the conservative first flow."""

from __future__ import annotations

import json
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

@pytest.fixture
def checkout(tmp_path, monkeypatch):
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
    return relocation.plan(fixture.record.worktree_id, str(fixture.destination), fixture.config)


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


def test_copy_interruption_fences_launch_and_retries_original_operation(checkout, monkeypatch):
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
    with pytest.raises(ValueError, match="unfinished"), worktree_lifecycle.guard(checkout.record.yaml_path):
        pytest.fail("an unfinished move must fence a subsequent launch")
    with pytest.raises(ValueError, match="unfinished"):
        tracking.save_record(tracking.load_record(checkout.record.yaml_path))
    monkeypatch.setattr(relocation.transfer, "copy", copy)
    assert _move(checkout)["phase"] == "complete"


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


@pytest.mark.parametrize("blocker", ["dirty", "session", "unknown", "open-pr", "claim", "submodule", "git-lock", "foreign-machine", "source-alias", "seed"])
def test_refusals_leave_original_and_no_journal(checkout, monkeypatch, blocker):
    record = checkout.record
    if blocker == "dirty":
        (checkout.source / "tracked.txt").write_bytes(b"dirty")
    elif blocker == "session":
        monkeypatch.setattr(sessions, "verify_worktree_active", lambda _: sessions.LiveVerdict(active=True))
    elif blocker == "unknown":
        monkeypatch.setattr(sessions, "verify_worktree_active", lambda _: sessions.LiveVerdict(probes_ok=False))
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
                if Path(path) == fixture.source:
                    (fixture.source / "ignored.bin").unlink()
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
    assert fixture.source.exists()
    assert fixture.destination.exists()
    assert (fixture.destination / "ignored.bin").read_bytes() == bytes(range(256))
    assert _move(fixture)["phase"] == "complete"
    assert not fixture.source.exists()
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


def test_actual_cli_parser_process_plan_move_and_post_move_consumer(checkout):
    env = dict(os.environ, AGENT_HOME=str(checkout.home), USERPROFILE=str(checkout.home), HOME=str(checkout.home))
    command = [
        sys.executable, "-c",
        "import faulthandler,runpy; faulthandler.dump_traceback_later(20,repeat=True); "
        "runpy.run_module('agent_worktrees',run_name='__main__')",
        "--project", "sample",
    ]
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
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="read-only proof"):
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


def test_retained_session_database_reference_refuses_before_mutation(checkout):
    database = checkout.copilot / "session-store.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE sessions (cwd TEXT)")
        connection.execute("CREATE TABLE session_files (file_path TEXT)")
        connection.execute("INSERT INTO sessions VALUES (?)", (str(checkout.source),))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match="retained Copilot database"):
        _plan(checkout)
    assert not move_state.journal_path(checkout.record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native cross-volume qualification")
def test_native_cross_volume_move_requires_an_explicit_owned_second_root(checkout, request):
    root = os.environ.get("AGENT_WORKTREES_RELOCATION_SECOND_ROOT")
    if not root:
        pytest.skip("second fixture volume not enrolled")
    approved = Path(root)
    assert approved.is_dir()
    assert checkout.source.stat().st_dev != approved.stat().st_dev
    content_limit = 1024 * 1024
    assert sum(path.stat().st_size for path in checkout.source.rglob("*") if path.is_file()) + 16384 <= content_limit
    original_manifest = relocation.transfer.manifest(checkout.source)
    original_nonce = checkout.record.creation_nonce
    parent = approved / ("relocation-fixture-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    parent.mkdir(mode=0o700)
    owner = {
        "owner": request.node.nodeid, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "disposable native cross-volume relocation qualification",
        "retention": "owned until successful fixture teardown; retain failures for owner inspection",
    }
    (parent / "scratch-owner.json").write_text(json.dumps(owner), encoding="utf-8")
    destination = parent / checkout.record.worktree_id
    assert not destination.exists()
    checkout.destination = destination
    succeeded = False
    try:
        assert _move(checkout)["phase"] == "complete"
        assert (destination / "ignored.bin").read_bytes() == bytes(range(256))
        assert not checkout.source.exists()
        assert sum(path.stat().st_size for path in parent.rglob("*") if path.is_file()) <= content_limit
        checkout.destination = checkout.source
        assert _move(checkout)["phase"] == "complete"
        assert not destination.exists()
        assert relocation.transfer.manifest(checkout.source) == original_manifest
        current = tracking.load_record(checkout.record.yaml_path)
        assert current.worktree_path == str(checkout.source)
        assert current.creation_nonce == original_nonce
        succeeded = True
    finally:
        if succeeded:
            assert sorted(path.name for path in parent.iterdir()) == ["scratch-owner.json"]
            shutil.rmtree(parent)
