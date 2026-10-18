"""Prepublished version aliases survive squash landings and failed Main merges."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import build_python_artifacts as builder
import promote_release as promotion
import promotion_artifact_readiness as readiness
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from promotion_source_alias import validate_prior_source_alias
from test_promotion_python_artifacts import (
    _STATIC_BUILD, _complete_manifest, _git, _wheel_bytes, _write_index,
)
from test_publish_candidate_artifacts import PublicationService


def _plugin(repo: Path, name: str, version: str, value: str) -> None:
    root = repo / "plugins" / name
    (root / "src" / name).mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        _STATIC_BUILD + f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8",
    )
    (root / "src" / name / "__init__.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", message], repo)
    return _git(["rev-parse", "HEAD"], repo)


def _candidate(repo: Path, dev: str, parent: str, *, parent_claim: str | None = None) -> str:
    state = repo / promotion.PIPELINE_STATE_PATH
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({
        "schema": promotion.PIPELINE_STATE_SCHEMA, "version": 1, "paused": False,
        "last_promotion": {
            "dev_head": dev, "release_id": dev,
            "main_before": parent if parent_claim is None else parent_claim, "tag": f"promote-dev-{dev}",
        },
    }), encoding="utf-8")
    _git(["add", "-A"], repo)
    tree = _git(["write-tree"], repo)
    candidate = _git(["commit-tree", tree, "-p", parent, "-m", "qualified candidate"], repo)
    _git(["read-tree", "HEAD"], repo)
    state.unlink()
    return candidate


def _artifact_set(repo: Path, directory: Path, *, dev: str, candidate: str, control: str) -> dict:
    directory.mkdir()
    trusted = promotion._load_module(Path(builder.__file__), "source_alias_real_builder")
    trusted.REPO = repo
    entries = []
    for name, version in artifacts.candidate_projects(repo).items():
        root = repo / "plugins" / name
        wheels = directory.parent / f"{directory.name}-{name}-wheels"
        wheels.mkdir()
        wheel = wheels / f"{name}-{version}-py3-none-any.whl"
        wheel.write_bytes(_wheel_bytes(
            name, version, payload=(root / "src" / name / "__init__.py").read_bytes(),
        ))
        manifest = _complete_manifest({
            "schema": "copilot-extensions.python-artifact-manifest", "schema_version": 3,
            "plugin": name, "version": version,
            "payload_hash": trusted.compute_payload_hash([root]),
            "wheels": [{
                "filename": wheel.name, "role": "plugin", "sha256": "sha256:" + artifacts._digest(wheel),
            }],
        })
        entries.append(artifacts.bundle_wheels(manifest, wheels, directory))
    tree = promotion._tree_excluding_state(
        _git(["rev-parse", candidate + "^{tree}"], repo), repo=repo,
    )
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 2, "dev_head": dev,
        "content_tree": tree, "artifacts": entries,
        "admission": {
            "signer_digest": control, "source_digest": control, "source_ref": "refs/heads/main",
        },
    }
    _write_index(directory, index)
    artifacts.validate_artifacts(
        directory, dev_head=dev, content_tree=tree, main_before=control,
        candidate_root=repo, projects=artifacts.candidate_projects(repo),
    )
    return index


@pytest.fixture
def publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def prepare(*, landed: bool, changed_demo: bool = False) -> dict:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init", "-q", "-b", "main"], repo)
        _git(["config", "user.name", "Test"], repo)
        _git(["config", "user.email", "test@example.com"], repo)
        _git(["config", "core.autocrlf", "false"], repo)
        (repo / "README.md").write_text("publication fixture\n", encoding="utf-8")
        base = _commit(repo, "Main control")
        _git(["checkout", "-qb", "dev"], repo)
        _plugin(repo, "demo", "1.0", "unchanged plugin")
        _plugin(repo, "other", "1.0", "first other plugin")
        dev1 = _commit(repo, "development release one")
        candidate1 = _candidate(repo, dev1, base)
        first_dir = tmp_path / "first-artifacts"
        first_index = _artifact_set(
            repo, first_dir, dev=dev1, candidate=candidate1, control=base,
        )
        service = PublicationService()
        monkeypatch.setattr(publisher, "_gh", service.gh)
        readiness.prepare_artifacts(
            first_dir, repository="example/plugins", candidate_commit=candidate1,
            dev_head=dev1, repo=repo,
        )
        publisher.publish_artifacts(
            first_dir, repository="example/plugins", merged_commit=candidate1,
            candidate_commit=candidate1, dev_head=dev1, repo=repo,
        )
        if landed:
            main = _git(
                ["commit-tree", _git(["rev-parse", candidate1 + "^{tree}"], repo),
                 "-p", base, "-m", "squash landing"], repo,
            )
            service.tags[f"promote-dev-{dev1}"] = main
            _git(["update-ref", "refs/heads/main", main], repo)
        else:
            main = base
            assert f"promote-dev-{dev1}" not in service.tags
        _plugin(repo, "other", "1.1", "changed other plugin")
        if changed_demo:
            _plugin(repo, "demo", "1.0", "changed without a version bump")
        dev2 = _commit(repo, "development release two")
        candidate2 = _candidate(repo, dev2, main)
        assert not promotion._is_ancestor(candidate1, candidate2, cwd=repo)
        second_dir = tmp_path / "second-artifacts"
        second_index = _artifact_set(
            repo, second_dir, dev=dev2, candidate=candidate2, control=base,
        )
        readiness.prepare_artifacts(
            second_dir, repository="example/plugins", candidate_commit=candidate2,
            dev_head=dev2, repo=repo,
        )
        return dict(
            repo=repo, base=base, dev1=dev1, dev2=dev2, candidate1=candidate1, candidate2=candidate2,
            first_dir=first_dir, first_index=first_index, directory=second_dir,
            index=second_index, service=service,
        )
    return prepare


def _publish(state: dict, cache: dict | None = None) -> None:
    publisher.publish_artifacts(
        state["directory"], repository="example/plugins", merged_commit=state["candidate2"],
        candidate_commit=state["candidate2"], dev_head=state["dev2"], repo=state["repo"],
        admission_cache=cache,
    )


@pytest.mark.parametrize("landed", [False, True], ids=["failed-main-merge", "squash-main-landing"])
def test_two_releases_reuse_unchanged_version_alias_without_a_main_commit_semantic_key(
    publication, landed: bool,
) -> None:
    state = publication(landed=landed)
    service = state["service"]
    previous = list(service.releases["demo-v1.0"]["assets"])
    cache: dict = {}
    _publish(state, cache)
    publisher.verify_published_artifacts(
        state["directory"], repository="example/plugins", candidate_commit=state["candidate2"],
        dev_head=state["dev2"], repo=state["repo"], admission_cache=cache,
    )
    assert service.tags["demo-v1.0"] == state["candidate1"]
    assert service.releases["demo-v1.0"]["assets"] == previous
    assert service.tags["other-v1.1"] == state["candidate2"]
    subjects = [argv for argv, _ in service.calls if argv[:2] == ["attestation", "verify"]]
    assert len(subjects) == 1, "cache one authenticated index, not repeated per-plugin network proofs"
    assert subjects[0][subjects[0].index("--signer-digest") + 1] == state["base"]
    assert set(cache) == {("example/plugins", state["dev1"])}


@pytest.mark.parametrize("failure", ["raw-index", "signature", "composition", "missing-index"])
def test_failed_prior_source_authentication_refuses_without_new_version_writes(
    publication, failure: str,
) -> None:
    state = publication(landed=False)
    service = state["service"]
    old_release = service.releases[f"python-artifacts-{state['dev1']}"]
    entry = next(asset for asset in old_release["assets"] if asset["name"] == artifacts.INDEX_NAME)
    asset = service.assets[entry["id"]]
    if failure == "raw-index":
        asset["payload"] += b"\n"
    elif failure == "signature":
        service.failure = "attestation"
    elif failure == "composition":
        index = json.loads(asset["payload"])
        index["content_tree"] = "f" * 40
        asset["payload"] = json.dumps(index).encode()
        entry["digest"] = asset["digest"] = "sha256:" + hashlib.sha256(asset["payload"]).hexdigest()
    else:
        del service.releases[f"python-artifacts-{state['dev1']}"]
    before = list(service.writes)
    with pytest.raises(artifacts.PromotionArtifactError):
        _publish(state)
    assert service.writes == before
    assert "other-v1.1" not in service.tags


def test_prior_signed_composition_does_not_waive_actual_unchanged_plugin_source(publication) -> None:
    state = publication(landed=False, changed_demo=True)
    before = list(state["service"].writes)
    with pytest.raises(artifacts.PromotionArtifactError, match="payload hash differs"):
        _publish(state)
    assert state["service"].writes == before


@pytest.mark.parametrize("failure", ["unrelated-source", "wrong-parent", "multiple-parents"])
def test_alias_requires_a_real_single_parent_candidate_in_the_admitted_source_history(
    publication, failure: str,
) -> None:
    state = publication(landed=False)
    root = state["repo"]
    if failure == "unrelated-source":
        unrelated = _git(
            ["commit-tree", _git(["rev-parse", state["dev1"] + "^{tree}"], root),
             "-p", state["base"], "-m", "unrelated development history"], root,
        )
        previous = _candidate(root, unrelated, state["base"])
    elif failure == "wrong-parent":
        previous = _candidate(root, state["dev1"], state["base"], parent_claim=state["dev1"])
    else:
        previous = _git(
            ["commit-tree", _git(["rev-parse", state["candidate1"] + "^{tree}"], root),
             "-p", state["base"], "-p", state["dev1"], "-m", "not a single-parent candidate"], root,
        )
    state["service"].tags["demo-v1.0"] = previous
    before = list(state["service"].writes)
    with pytest.raises(artifacts.PromotionArtifactError):
        _publish(state)
    assert state["service"].writes == before


def test_current_raw_readback_is_still_required_after_prior_alias_authentication(publication) -> None:
    state = publication(landed=False)
    cache: dict = {}
    _publish(state, cache)
    state["service"].failure = "digest"
    with pytest.raises(artifacts.PromotionArtifactError, match="downloaded published asset digest"):
        publisher.verify_published_artifacts(
            state["directory"], repository="example/plugins", candidate_commit=state["candidate2"],
            dev_head=state["dev2"], repo=state["repo"], admission_cache=cache,
        )


def test_cached_source_index_is_not_blanket_authorization_for_another_candidate_composition(publication) -> None:
    state = publication(landed=False)
    cache: dict = {}
    _publish(state, cache)
    root = state["repo"]
    (root / "unadmitted-control.txt").write_text("different composition\n", encoding="utf-8")
    changed = _candidate(root, state["dev1"], state["base"])
    with pytest.raises(artifacts.PromotionArtifactError, match="composition differs"):
        validate_prior_source_alias(
            repository="example/plugins", tag_commit=changed, current_commit=state["candidate2"],
            current_index=state["index"], repo=root, cache=cache,
        )
