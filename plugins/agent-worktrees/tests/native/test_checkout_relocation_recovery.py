"""Metadata checkpoints must never outrun their durable owned effects."""

import json
import hashlib
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as state
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
