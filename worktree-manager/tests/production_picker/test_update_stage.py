from __future__ import annotations

from worktree_manager.production_picker import context as picker_context
from worktree_manager.production_picker import engine_group_a
from worktree_manager.production_picker import update_stage


def test_indicator_state_reads_engine_contract(monkeypatch):
    monkeypatch.setattr(picker_context, "project", lambda: "dotfiles")
    monkeypatch.setattr(
        engine_group_a,
        "update_stage_indicator_state",
        lambda project: "available",
    )

    assert update_stage.indicator_state() == "available"


def test_indicator_state_degrades_when_engine_lacks_flag(monkeypatch):
    monkeypatch.setattr(picker_context, "project", lambda: "dotfiles")

    def _unsupported(project):
        raise engine_group_a.engine_client.EngineFeatureUnavailable("unsupported")

    monkeypatch.setattr(
        engine_group_a,
        "update_stage_indicator_state",
        _unsupported,
    )

    assert update_stage.indicator_state() == "idle"
