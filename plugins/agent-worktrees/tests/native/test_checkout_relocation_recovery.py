"""Metadata checkpoints must never outrun their durable owned effects."""

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
