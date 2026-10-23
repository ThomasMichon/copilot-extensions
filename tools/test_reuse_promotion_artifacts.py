"""Frozen source reuse retains signatures, source binding and exact admitted locks."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import promote_release as promotion
import promotion_artifact_readiness as readiness
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
import reuse_promotion_artifacts as reuse
from test_promotion_artifact_readiness import HostedRelease
from test_promotion_python_artifacts import DEV, MAIN, TREE, _publish_fixture, _write_index


@pytest.fixture
def frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    directory, _ = _publish_fixture(tmp_path, monkeypatch)
    index = json.loads((directory / artifacts.INDEX_NAME).read_text("utf-8"))
    index["schema_version"] = 2
    del index["main_before"]
    index["admission"] = {
        "signer_digest": MAIN, "source_digest": MAIN, "source_ref": "refs/heads/main",
    }
    _write_index(directory, index)
    server = HostedRelease(directory)
    monkeypatch.setattr(publisher, "_gh", server.gh)
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *args, **kwargs: True)
    attestations: list[dict] = []

    def verify(directory: Path, **kwargs: str) -> None:
        attestations.append(kwargs)
        assert (directory / artifacts.INDEX_NAME).read_bytes() == (
            server.directory / artifacts.INDEX_NAME
        ).read_bytes()

    monkeypatch.setattr(publisher, "verify_attestations", verify)
    monkeypatch.setattr(publisher, "verify_attestation_subject", lambda *args, **kwargs: None)
    readiness.prepare_artifacts(
        directory, repository="example/plugins", candidate_commit=TREE, dev_head=DEV, repo=tmp_path,
    )
    restored = tmp_path / "restored"
    restored.mkdir()
    kwargs = dict(
        repository="example/plugins", dev_head=DEV, main_before="f" * 40,
        content_tree=TREE, candidate_root=tmp_path, repo=tmp_path,
        source_policy="governed", min_age_days=None,
    )
    return directory, restored, index, server, attestations, kwargs


def test_reuses_the_original_signed_index_and_lock_without_another_build_or_publication(frozen: tuple) -> None:
    directory, restored, index, server, attestations, kwargs = frozen
    writes = list(server.writes)
    assert reuse.reuse_artifacts(restored, **kwargs) == index
    assert server.writes == writes
    assert {path.name for path in restored.iterdir()} == {path.name for path in directory.iterdir()}
    assert all(path.read_bytes() == (directory / path.name).read_bytes() for path in restored.iterdir())
    assert attestations == [{
        "repository": "example/plugins", "signer_digest": MAIN,
        "source_digest": MAIN, "source_ref": "refs/heads/main",
    }]
    assert all(options.get("verification") for _, options in server.calls if options.get("binary"))


def test_real_export_reuses_before_running_a_builder_or_resolving_a_toolchain(
    frozen: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _, index, server, _, kwargs = frozen

    def forbid_build(argv, *, cwd) -> str:
        if argv[:4] == ["git", f"--attr-source={TREE}", "checkout-index", "--all"]:
            assert cwd == kwargs["repo"] and argv[4].endswith(os.sep)
            candidate = Path(argv[4].removeprefix("--prefix="))
            shutil.copytree(cwd / "plugins", candidate / "plugins")
            return ""
        pytest.fail("an admitted source must not resolve/build another toolchain")

    monkeypatch.setattr(artifacts, "_run", forbid_build)
    destination = directory.parent / "reused-export"
    writes = list(server.writes)
    assert artifacts.export_artifacts(
        repo=kwargs["repo"], commit=TREE, content_tree=TREE, dev_head=DEV,
        main_before=kwargs["main_before"], out_dir=destination,
        reuse_repository="example/plugins",
    ) == index
    assert server.writes == writes
    assert (destination / artifacts.INDEX_NAME).read_bytes() == (directory / artifacts.INDEX_NAME).read_bytes()


def test_only_actual_absence_allows_a_new_source_build(frozen: tuple) -> None:
    _, restored, _, server, attestations, kwargs = frozen
    server.release = None
    assert reuse.reuse_artifacts(restored, **kwargs) is None
    assert not list(restored.iterdir()) and not attestations


@pytest.mark.parametrize("failure", [
    "permission", "download", "download-digest", "missing-bundle", "tag",
])
def test_unavailable_or_conflicting_frozen_evidence_never_falls_back_to_a_new_lock(
    frozen: tuple, failure: str,
) -> None:
    _, restored, _, server, _, kwargs = frozen
    if failure == "missing-bundle":
        name = next(name for name in server.assets if name != artifacts.INDEX_NAME)
        del server.assets[name]
    elif failure == "tag":
        server.target = "e" * 40
    else:
        server.failure = failure
    writes = list(server.writes)
    with pytest.raises(artifacts.PromotionArtifactError):
        reuse.reuse_artifacts(restored, **kwargs)
    assert server.writes == writes


@pytest.mark.parametrize("field", ["dev_head", "content_tree", "source_policy"])
def test_source_composition_and_governance_are_still_required(frozen: tuple, field: str) -> None:
    _, restored, _, server, _, kwargs = frozen
    kwargs[field] = "seasoned-public" if field == "source_policy" else "e" * 40
    writes = list(server.writes)
    with pytest.raises(artifacts.PromotionArtifactError):
        reuse.reuse_artifacts(restored, **kwargs)
    assert server.writes == writes


def test_a_non_main_original_signer_is_not_an_admitted_reuse_candidate(
    frozen: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, restored, _, _, attestations, kwargs = frozen
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *args, **kwargs: False)
    with pytest.raises(artifacts.PromotionArtifactError, match="not trusted Main history"):
        reuse.reuse_artifacts(restored, **kwargs)
    assert not attestations


def test_attestation_failure_cannot_be_laundered_by_a_fresh_signature_or_rebuild(
    frozen: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, restored, _, server, _, kwargs = frozen

    def refused(*args: object, **kwargs: object) -> None:
        raise artifacts.PromotionArtifactError("original source attestation refused")

    monkeypatch.setattr(publisher, "verify_attestations", refused)
    writes = list(server.writes)
    with pytest.raises(artifacts.PromotionArtifactError, match="original source attestation"):
        reuse.reuse_artifacts(restored, **kwargs)
    assert server.writes == writes


def test_index_only_evidence_is_authenticated_before_any_old_bundle_is_consumed(
    frozen: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, restored, _, server, attestations, kwargs = frozen
    subjects: list[str] = []

    def verify(path: Path, **identity: str) -> None:
        subjects.append(path.name)
        assert identity["signer_digest"] == MAIN and identity["source_digest"] == MAIN
        assert identity["source_ref"] == "refs/heads/main"

    monkeypatch.setattr(publisher, "verify_attestation_subject", verify)
    arguments = {
        key: value for key, value in kwargs.items()
        if key not in ("candidate_root", "source_policy", "min_age_days")
    }
    assert reuse.read_admitted_source_index(restored, **arguments)
    assert subjects == [artifacts.INDEX_NAME]
    assert {path.name for path in restored.iterdir()} == {artifacts.INDEX_NAME}
    assert not attestations, "index-only source proof must not claim old bundle verification"
    downloaded = [call for call, options in server.calls if options.get("binary")]
    assert downloaded[-1][1].endswith("/" + str(server.assets[artifacts.INDEX_NAME]["id"]))


def test_a_refused_prior_index_signature_cannot_authorize_source_alias_reuse(
    frozen: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, restored, _, server, _, kwargs = frozen

    def refuse(*args: object, **kwargs: object) -> None:
        raise artifacts.PromotionArtifactError("prior index signature refused")

    monkeypatch.setattr(publisher, "verify_attestation_subject", refuse)
    arguments = {
        key: value for key, value in kwargs.items()
        if key not in ("candidate_root", "source_policy", "min_age_days")
    }
    before = list(server.writes)
    with pytest.raises(artifacts.PromotionArtifactError, match="prior index signature"):
        reuse.read_admitted_source_index(restored, **arguments)
    assert server.writes == before


@pytest.mark.parametrize("name", ["../escaped.zip", "C:escaped.zip", "CON.zip", "extra.json", ".hidden"])
def test_untrusted_asset_names_cannot_select_an_output_path(frozen: tuple, name: str) -> None:
    _, restored, _, server, _, kwargs = frozen
    server.assets[name] = {
        "name": name, "id": 200, "state": "uploaded", "digest": "sha256:" + "f" * 64,
        "payload": b"not admitted",
    }
    with pytest.raises(artifacts.PromotionArtifactError, match="unsafe or incomplete"):
        reuse.reuse_artifacts(restored, **kwargs)
    assert not list(restored.iterdir())


def test_reuse_checks_the_complete_actual_runtime_project_set(frozen: tuple) -> None:
    _, restored, _, _, _, kwargs = frozen
    added = kwargs["candidate_root"] / "plugins" / "another"
    added.mkdir()
    (added / "pyproject.toml").write_text(
        '[project]\nname = "another"\nversion = "1.0"\n', encoding="utf-8",
    )
    with pytest.raises(artifacts.PromotionArtifactError, match="candidate"):
        reuse.reuse_artifacts(restored, **kwargs)
