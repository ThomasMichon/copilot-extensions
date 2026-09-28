from __future__ import annotations

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker import engine_group_c


def test_picker_reconcile_local_reads_group_c_contract(monkeypatch):
    payload = {
        "version": 1,
        "rows": [{"id": "wt-one", "session_bound_live": True}],
        "summary": {
            "platform": "windows",
            "requested_worktree_ids": ["wt-one"],
            "record_count": 1,
            "pr_terminal_count": 0,
            "bound_visible_change_count": 1,
            "had_unresolved_bound": False,
            "mux_scan_ok": True,
        },
    }
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: payload)

    batch = engine_group_c.picker_reconcile_local("dotfiles", worktree_ids=["wt-one"])

    assert batch.version == 1
    assert batch.rows == [{"id": "wt-one", "session_bound_live": True}]
    assert batch.summary["requested_worktree_ids"] == ["wt-one"]


def test_picker_reconcile_local_classifies_unsupported_engine(monkeypatch):
    def _boom(*args, **kwargs):
        raise engine_client.EngineError(
            "agent-worktrees picker-reconcile-local --json failed "
            "(exit 2): invalid choice: 'picker-reconcile-local'"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        engine_group_c.picker_reconcile_local("dotfiles")


def test_picker_reconcile_local_payload_parser_rejects_bad_shape():
    with pytest.raises(ValueError, match="rows must be a list of objects"):
        engine_group_c.PickerReconcileLocalBatch.from_payload(
            {"version": 1, "rows": "nope", "summary": {}}
        )

    with pytest.raises(ValueError, match="summary must be an object"):
        engine_group_c.PickerReconcileLocalBatch.from_payload(
            {"version": 1, "rows": [], "summary": None}
        )
