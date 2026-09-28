from __future__ import annotations

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker import engine_group_a


def test_picker_paths_reads_group_a_contract(monkeypatch):
    payload = {
        "version": 1,
        "install_dir": "/state/.agent-worktrees",
        "installed_plugins_dir": "/state/.copilot/installed-plugins",
    }
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: payload)

    assert engine_group_a.picker_paths("dotfiles") == payload


def test_state_root_resolution_allows_unbound_nonzero_payload(monkeypatch):
    payload = {
        "state_root": None,
        "source": "knowledge_repo",
        "repo": "knowledge",
        "stateless": True,
        "requires_external": True,
        "bound": False,
        "error": "no knowledge repo is bound",
    }
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: payload)

    assert engine_group_a.state_root_resolution("dotfiles") == payload


def test_update_stage_indicator_state_reads_json_contract(monkeypatch):
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda *args, **kwargs: {"version": 1, "indicator_state": "available"},
    )

    assert engine_group_a.update_stage_indicator_state("dotfiles") == "available"


def test_update_stage_indicator_state_classifies_unsupported_engine(monkeypatch):
    def _boom(*args, **kwargs):
        raise engine_client.EngineError(
            "agent-worktrees stage-update --indicator-state --json failed "
            "(exit 2): unrecognized arguments: --indicator-state"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        engine_group_a.update_stage_indicator_state("dotfiles")
