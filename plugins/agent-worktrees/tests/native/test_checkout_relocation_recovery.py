"""Metadata checkpoints must never outrun their durable owned effects."""

import json
import subprocess
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
