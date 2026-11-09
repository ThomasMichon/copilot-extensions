"""Metadata checkpoints must never outrun their durable owned effects."""

import json
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as state
from agent_procutil.owned_process import run_caller_owned
from test_checkout_relocation import checkout as checkout


@pytest.mark.timeout(120)
@pytest.mark.parametrize("phase", ["published", "git-repaired"])
def test_failed_pointer_barrier_keeps_the_retryable_preceding_checkpoint(checkout, monkeypatch, phase):
    original = relocation.durability.files
    before = checkout.record.creation_nonce
    def failed(paths):
        if state.read(checkout.record.yaml_path)["phase"] == phase:
            raise OSError("injected pointer durability failure")
        original(paths)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "files", failed)
        with pytest.raises(OSError, match="pointer durability"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    retained = state.read(checkout.record.yaml_path)
    assert retained["phase"] == phase
    assert checkout.source.exists() and checkout.destination.exists()
    result = relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert result["phase"] == "complete" and result["stage"] == retained["stage"]
    assert result["identity"]["creation_nonce"] == before
    assert not checkout.source.exists()


@pytest.mark.parametrize("kind", ["trust", "permissions"])
def test_permission_write_access_is_rechecked_under_its_owner_lock(checkout, kind):
    paths = relocation.permission_paths
    captured = paths.capture(str(checkout.source), str(checkout.destination))
    path = checkout.copilot / ("config.json" if kind == "trust" else "permissions-config.json")
    before = path.read_bytes()
    path.chmod(0o444)
    try:
        with pytest.raises(ValueError, match="read-only|owned writable"):
            paths.apply(str(checkout.source), str(checkout.destination), captured)
        assert path.read_bytes() == before
    finally:
        path.chmod(0o644)


def test_expired_git_probe_is_a_structured_refusal_without_move_custody(checkout, monkeypatch, capsys):
    from agent_worktrees import worktree_ops_cli as cli

    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "rev-parse"], 10)
    monkeypatch.setattr(cli.cfg, "load_config", lambda: checkout.config)
    monkeypatch.setattr(relocation.git_ops, "git", expired)
    monkeypatch.setattr(
        relocation, "_stopped",
        lambda *_args: pytest.fail("the expired destination probe must refuse before owner probes"),
    )
    args = SimpleNamespace(
        capabilities=False, worktree_id=checkout.record.worktree_id,
        destination=str(checkout.destination), apply=True, abort=False,
    )
    assert cli.cmd_move(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is False and "timed out" in result["error"]
    assert not state.journal_path(checkout.record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.parametrize("spelling", ["dot", "trailing"])
def test_literal_recorded_path_alias_refuses_before_custody(checkout, spelling):
    from agent_worktrees import tracking

    record = checkout.record
    record.worktree_path = (
        str(checkout.source.parent) + os.sep + "." + os.sep + checkout.source.name
        if spelling == "dot" else str(checkout.source) + os.sep
    )
    tracking.save_record(record)
    before = record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="noncanonical"):
        relocation._action_plan(record.worktree_id, str(checkout.destination), checkout.config)
    assert record.yaml_path.read_bytes() == before
    assert not state.journal_path(record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.timeout(120)
def test_recovery_repeats_destination_parent_sync_after_rename(checkout, monkeypatch):
    original = relocation.durability.directory
    def failed(path):
        if path == checkout.destination.parent:
            raise OSError("injected post-rename parent sync failure")
        original(path)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "directory", failed)
        with pytest.raises(OSError, match="post-rename"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    retained = state.read(checkout.record.yaml_path)
    assert retained["phase"] == "copied" and not Path(retained["stage"]).exists()
    assert checkout.source.exists() and checkout.destination.exists()
    observed = []
    def synchronized(path):
        if path == checkout.destination.parent:
            observed.append(state.read(checkout.record.yaml_path)["phase"])
        original(path)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "directory", synchronized)
        result = relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert "copied" in observed and result["phase"] == "complete"
    assert result["stage"] == retained["stage"] and not checkout.source.exists()


@pytest.mark.timeout(120)
def test_partial_history_write_preserves_original_receipt_and_retries(checkout, monkeypatch):
    relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    ledger = checkout.record.yaml_path
    raw = state.journal_path(ledger).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    directory = ledger.parent / "checkout-relocations" / ledger.stem
    history = directory / (digest + ".json")
    original = Path.open
    @contextmanager
    def partial(path, *args, **kwargs):
        with original(path, *args, **kwargs) as stream:
            if path.parent == directory and args and args[0] == "xb":
                class InterruptedWriter:
                    def write(self, data):
                        stream.write(data[:12])
                        stream.flush()
                        raise OSError("injected partial archive write")
                yield InterruptedWriter()
            else:
                yield stream
    with monkeypatch.context() as injection:
        injection.setattr(Path, "open", partial)
        with pytest.raises(OSError, match="partial archive"):
            state.archive_terminal(ledger, digest)
    assert not history.exists() and state.journal_path(ledger).read_bytes() == raw
    state.archive_terminal(ledger, digest)
    assert history.read_bytes() == raw and state.journal_path(ledger).read_bytes() == raw


@pytest.mark.parametrize("kind", ["workspace", "database"])
def test_retained_destination_reference_refuses_before_custody(checkout, kind):
    if kind == "workspace":
        directory = checkout.copilot / "session-state" / "retained-destination"
        directory.mkdir(parents=True)
        path = directory / "workspace.yaml"
        path.write_text(json.dumps({"cwd": str(checkout.destination)}))
    else:
        path = checkout.copilot / "session-store.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE sessions (cwd TEXT)")
            connection.execute("CREATE TABLE session_files (file_path TEXT)")
            connection.execute("INSERT INTO sessions VALUES (?)", (str(checkout.destination),))
            connection.commit()
        finally:
            connection.close()
    before, ledger = path.read_bytes(), checkout.record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="retained Copilot"):
        relocation._action_plan(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert checkout.record.yaml_path.read_bytes() == ledger and path.read_bytes() == before
    assert not state.journal_path(checkout.record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.timeout(120)
def test_new_destination_reference_after_copy_still_allows_prepublication_abort(checkout, monkeypatch):
    original = relocation.transfer.copy
    directory = checkout.copilot / "session-state" / "new-destination"
    def copied(source, stage):
        original(source, stage)
        directory.mkdir(parents=True)
        (directory / "workspace.yaml").write_text(json.dumps({"cwd": str(checkout.destination)}))
    with monkeypatch.context() as injection:
        injection.setattr(relocation.transfer, "copy", copied)
        with pytest.raises(ValueError, match="retained Copilot"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert state.read(checkout.record.yaml_path)["phase"] == "copied"
    assert checkout.source.exists() and not checkout.destination.exists()
    result = relocation.abort(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert result["phase"] == "cancelled" and checkout.source.exists()
    assert (directory / "workspace.yaml").exists() and not checkout.destination.exists()


def test_retained_root_timeout_never_fabricates_authority_or_leaks_a_resume_exception(checkout, monkeypatch):
    from agent_worktrees import retained_checkout

    config = replace(checkout.config, repos={"sample": replace(
        checkout.config.default_repo, worktree_root=str(checkout.home / "future-root"),
    )})
    assert retained_checkout.matches(checkout.record, config)
    before = checkout.record.yaml_path.read_bytes()
    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "rev-parse"], 10)
    monkeypatch.setattr(retained_checkout.git_ops, "git", expired)
    assert retained_checkout.matches(checkout.record, config) is False
    with pytest.raises(ValueError, match="timed out; authority is unavailable"):
        retained_checkout.require_git_checkout(checkout.source, checkout.anchor, checkout.record.branch)
    assert checkout.record.yaml_path.read_bytes() == before


@pytest.mark.timeout(120)
@pytest.mark.parametrize("cut", ["rename", "parent-sync"])
def test_original_name_retirement_is_recoverable_before_complete(checkout, monkeypatch, cut):
    original_replace, original_directory = relocation.durability.replace, relocation.durability.directory
    def renamed(source, target, **kwargs):
        original_replace(source, target, **kwargs)
        if source == checkout.source:
            raise OSError("injected retirement rename interruption")
    def synchronized(path):
        if path == checkout.source.parent:
            retained = state.read(checkout.record.yaml_path)
            retirement = checkout.source.with_name(
                checkout.source.name + ".retiring-" + Path(retained["stage"]).name.rsplit("-", 1)[1],
            )
            if not retirement.exists():
                raise OSError("injected retirement parent-sync interruption")
        original_directory(path)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "replace" if cut == "rename" else "directory",
                          renamed if cut == "rename" else synchronized)
        with pytest.raises(OSError, match="retirement"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    retained = state.read(checkout.record.yaml_path)
    retirement = checkout.source.with_name(
        checkout.source.name + ".retiring-" + Path(retained["stage"]).name.rsplit("-", 1)[1],
    )
    assert retained["phase"] == ("rebound" if cut == "rename" else "retiring")
    assert not checkout.source.exists() and checkout.destination.exists()
    assert retirement.exists() is (cut == "rename")
    observed = []
    def completed(path):
        if path == checkout.source.parent:
            observed.append(state.read(checkout.record.yaml_path)["phase"])
        original_directory(path)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "directory", completed)
        result = relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert result["phase"] == "complete" and "retiring" in observed
    assert result["stage"] == retained["stage"]
    assert not checkout.source.exists() and not retirement.exists()


@pytest.mark.timeout(120)
def test_actual_move_json_keeps_stale_lock_diagnostics_out_of_stdout(checkout):
    lock = Path(checkout.config.default_repo.worktree_root) / ".finalize.lock"
    lock.write_text("0::owned-fixture-stale-lock")
    old = time.time() - 60
    os.utime(lock, (old, old))
    result = run_caller_owned(
        [sys.executable, "-m", "agent_worktrees", "--project", "sample", "move",
         checkout.record.worktree_id, "--to", str(checkout.destination), "--apply", "--json"],
        env=dict(os.environ, AGENT_HOME=str(checkout.home), USERPROFILE=str(checkout.home), HOME=str(checkout.home)),
        cwd=checkout.home, timeout=100, max_stdout_bytes=1024 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["phase"] == "complete"
    assert "Stale lock detected" in result.stderr and "Stale lock detected" not in result.stdout
    assert checkout.destination.exists() and not checkout.source.exists()


def test_fresh_fixture_interpreter_validates_namespace_and_installs_command_guard(checkout):
    script = """
import json, subprocess, sys
from plugin_test_command_safety import UnsafeTestCommand
try:
    subprocess.Popen([sys.argv[1], "-L", "foreign-not-runner-owned", "list-sessions"])
except UnsafeTestCommand:
    print(json.dumps({"guard_installed": True}))
else:
    raise AssertionError("unscoped native work was admitted")
"""
    env = dict(os.environ)
    missing_mux = checkout.home / "not-installed" / ("psmux.exe" if os.name == "nt" else "tmux")
    assert not missing_mux.exists()
    result = run_caller_owned(
        [sys.executable, "-c", script, str(missing_mux)], env=env, cwd=checkout.home, timeout=30,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["guard_installed"] is True
    env["RELOCATION_FIXTURE_MUX_ARGS"] = "[]"
    refused = run_caller_owned(
        [sys.executable, "-c", "print('unexpected native work')"], env=env, cwd=checkout.home, timeout=30,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert refused.returncode == 97 and not refused.stdout
    assert "fixture child scope refused" in refused.stderr


@pytest.mark.timeout(120)
@pytest.mark.parametrize("cut", ["copy", "permissions"])
def test_postcopy_source_deletion_never_silently_restores_ignored_content(checkout, monkeypatch, cut):
    original = relocation.transfer.copy if cut == "copy" else relocation.permission_paths.apply
    def changed(*args):
        original(*args)
        (checkout.source / "ignored.bin").unlink()
    with monkeypatch.context() as injection:
        injection.setattr(relocation.transfer if cut == "copy" else relocation.permission_paths,
                          "copy" if cut == "copy" else "apply", changed)
        with pytest.raises(ValueError, match="original copy changed"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    retained = state.read(checkout.record.yaml_path)
    assert checkout.source.exists() and not (checkout.source / "ignored.bin").exists()
    if cut == "copy":
        assert retained["phase"] == "copied" and not checkout.destination.exists()
        result = relocation.abort(checkout.record.worktree_id, str(checkout.destination), checkout.config)
        assert result["phase"] == "cancelled" and not (checkout.source / "ignored.bin").exists()
    else:
        assert retained["phase"] == "rebound" and checkout.destination.exists()
        assert (checkout.destination / "ignored.bin").read_bytes() == bytes(range(256))
        with pytest.raises(ValueError, match="original copy changed"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
        assert state.read(checkout.record.yaml_path)["phase"] == "rebound"
        assert checkout.source.exists() and not (checkout.source / "ignored.bin").exists()


@pytest.mark.timeout(120)
def test_partial_retirement_is_not_allowed_before_complete_copy_verification(checkout, monkeypatch):
    original = relocation.durability.replace
    def renamed(source, target, **kwargs):
        original(source, target, **kwargs)
        if source == checkout.source:
            (target / "ignored.bin").unlink()
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "replace", renamed)
        with pytest.raises(ValueError, match="original copy changed"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    retained = state.read(checkout.record.yaml_path)
    retirement = checkout.source.with_name(
        checkout.source.name + ".retiring-" + Path(retained["stage"]).name.rsplit("-", 1)[1],
    )
    assert retained["phase"] == "rebound" and retirement.exists() and not checkout.source.exists()
    assert not (retirement / "ignored.bin").exists()
    assert (checkout.destination / "ignored.bin").read_bytes() == bytes(range(256))
    with pytest.raises(ValueError, match="original copy changed"):
        relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert state.read(checkout.record.yaml_path)["phase"] == "rebound" and retirement.exists()


@pytest.mark.timeout(120)
@pytest.mark.parametrize("cut", ["publication", "rebound"])
def test_missing_original_before_owned_retirement_never_adopts_stale_destination(checkout, monkeypatch, cut):
    original_replace, original_write = relocation.durability.replace, state.write
    def published(source, target, **kwargs):
        original_replace(source, target, **kwargs)
        if target == checkout.destination:
            raise OSError("injected publication interruption")
    def rebound(ledger, report):
        original_write(ledger, report)
        if report["phase"] == "rebound":
            raise OSError("injected rebound interruption")
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability if cut == "publication" else state,
                          "replace" if cut == "publication" else "write",
                          published if cut == "publication" else rebound)
        with pytest.raises(OSError, match="injected"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    retained = state.read(checkout.record.yaml_path)
    before = checkout.record.yaml_path.read_bytes()
    shutil.rmtree(checkout.source)
    with pytest.raises(ValueError, match="disappeared before owned retirement"):
        relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert state.read(checkout.record.yaml_path)["phase"] == retained["phase"]
    assert checkout.record.yaml_path.read_bytes() == before and not checkout.source.exists()
    assert (checkout.destination / "ignored.bin").read_bytes() == bytes(range(256))


@pytest.mark.parametrize("change", ["grow", "shrink"])
def test_actual_file_growth_and_shrink_refuse_bounded_inventory(checkout, monkeypatch, change):
    path = checkout.source / "ignored.bin"
    before = path.read_bytes()
    original, changed = Path.open, False
    def opened(selected, *args, **kwargs):
        nonlocal changed
        if selected == path and args and args[0] == "rb" and not changed:
            changed = True
            with original(path, "wb") as stream:
                stream.write(before + b"new" if change == "grow" else before[:-1])
        return original(selected, *args, **kwargs)
    with monkeypatch.context() as injection:
        injection.setattr(Path, "open", opened)
        with pytest.raises(ValueError, match="changed during bounded inventory"):
            relocation.transfer.manifest(checkout.source)
    assert changed and path.read_bytes() != before
    assert not state.journal_path(checkout.record.yaml_path).exists()


def _companion_reservation(checkout, monkeypatch):
    from agent_worktrees import paired_allocation_location as paired

    monkeypatch.delenv("AGENT_WORKTREES_NO_PAIR", raising=False)
    anchor, allocation = checkout.home / "knowledge-anchor", checkout.home / "independent-knowledge-root"
    anchor.mkdir()
    allocation.mkdir()
    root = SimpleNamespace(requires_external=True, bound=True, repo="knowledge", path=str(anchor))
    receiver = SimpleNamespace(repo_name="knowledge", default_repo=SimpleNamespace(
        anchor=str(anchor), worktree_root=str(allocation),
    ))
    monkeypatch.setattr(paired.state_root, "resolve_state_root", lambda config: root)
    monkeypatch.setattr(paired.repos, "find_repo", lambda name: SimpleNamespace(repo_class="knowledge"))
    monkeypatch.setattr(paired.cfg, "load_project_config", lambda *args, **kwargs: receiver)
    path = checkout.record.yaml_path.with_suffix(".paired-allocation.json")
    def reserve():
        paired.reserve(checkout.config, checkout.record.worktree_id, checkout.record.creation_nonce)
    return paired, root, receiver, path, reserve


def test_partial_companion_reservation_never_publishes_final_intent(checkout, monkeypatch):
    paired, root, receiver, path, reserve = _companion_reservation(checkout, monkeypatch)
    original = Path.open
    @contextmanager
    def partial(selected, *args, **kwargs):
        with original(selected, *args, **kwargs) as stream:
            if selected.parent == path.parent and ".paired-allocation" in selected.name and args and args[0] == "x":
                class InterruptedWriter:
                    def write(self, data):
                        stream.write(data[:1])
                        stream.flush()
                        raise OSError("injected reservation write")
                yield InterruptedWriter()
            else:
                yield stream
    with monkeypatch.context() as injection:
        injection.setattr(Path, "open", partial)
        with pytest.raises(OSError, match="reservation write"):
            reserve()
    assert not path.exists()
    reserve()
    assert paired.original(checkout.record, root) == Path(receiver.default_repo.worktree_root) / (
        checkout.record.worktree_id + "-k"
    )


def test_companion_reservation_barrier_precedes_success_and_preserves_original_root(checkout, monkeypatch):
    paired, root, receiver, path, reserve = _companion_reservation(checkout, monkeypatch)
    original = relocation.durability.directory
    def failed(selected):
        if selected == path.parent:
            raise OSError("injected reservation parent barrier")
        original(selected)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "directory", failed)
        with pytest.raises(OSError, match="reservation parent"):
            reserve()
    before, initial_root = path.read_bytes(), receiver.default_repo.worktree_root
    receiver.default_repo.worktree_root = str(checkout.home / "later-root")
    assert paired.original(checkout.record, root).parent == Path(initial_root)
    with pytest.raises(ValueError, match="intent conflicts"):
        reserve()
    assert path.read_bytes() == before
    receiver.default_repo.worktree_root = initial_root
    reserve()
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["session-root", "session-child", "launch-requests", "launch-attempt-owner"])
def test_incomplete_operational_directory_inventory_refuses_before_custody(checkout, monkeypatch, kind):
    session = checkout.copilot / "session-state" / "retained"
    if kind.startswith("session"):
        session.mkdir(parents=True)
        (session / "workspace.yaml").write_text(json.dumps({"cwd": str(checkout.source)}))
        denied = session.parent if kind == "session-root" else session
    elif kind == "launch-requests":
        denied = checkout.record.yaml_path.parent / "launch-requests"
        denied.mkdir()
        (denied / "request.json").write_text("{}")
    else:
        denied = checkout.record.yaml_path.parent / "launch-attempts" / "owner"
        denied.mkdir(parents=True)
        (denied / ("a" * 32 + ".json")).write_text("{}")
    before = checkout.record.yaml_path.read_bytes()
    original = os.scandir
    def unreadable(path):
        if isinstance(path, (str, bytes, Path)) and Path(os.fsdecode(path)) == denied:
            raise PermissionError("injected incomplete operational inventory")
        return original(path)
    with monkeypatch.context() as injection:
        injection.setattr(os, "scandir", unreadable)
        with pytest.raises(PermissionError, match="incomplete operational"):
            relocation._action_plan(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert checkout.record.yaml_path.read_bytes() == before
    assert not state.journal_path(checkout.record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.timeout(120)
def test_cancellation_repeats_parent_barrier_before_terminal_receipt(checkout, monkeypatch):
    def interrupted(*args):
        raise OSError("injected prepublication copy interruption")
    with monkeypatch.context() as injection:
        injection.setattr(relocation.transfer, "copy", interrupted)
        with pytest.raises(OSError, match="copy interruption"):
            relocation.move(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    stage = Path(state.read(checkout.record.yaml_path)["stage"])
    original = relocation.durability.directory
    def failed(path):
        if path == stage.parent:
            raise OSError("injected cancellation parent barrier")
        original(path)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "directory", failed)
        with pytest.raises(OSError, match="cancellation parent"):
            relocation.abort(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert state.read(checkout.record.yaml_path)["phase"] == "cancelling"
    assert not stage.exists() and checkout.source.exists() and not checkout.destination.exists()
    observed = []
    def synchronized(path):
        if path == stage.parent:
            observed.append(state.read(checkout.record.yaml_path)["phase"])
        original(path)
    with monkeypatch.context() as injection:
        injection.setattr(relocation.durability, "directory", synchronized)
        result = relocation.abort(checkout.record.worktree_id, str(checkout.destination), checkout.config)
    assert result["phase"] == "cancelled" and "cancelling" in observed
    assert checkout.source.exists() and not checkout.destination.exists()


@pytest.mark.parametrize("operation", ["manifest", "durability"])
def test_incomplete_checkout_tree_never_counts_as_complete_inventory(checkout, monkeypatch, operation):
    directory = checkout.source / "unreadable"
    directory.mkdir()
    payload = directory / "unique-data"
    payload.write_bytes(b"must remain present")
    original = os.scandir
    def unreadable(path):
        if isinstance(path, (str, bytes, Path)) and Path(os.fsdecode(path)) == directory:
            raise PermissionError("injected incomplete checkout inventory")
        return original(path)
    with monkeypatch.context() as injection:
        injection.setattr(os, "scandir", unreadable)
        with pytest.raises(PermissionError, match="incomplete checkout"):
            (relocation.transfer.manifest if operation == "manifest" else relocation.durability.tree)(checkout.source)
    assert payload.read_bytes() == b"must remain present"
    assert not state.journal_path(checkout.record.yaml_path).exists()
    assert checkout.source.exists() and not checkout.destination.exists()


def test_unqualified_platform_refuses_before_receiving_record_lookup_or_custody(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unqualified platform must not inspect or mutate receiving state")
    monkeypatch.setattr(relocation, "_native_supported", lambda: False)
    monkeypatch.setattr(relocation.cfg, "tracking_dir", forbidden)
    sentinel = tmp_path / "preserved"
    sentinel.write_bytes(b"untouched")
    for operation in (relocation.plan, relocation.move, relocation.abort):
        with pytest.raises(ValueError, match="native Windows"):
            operation("unqualified", str(tmp_path / "destination"), None)
    assert sentinel.read_bytes() == b"untouched"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["preserved"]
    assert relocation.capabilities()["apply"] is False


def test_historical_companion_without_intent_keeps_its_anchor_derived_root(checkout, monkeypatch):
    paired, root, receiver, path, reserve = _companion_reservation(checkout, monkeypatch)
    expected = Path(paired.cfg.derive_worktree_root(root.path))
    selected, retained = paired.receiver_root(checkout.config, checkout.record.worktree_id, root, None)
    assert Path(selected) == expected
    assert retained == expected / (checkout.record.worktree_id + "-k")
    assert not path.exists()
    reserve()
    selected, retained = paired.receiver_root(checkout.config, checkout.record.worktree_id, root, None)
    assert Path(selected) == Path(receiver.default_repo.worktree_root)
    assert retained == Path(selected) / (checkout.record.worktree_id + "-k")


@pytest.mark.parametrize("foreign", ["record", "configuration"])
def test_retained_foreign_platform_never_acquires_publication_authority(checkout, foreign):
    from agent_worktrees import pr_source_publication, retained_checkout, tracking

    config = replace(checkout.config, repos={"sample": replace(
        checkout.config.default_repo, worktree_root=str(checkout.home / "future-allocation"),
    )})
    checkout.record.status = "active"
    tracking.save_record(checkout.record)
    assert retained_checkout.matches(checkout.record, config)
    assert pr_source_publication._caller(checkout.record.worktree_id, config).worktree_path == str(checkout.source)
    other = "linux" if checkout.record.platform != "linux" else "windows"
    if foreign == "record":
        checkout.record.platform = other
        tracking.save_record(checkout.record)
    else:
        config = replace(config, platform=other)
    before = checkout.record.yaml_path.read_bytes()
    assert retained_checkout.matches(checkout.record, config) is False
    with pytest.raises(pr_source_publication.SourcePublicationError, match="does not own"):
        pr_source_publication._caller(checkout.record.worktree_id, config)
    assert checkout.record.yaml_path.read_bytes() == before


def test_native_relative_git_backpointer_retains_same_family_and_publication_authority(checkout):
    from agent_worktrees import git_ops, pr_source_publication, retained_checkout, tracking

    result = git_ops.git("worktree", "repair", "--relative-paths", str(checkout.source),
                         cwd=checkout.anchor, check=False)
    if result.returncode and "unknown option" in result.stderr:
        pytest.skip("native Git does not support relative worktree repair")
    assert result.returncode == 0, (result.stdout, result.stderr)
    administrative = Path(git_ops.git("rev-parse", "--absolute-git-dir", cwd=checkout.source).stdout.strip())
    assert not Path((administrative / "gitdir").read_text().strip()).is_absolute()
    config = replace(checkout.config, repos={"sample": replace(
        checkout.config.default_repo, worktree_root=str(checkout.home / "future-allocation"),
    )})
    checkout.record.status = "active"
    tracking.save_record(checkout.record)
    assert retained_checkout.matches(checkout.record, config)
    assert pr_source_publication._caller(checkout.record.worktree_id, config).worktree_path == str(checkout.source)
    (administrative / "gitdir").write_text(os.path.relpath(checkout.home / "foreign" / ".git", administrative) + "\n")
    assert retained_checkout.matches(checkout.record, config) is False
    with pytest.raises(pr_source_publication.SourcePublicationError, match="does not own"):
        pr_source_publication._caller(checkout.record.worktree_id, config)
