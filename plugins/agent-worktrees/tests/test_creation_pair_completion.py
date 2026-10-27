"""Pair summaries cannot turn failed receiving composition into completion."""

from types import SimpleNamespace

import pytest

from agent_worktrees.creation_pair_completion import complete_created_pair
from agent_worktrees import local_cache_refresh, receiving_creation_recovery


@pytest.mark.parametrize("action", ["no-op", "retired", "error"])
def test_pair_error_never_completes_receiving_creation(monkeypatch, action):
    record = SimpleNamespace(
        kind="session", worktree_id="original", platform="linux", machine_identity=None,
        worktree_path="/synthetic/child", receiving_allocation_present=True,
    )
    pending = []
    monkeypatch.setattr(local_cache_refresh, "mark_pair_pending", pending.append)
    monkeypatch.setattr(receiving_creation_recovery, "requires_pair", lambda *a: False)
    with pytest.raises(RuntimeError, match="pairing remains incomplete"):
        complete_created_pair(
            None, record, timestamp="20261026-123456", suffix="abcd", no_pair=False,
            carve=lambda *a, **k: {"pair_id": "original"},
            stamp=lambda *a, **k: {"action": action, "pair_error": "invalid live pair"},
        )
    assert pending == [record.worktree_path]


@pytest.mark.parametrize("no_pair", [False, True])
def test_original_required_pair_cannot_complete_from_none_callbacks(monkeypatch, no_pair):
    record = SimpleNamespace(
        kind="session", worktree_id="original", platform="linux", machine_identity=None,
        worktree_path="/synthetic/child", receiving_allocation_present=True,
    )
    pending = []
    monkeypatch.setattr(local_cache_refresh, "mark_pair_pending", pending.append)
    monkeypatch.setattr(receiving_creation_recovery, "requires_pair", lambda *a: True)
    with pytest.raises(RuntimeError, match="pairing remains incomplete"):
        complete_created_pair(
            None, record, timestamp="20261026-123456", suffix="abcd", no_pair=no_pair,
            carve=lambda *a, **k: None, stamp=lambda *a, **k: None,
        )
    assert pending == [record.worktree_path]


def test_intentional_nonreceiving_no_pair_does_not_consult_receiving_journal(monkeypatch):
    record = SimpleNamespace(kind="session", receiving_allocation_present=False)
    monkeypatch.setattr(
        receiving_creation_recovery, "requires_pair",
        lambda *a: pytest.fail("ordinary creation must not inspect a receiving journal"),
    )
    assert complete_created_pair(
        None, record, timestamp="20261026-123456", suffix="abcd", no_pair=True,
        carve=lambda *a, **k: pytest.fail("intentional no-pair must not carve"),
        stamp=lambda *a, **k: pytest.fail("intentional no-pair must not stamp"),
    )
