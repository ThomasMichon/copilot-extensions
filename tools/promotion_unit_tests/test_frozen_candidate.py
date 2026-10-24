"""Pure frozen-object admission and publication ordering; Git is a mocked boundary."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

import promote_release as promotion
import promotion_frozen_candidate as frozen
import promotion_python_artifacts as artifacts

DEV, MAIN, CANDIDATE, CONTENT, TREE = (letter * 40 for letter in "abcde")
PREPARED = "2026-10-23T12:00:00Z"


def record():
    return {
        "schema": frozen.SCHEMA, "version": 1, "dev_sha": DEV, "main_before": MAIN,
        "candidate_sha": CANDIDATE, "content_tree": CONTENT, "tree": TREE,
        "prepared_at": PREPARED, "bundle_sha256": hashlib.sha256(b"synthetic bundle").hexdigest(),
        "report": {"promoted": True, "dev_head": DEV, "main_before": MAIN, "commit": CANDIDATE},
    }


@pytest.mark.parametrize("field", ["dev_sha", "main_before", "candidate_sha", "content_tree", "tree", "bundle_sha256", "prepared_at"])
def test_frozen_identity_rejects_invalid_or_mutated_inputs(field):
    data = record()
    data[field] = "invalid"
    with pytest.raises(ValueError):
        frozen.validate_record(data, dev_sha=DEV, main_before=MAIN)


@pytest.mark.parametrize("field", ["commit", "dev_head", "main_before"])
def test_frozen_report_cannot_introduce_another_release_identity(field):
    data = record()
    data["report"][field] = "f" * 40
    with pytest.raises(ValueError):
        frozen.validate_record(data, dev_sha=DEV, main_before=MAIN)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    directory = tmp_path / "frozen"
    directory.mkdir()
    (directory / "candidate.bundle").write_bytes(b"synthetic bundle")
    (directory / "candidate.json").write_text(json.dumps(record()))
    calls = []
    state = {
        "last_promotion": {"dev_head": DEV, "main_before": MAIN, "promoted_at": PREPARED},
    }

    def git(arguments, **kwargs):
        calls.append(arguments)
        if arguments[:2] == ["bundle", "list-heads"]:
            return CANDIDATE + " refs/promotion-freeze/synthetic"
        if arguments[:3] == ["show", "-s", "--format=%P"]:
            return MAIN
        if arguments[0] == "rev-parse":
            return TREE
        if arguments[0] == "show":
            return json.dumps(state)
        return ""

    monkeypatch.setattr(promotion, "_git", git)
    monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *_a, **_k: CONTENT)
    return directory, calls, state


def test_frozen_import_checks_objects_not_only_self_declared_hashes(bundle):
    directory, calls, _state = bundle
    assert frozen.import_candidate(directory, repo=directory, dev_sha=DEV, main_before=MAIN) == record()
    assert calls[0][:2] == ["bundle", "verify"]
    assert any(call[:3] == ["fetch", "--no-tags", "--no-write-fetch-head"] for call in calls)
    assert not any(call[0] in ("push", "commit", "commit-tree") for call in calls)


@pytest.mark.parametrize("change", ["bundle", "state", "content-tree", "inventory"])
def test_frozen_import_refuses_post_test_mutation(bundle, monkeypatch, change):
    directory, calls, state = bundle
    if change == "bundle":
        (directory / "candidate.bundle").write_bytes(b"changed")
    elif change == "state":
        state["last_promotion"]["dev_head"] = "f" * 40
    elif change == "content-tree":
        monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *_a, **_k: "f" * 40)
    else:
        (directory / "extra.json").write_text("{}")
    with pytest.raises(ValueError):
        frozen.import_candidate(directory, repo=directory, dev_sha=DEV, main_before=MAIN)
    assert not any(call[0] == "push" for call in calls)


def test_frozen_publication_consumes_the_tested_object_without_recomposing(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(promotion, "_rev_parse", lambda ref, **_k: DEV if ref == "source" else MAIN)
    monkeypatch.setattr(promotion, "_git", lambda *_a, **_k: TREE)
    monkeypatch.setattr(promotion, "_read_pipeline_state", lambda *_a, **_k: {"paused": False})
    monkeypatch.setattr(frozen, "import_candidate", lambda *_a, **_k: record())
    monkeypatch.setattr(promotion, "add_scratch_worktree", lambda commit, **_k: tmp_path / commit)
    monkeypatch.setattr(promotion, "remove_scratch_worktree", lambda *_a, **_k: observed.append("cleanup"))
    monkeypatch.setattr(artifacts, "validate_composed_artifacts", lambda *_a, **_k: observed.append("validate"))
    monkeypatch.setattr(promotion, "push_candidate", lambda commit, *a, **k: observed.append(commit))
    monkeypatch.setattr(
        promotion, "consume_pending_changes",
        lambda *_a, **_k: pytest.fail("post-test composition must never run"),
    )
    report = promotion.promote(
        repo=tmp_path, dev_ref="source", main_ref="base", push=True,
        candidate_branch="release/promote-1", promotion_time=PREPARED,
        python_artifacts_dir=tmp_path / "artifacts", frozen_candidate_dir=tmp_path / "frozen",
    )
    assert observed == ["validate", CANDIDATE, "cleanup"]
    assert report["commit"] == CANDIDATE and report["dev_head"] == DEV


def test_frozen_candidate_record_validation_does_not_mutate_input():
    data = record()
    before = copy.deepcopy(data)
    assert frozen.validate_record(data, dev_sha=DEV, main_before=MAIN) == before
    assert data == before
