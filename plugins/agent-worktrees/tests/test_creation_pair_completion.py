"""Pair summaries cannot turn failed receiving composition into completion."""

from types import SimpleNamespace

import pytest

from agent_worktrees.creation_pair_completion import complete_created_pair
from agent_worktrees import local_cache_refresh


@pytest.mark.parametrize("action", ["no-op", "retired", "error"])
def test_pair_error_never_completes_receiving_creation(monkeypatch, action):
    record = SimpleNamespace(
        kind="session", worktree_id="original", platform="linux", machine_identity=None,
        worktree_path="/synthetic/child", receiving_allocation_present=True,
    )
    pending = []
    monkeypatch.setattr(local_cache_refresh, "mark_pair_pending", pending.append)
    with pytest.raises(RuntimeError, match="pairing remains incomplete"):
        complete_created_pair(
            None, record, timestamp="20261026-123456", suffix="abcd", no_pair=False,
            carve=lambda *a, **k: {"pair_id": "original"},
            stamp=lambda *a, **k: {"action": action, "pair_error": "invalid live pair"},
        )
    assert pending == [record.worktree_path]
