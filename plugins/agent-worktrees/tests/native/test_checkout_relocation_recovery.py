"""Metadata checkpoints must never outrun their durable owned effects."""

import json
import hashlib
import os
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

    def expired(*args):
        raise subprocess.TimeoutExpired(["git", "rev-parse"], 10)
    monkeypatch.setattr(cli.cfg, "load_config", lambda: checkout.config)
    monkeypatch.setattr(relocation, "_git", expired)
    args = SimpleNamespace(
        capabilities=False, worktree_id=checkout.record.worktree_id,
        destination=str(checkout.destination), apply=False, abort=False,
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
        relocation.plan(record.worktree_id, str(checkout.destination), checkout.config)
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
        relocation.plan(checkout.record.worktree_id, str(checkout.destination), checkout.config)
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
    assert retained["phase"] == "rebound"
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
    assert result["phase"] == "complete" and "rebound" in observed
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
