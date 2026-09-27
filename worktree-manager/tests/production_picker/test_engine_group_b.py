from __future__ import annotations

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker import context, engine_group_b


def test_picker_bootstrap_reads_group_b_contract(monkeypatch):
    payload = {
        "version": 1,
        "project": "dotfiles",
        "should_switch_cwd": True,
        "cwd": "/state/dotfiles",
        "default_live": False,
    }
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: payload)

    assert engine_group_b.picker_bootstrap("dotfiles") == payload


def test_picker_bootstrap_classifies_unsupported_engine(monkeypatch):
    def _boom(*args, **kwargs):
        raise engine_client.EngineError(
            "agent-worktrees picker-bootstrap --json failed "
            "(exit 2): invalid choice: 'picker-bootstrap'"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        engine_group_b.picker_bootstrap("dotfiles")


def test_repair_stale_anchor_reads_group_b_contract(monkeypatch):
    payload = {
        "version": 1,
        "project": "dotfiles",
        "status": "unchanged",
        "self_present_before": True,
        "self_present_after": True,
    }
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: payload)

    assert engine_group_b.repair_stale_anchor("dotfiles") == payload


def test_repair_stale_anchor_classifies_unsupported_engine(monkeypatch):
    def _boom(*args, **kwargs):
        raise engine_client.EngineError(
            "agent-worktrees repair-stale-anchor --json failed "
            "(exit 2): unknown command repair-stale-anchor"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        engine_group_b.repair_stale_anchor("dotfiles")


def test_bind_project_bootstrap_records_authoritative_project():
    context.set_project("requested-project")

    binding = context.bind_project_bootstrap(
        {
            "version": 1,
            "project": "resolved-project",
            "should_switch_cwd": True,
            "cwd": "/state/resolved-project",
            "default_live": True,
        }
    )

    assert context.project() == "resolved-project"
    assert context.project_bootstrap() == binding
    assert binding.cwd == "/state/resolved-project"
    assert binding.should_switch_cwd is True
    assert binding.default_live is True


def test_set_project_clears_stale_bootstrap_binding():
    context.bind_project_bootstrap(
        {
            "version": 1,
            "project": "resolved-project",
            "should_switch_cwd": False,
            "cwd": None,
            "default_live": False,
        }
    )

    context.set_project("next-project")

    assert context.project() == "next-project"
    assert context.project_bootstrap() is None
