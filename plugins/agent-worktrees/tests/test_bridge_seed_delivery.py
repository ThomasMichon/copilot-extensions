"""Deferred Bridge creation seeds reuse existing receipt authority."""

import dataclasses
import io
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import bridge_seed_delivery as delivery, launch_seed_state


def test_inflight_retry_retains_original_operation_and_creation_receipt(monkeypatch, tmp_path):
    current = launch_seed_state.LaunchSeed("seed", "new", "creation task", 1)
    calls = []
    monkeypatch.setattr(launch_seed_state, "pending", lambda path: current)
    def take(path, **kwargs):
        nonlocal current
        calls.append(kwargs)
        current = dataclasses.replace(current, handoff_id=kwargs["handoff_id"])
        return {"seed": dataclasses.asdict(current)}
    monkeypatch.setattr(launch_seed_state, "take", take)
    args = SimpleNamespace(bridge_seed_operation="operation-a", bridge_scope_id="scope", seed=None)
    first = delivery.claim(args, tmp_path / "worktree.yaml")
    args.bridge_seed_operation = "operation-b"
    second = delivery.claim(args, tmp_path / "worktree.yaml")
    assert first == second
    assert second["bridge_seed_operation"] == "operation-a"
    assert second["deferred_creation_seed"]["seed"]["text"] == "creation task"
    assert calls[0]["handoff_id"] == calls[1]["handoff_id"] == "bridge-operation-a"


def test_legacy_unconfirmed_creation_seed_is_never_reclaimed(monkeypatch, tmp_path):
    monkeypatch.setattr(launch_seed_state, "pending", lambda path:
                        launch_seed_state.LaunchSeed("seed", "new", "old task", 1, "legacy-tty"))
    monkeypatch.setattr(launch_seed_state, "take", lambda *args, **kwargs: pytest.fail("must not take"))
    with pytest.raises(ValueError, match="already have been typed"):
        delivery.claim(SimpleNamespace(bridge_seed_operation="new", bridge_scope_id="scope", seed=None),
                       tmp_path / "worktree.yaml")


def test_settlement_uses_existing_finish_receipt(monkeypatch, tmp_path):
    receipt = {"seed": {"seed_id": "seed", "handoff_id": "bridge-op"}}
    called = []
    monkeypatch.setattr(delivery.config, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(delivery.worktree_identity, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(delivery.sys, "stdin", io.StringIO(json.dumps({"receipt": receipt})))
    monkeypatch.setattr(launch_seed_state, "finish",
                        lambda path, value: called.append((path, value)) or {"finished": True})
    assert delivery.settle(SimpleNamespace(worktree_id="worktree")) == 0
    assert called == [(tmp_path / "worktree.yaml", receipt)]
