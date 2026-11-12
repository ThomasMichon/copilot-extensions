"""A development source owns immutable artifacts, not its generated Git container."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

import promote_release as promotion
import promotion_artifact_readiness as readiness
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from test_promotion_artifact_readiness import HostedRelease
from test_promotion_python_artifacts import (
    DEV, MAIN, TREE, _git, _index, _publish_fixture, _repo_fixture, _write_index,
)
from test_promote_release import _commit
import promotion_source_history as history

repo = _repo_fixture


def _promoted_history(repo: Path, **changed):
    dev = _git(["rev-parse", "HEAD"], repo)
    state = repo / history.PIPELINE_STATE_PATH
    state.parent.mkdir(exist_ok=True)
    promotion = {
        "dev_head": dev, "main_before": dev, "release_id": dev, "tag": "promote-dev-" + dev,
    }
    promotion.update(changed)
    state.write_text(json.dumps({
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "last_promotion": promotion,
    }), encoding="utf-8")
    coverage = repo / history.COVERAGE_BASELINES_DIR
    coverage.mkdir(parents=True)
    (coverage / "baseline.json").write_text('{"bookkeeping":true}', encoding="utf-8")
    return dev, _commit(repo, "generated source promotion")


def test_source_channel_mapping_uses_pinned_objects_and_the_publishers_content_tree(repo, monkeypatch):
    dev, main = _promoted_history(repo)
    index = _git(["write-tree"], repo)
    # Neither a changed working file nor inherited routing may nominate the
    # source or pollute the caller's index during the content-only comparison.
    (repo / history.PIPELINE_STATE_PATH).write_text("not the pinned state", encoding="utf-8")
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo / "foreign-index"))
    monkeypatch.setenv("GIT_DIR", str(repo / "missing-git"))
    selected = history.read_promoted_source(repo, main, deadline=time.monotonic() + 30)
    assert selected == history.PromotedSource(
        main, dev, dev, history.content_tree(dev, repo=repo),
    )
    assert not (repo / "foreign-index").exists()
    monkeypatch.delenv("GIT_INDEX_FILE")
    monkeypatch.delenv("GIT_DIR")
    assert _git(["write-tree"], repo) == index
    assert promotion._tree_excluding_state(_git(["rev-parse", main + "^{tree}"], repo), repo=repo) == (
        selected.content_tree
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git path handling")
def test_source_channel_mapping_reads_long_acquired_history(repo, tmp_path):
    _, main = _promoted_history(repo)
    expected = history.read_promoted_source(repo, main, deadline=time.monotonic() + 30)
    _git(["config", "core.longpaths", "false"], repo)
    acquired = tmp_path / ("r" * max(1, 240 - len(str(tmp_path)) - 1))
    shutil.copytree(repo, acquired)
    assert max(len(str(path)) for path in (acquired / ".git" / "objects").rglob("*")) > 260
    assert history.read_promoted_source(
        acquired, main, deadline=time.monotonic() + 30,
    ) == expected


@pytest.mark.parametrize("changed", [
    {"main_before": "f" * 40}, {"release_id": "f" * 40},
    {"tag": "promote-other"}, {"dev_head": "f" * 40},
])
def test_source_channel_mapping_refuses_unbound_promotion_facts(repo, changed):
    _, main = _promoted_history(repo, **changed)
    with pytest.raises(history.SourceHistoryError, match="recorded frozen source"):
        history.read_promoted_source(repo, main)


def test_source_channel_mapping_refuses_missing_and_duplicate_state(repo):
    dev = _git(["rev-parse", "HEAD"], repo)
    with pytest.raises(history.SourceHistoryError, match="promotion provenance"):
        history.read_promoted_source(repo, dev)
    _, main = _promoted_history(repo)
    state = repo / history.PIPELINE_STATE_PATH
    content = state.read_text()
    state.write_text(content.replace('"version": 1', '"version": 1, "version": 1'))
    duplicate = _commit(repo, "duplicate promotion state")
    with pytest.raises(history.SourceHistoryError, match="duplicate source promotion"):
        history.read_promoted_source(repo, duplicate)
    with pytest.raises(history.SourceHistoryError, match="deadline"):
        history.read_promoted_source(repo, main, deadline=time.monotonic() - 1)


def _source_index(directory: Path) -> dict:
    index = json.loads((directory / artifacts.INDEX_NAME).read_text("utf-8"))
    index["schema_version"] = 2
    del index["main_before"]
    _write_index(directory, index)
    return index


def test_source_owned_index_accepts_an_equivalent_composition_in_another_container(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts"
    _index(directory)
    index = _source_index(directory)
    assert artifacts.validate_artifacts(
        directory, content_tree=TREE, dev_head=DEV, main_before="f" * 40,
    ) == index


@pytest.mark.parametrize("field", ["dev_head", "content_tree"])
def test_source_owned_index_still_rejects_changed_source_or_composition(
    tmp_path: Path, field: str,
) -> None:
    directory = tmp_path / "artifacts"
    _index(directory)
    _source_index(directory)
    expected = dict(content_tree=TREE, dev_head=DEV, main_before=MAIN)
    expected[field] = "f" * 40
    with pytest.raises(artifacts.PromotionArtifactError, match=field):
        artifacts.validate_artifacts(directory, **expected)


def test_legacy_index_retains_its_original_parent_binding(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts"
    _index(directory)
    with pytest.raises(artifacts.PromotionArtifactError, match="main_before"):
        artifacts.validate_artifacts(
            directory, content_tree=TREE, dev_head=DEV, main_before="f" * 40,
        )


def test_source_owned_index_refuses_a_second_container_identity(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts"
    _index(directory)
    index = _source_index(directory)
    index["main_before"] = MAIN
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError, match="must not bind"):
        artifacts.validate_artifacts(
            directory, content_tree=TREE, dev_head=DEV, main_before=MAIN,
        )


def test_source_release_reuses_the_same_immutable_set_across_git_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _ = _publish_fixture(tmp_path, monkeypatch)
    _source_index(directory)
    server = HostedRelease(directory)
    monkeypatch.setattr(publisher, "_gh", server.gh)
    kwargs = dict(repository="example/plugins", dev_head=DEV, repo=tmp_path)
    tag = readiness.prepare_artifacts(directory, candidate_commit=TREE, **kwargs)
    assert tag == "python-artifacts-" + DEV
    assert server.target == DEV
    writes = list(server.writes)
    assert readiness.prepare_artifacts(directory, candidate_commit="e" * 40, **kwargs) == tag
    assert server.writes == writes
    assert readiness.verify_ready_artifacts(directory, candidate_commit="e" * 40, **kwargs) == tag


def test_same_source_tag_cannot_hide_changed_artifact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _ = _publish_fixture(tmp_path, monkeypatch)
    _source_index(directory)
    server = HostedRelease(directory)
    monkeypatch.setattr(publisher, "_gh", server.gh)
    kwargs = dict(repository="example/plugins", candidate_commit=TREE, dev_head=DEV, repo=tmp_path)
    readiness.prepare_artifacts(directory, **kwargs)
    server.failure = "download-digest"
    writes = list(server.writes)
    with pytest.raises(artifacts.PromotionArtifactError, match="downloaded published asset digest"):
        readiness.prepare_artifacts(directory, **kwargs)
    assert server.writes == writes


def test_source_owned_set_keeps_legacy_version_tags_without_pipeline_state_valid(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _git(["rev-parse", "HEAD"], repo)
    state = repo / promotion.PIPELINE_STATE_PATH
    state.parent.mkdir(exist_ok=True)
    state.write_text('{"last_promotion": {}}\n', encoding="utf-8")
    current = _commit(repo, "add operational pipeline state")
    git, tree = promotion._git, promotion._tree_excluding_state
    directory, entry = _publish_fixture(tmp_path / "artifacts", monkeypatch)
    _source_index(directory)
    monkeypatch.setattr(promotion, "_git", git)
    monkeypatch.setattr(promotion, "_tree_excluding_state", tree)
    verified: list[Path] = []
    monkeypatch.setattr(publisher, "validate_source_binding", lambda manifest, root: verified.append(root))
    publisher._validate_tag_binding(
        directory, entry, tag_commit=legacy, merged_commit=current, repo=repo,
        repository="example/plugins",
    )
    assert verified, "legacy ancestry/source verification must not be bypassed"


def test_equal_source_composition_accepts_a_distinct_nonancestor_git_container(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _git(["rev-parse", "HEAD"], repo)
    state = repo / promotion.PIPELINE_STATE_PATH
    state.parent.mkdir(exist_ok=True)
    state.write_text(
        json.dumps({"last_promotion": {"dev_head": DEV, "release_id": DEV}}), encoding="utf-8",
    )
    first = _commit(repo, "first source container")
    actual_tree = _git(["rev-parse", "HEAD^{tree}"], repo)
    second = _git(["commit-tree", actual_tree, "-p", parent, "-m", "another source container"], repo)
    assert first != second
    assert not promotion._is_ancestor(first, second, cwd=repo)
    git, tree = promotion._git, promotion._tree_excluding_state
    directory, entry = _publish_fixture(tmp_path / "artifacts", monkeypatch)
    _source_index(directory)
    monkeypatch.setattr(promotion, "_git", git)
    monkeypatch.setattr(promotion, "_tree_excluding_state", tree)
    publisher._validate_tag_binding(
        directory, entry, tag_commit=first, merged_commit=second, repo=repo,
        repository="example/plugins",
    )
