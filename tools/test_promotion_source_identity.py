"""A development source owns immutable artifacts, not its generated Git container."""
from __future__ import annotations

import json
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

repo = _repo_fixture


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
    with pytest.raises(artifacts.PromotionArtifactError, match="downloaded readiness asset digest"):
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
