"""Actual source/profile resolution before existing-owner retained preparation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from test_review_executor import environment as environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_workspace_selection import selection_workspace as selection_workspace


@pytest.fixture
def producer(
    environment: tuple, comment_preparer: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple:
    from agent_worktrees import config as cfg
    from comment_producer import CommentProducer

    root = tmp_path / "source"
    (root / ".github").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "project"
    (state / "worktrees").mkdir(parents=True)
    record = {
        "worktree_id": "selected-producer", "branch": "worktree/selected-producer",
        "repo": "fixture", "machine": "fixture-machine", "platform": "linux",
        "worktree_path": str(root), "codename": "bolted-circuit",
        "codename_source": "built-in", "sessions": [],
    }
    record_path = state / "worktrees/selected-producer.yaml"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    profile = root / ".github/ai-attribution.json"
    profile.write_text(json.dumps({
        "version": 1, "rules": [
            {"header": "[Selected profile] "},
            {"when": {"activity": "reply"}, "header": "[Reply profile] "},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: state)
    monkeypatch.setattr(Path, "home", lambda: home)
    config = cfg.Config(
        srcroot=str(tmp_path), machine="fixture-machine", platform="linux", repo_name="fixture",
        repos={"fixture": cfg.RepoConfig(anchor=str(root), worktree_root=str(tmp_path))},
    )
    selected = CommentProducer(
        root, config, environment[1]["scope"],
        {"domain": "forge", "visibility": "public", "activity": "comment", "required": True},
    )
    return selected, profile, record_path, record


@pytest.mark.parametrize("environment", [True], indirect=True)
def test_actual_resolver_precedes_commit_and_original_preview_survives_source_profile_loss(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, producer: tuple,
) -> None:
    from agent_pull_requests.review_selection_store import ReviewSelectionStore

    store, _, _, attachment, backend, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    selected, profile, record_path, _ = producer
    native.operations.selection_preparer = selected
    native.exchange("capabilities", {})
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    preview = native.exchange("allocate", request)["result"]
    assert backend.calls == []
    assert preview["intent"]["actions"][0]["body"].startswith("[Selected profile] ")
    assert preview["intent"]["actions"][0]["preparation"]["identity"]["source"]["origin"] == {
        "codename": "bolted-circuit",
    }
    retained = ReviewSelectionStore(store).recover(draft, expires_at=native.expires_at())
    assert retained["intent"] == preview["intent"]
    profile.write_text("invalid-profile", encoding="utf-8")
    record_path.write_text("invalid-record", encoding="utf-8")
    assert native.exchange("allocate", request)["result"] == preview
    assert backend.calls == []


@pytest.mark.parametrize("environment", [True], indirect=True)
def test_resolved_composer_is_pure_and_detached_from_later_owner_changes(
    environment: tuple, producer: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import comment_producer
    from agent_worktrees import tracking

    selected, profile, record_path, _ = producer
    compose = selected(environment[2], checkpoint=lambda: None)
    profile.write_text("invalid-profile", encoding="utf-8")
    record_path.write_text("invalid-record", encoding="utf-8")
    monkeypatch.setattr(comment_producer, "load_profile", lambda *a: pytest.fail("profile IO under lock"))
    monkeypatch.setattr(tracking, "load_record_by_id", lambda *a, **k: pytest.fail("source IO under lock"))
    identity = {
        "comment_id": "11111111-1111-4111-8111-111111111111",
        "write_id": "22222222-2222-4222-8222-222222222222",
    }
    prepared = compose("comment", "Selected note.", scope=environment[1]["scope"], **identity)
    assert prepared["body"].startswith("[Selected profile] ")
    assert compose("comment", prepared["body"], scope=environment[1]["scope"],
                   previous=prepared, **identity) == prepared
    reply = compose("reply", "Reply note.", scope=environment[1]["scope"], **identity)
    assert reply["body"].startswith("[Reply profile] ")
    edited = compose(
        "edit_owned_comment", "Edited note.", scope=environment[1]["scope"],
        comment_id=identity["comment_id"], write_id="33333333-3333-4333-8333-333333333333",
        previous=prepared,
    )
    assert edited["body"].startswith("[Selected profile] Edited note.")
    assert edited["identity"]["comment_id"] == prepared["identity"]["comment_id"]
    wrong = {**environment[1]["scope"], "number": 43}
    with pytest.raises(ValueError, match="action binding"):
        compose("comment", "Selected note.", scope=wrong, **identity)


@pytest.mark.parametrize("environment", [True], indirect=True)
@pytest.mark.parametrize("failure", ["profile", "record", "nested", "scope", "old-owner"])
def test_missing_or_differently_bound_owner_never_creates_a_selection(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple,
    producer: tuple, failure: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    store, _, _, attachment, backend, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    selected, profile, record_path, record = producer
    if failure == "profile":
        profile.write_text("invalid-profile", encoding="utf-8")
    elif failure == "record":
        record_path.write_text(json.dumps({**record, "repo": "another-repository"}), encoding="utf-8")
    elif failure == "nested":
        nested = selected.source_root / "nested"
        nested.mkdir()
        selected = replace(selected, source_root=nested)
    elif failure == "old-owner":
        from agent_worktrees import attribution_prepare_cli

        monkeypatch.delattr(attribution_prepare_cli, "resolve_comment_origin")
    else:
        selected = replace(selected, scope={**selected.scope, "number": 43})
    native.operations.selection_preparer = selected
    native.exchange("capabilities", {})
    before = store.path.read_bytes()
    message = {
        "profile": "signature profile unavailable",
        "record": "differs from its managed source",
        "nested": "differs from its managed source",
        "scope": "destination binding differs",
        "old-owner": "source owner unavailable",
    }[failure]
    with pytest.raises(ValueError, match=message):
        native.exchange("allocate", {
            "number": 42, "intent": copy.deepcopy(draft),
            "source_attachment": attachment, "budget_seconds": 60,
        })
    assert store.path.read_bytes() == before and backend.calls == []
