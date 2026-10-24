"""Pure release approval controls; all identities and attestations are synthetic."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

import pytest

import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from runtime_profile import ProfileError, validate_profile
from runtime_profile_cases import _record
from runtime_profile_release import (
    freeze_release_profiles, promotion_profiles, select_release_profile,
)


def _index(record=None):
    return {
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "d" * 40, "content_tree": "c" * 40, "artifacts": [],
        "admission": {
            "signer_digest": "a" * 40, "source_digest": "a" * 40,
            "source_ref": "refs/heads/main",
        },
        "runtime_profiles": [_record() if record is None else record],
    }


def _select(path, **overrides):
    record = _record()
    expected = {
        "repository": "example/plugins", "signer_digest": "a" * 40,
        "dev_head": "d" * 40, "content_tree": "c" * 40,
        "approval_source": record["approval_source"], "release": record["release"],
        "target": record["target"], "component": record["component"],
        "roots": record["roots"], "mode": record["mode"],
    }
    return select_release_profile(path, **(expected | overrides))


class TestReleaseRuntimeProfile:
    def test_producer_metadata_is_detached_but_not_approved(self):
        record = _record()
        frozen = freeze_release_profiles([record])
        record["backend"] = "cuda"
        assert frozen == [_record()]
        with pytest.raises(ProfileError, match="duplicate"):
            freeze_release_profiles([_record(), _record()])

    @pytest.mark.parametrize("value", [None, {}, True, "approved", [_record(), {}]])
    def test_malformed_producer_metadata_refuses(self, value):
        with pytest.raises(ProfileError):
            freeze_release_profiles(value)

    def test_profile_metadata_requires_source_owned_schema(self):
        for version in (1, True, 3):
            with pytest.raises(artifacts.PromotionArtifactError, match="runtime profiles"):
                promotion_profiles([], schema_version=version)

    @pytest.mark.parametrize("kind", ["unchanged", "legacy", "invalid", "duplicate"])
    def test_existing_artifact_validator_checks_optional_profile_metadata(self, tmp_path, kind):
        index = _index()
        if kind == "unchanged":
            index.pop("runtime_profiles")
        elif kind == "legacy":
            index["schema_version"] = 1
            index["main_before"] = "b" * 40
        elif kind == "invalid":
            index["runtime_profiles"][0]["approved"] = True
        else:
            index["runtime_profiles"] *= 2
        (tmp_path / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
        kwargs = dict(
            content_tree="c" * 40, dev_head="d" * 40, main_before="b" * 40,
        )
        if kind == "unchanged":
            assert artifacts.validate_artifacts(tmp_path, **kwargs) == index
        else:
            with pytest.raises(artifacts.PromotionArtifactError, match="runtime profiles"):
                artifacts.validate_artifacts(tmp_path, **kwargs)

    @pytest.mark.parametrize("conflict", [False, True])
    def test_export_cannot_rewrite_an_existing_source_profile_set(self, tmp_path, monkeypatch, conflict):
        index = _index()
        profiles = deepcopy(index["runtime_profiles"])
        if conflict:
            index["runtime_profiles"][0]["acquisition_policy"] = "different-policy"
        removed = []
        monkeypatch.setitem(sys.modules, "promote_release", SimpleNamespace(
            add_scratch_worktree=lambda *a, **kw: tmp_path,
            remove_scratch_worktree=lambda path, **kw: removed.append(path),
        ))
        monkeypatch.setitem(sys.modules, "reuse_promotion_artifacts", SimpleNamespace(
            reuse_artifacts=lambda *a, **kw: deepcopy(index),
        ))
        monkeypatch.setattr(artifacts, "_run", lambda *a, **kw: "")
        monkeypatch.setattr(artifacts, "candidate_projects", lambda *a: {})
        output = tmp_path / "export"
        kwargs = dict(
            repo=tmp_path, commit="d" * 40, content_tree="c" * 40,
            dev_head="d" * 40, main_before="b" * 40, out_dir=output,
            reuse_repository="example/plugins", runtime_profiles=profiles,
        )
        if conflict:
            with pytest.raises(artifacts.PromotionArtifactError, match="runtime profiles differ"):
                artifacts.export_artifacts(**kwargs)
            assert not output.exists()
        else:
            assert artifacts.export_artifacts(**kwargs) == index
            assert output.is_dir()
        assert removed == [tmp_path]
        assert not list(tmp_path.glob("python-artifacts-*"))

    def test_export_enforces_existing_index_byte_limit_before_publication(self, tmp_path, monkeypatch):
        record = _record()
        record["target"]["environment"]["platform_version"] = "x" * artifacts._MANIFEST_LIMIT
        removed = []
        monkeypatch.setitem(sys.modules, "promote_release", SimpleNamespace(
            add_scratch_worktree=lambda *a, **kw: tmp_path,
            remove_scratch_worktree=lambda path, **kw: removed.append(path),
        ))
        monkeypatch.setattr(artifacts, "candidate_projects", lambda *a: {})

        def run(argv, **kwargs):
            if "--out-dir" in argv:
                output = Path(argv[argv.index("--out-dir") + 1])
                (output / artifacts._BUNDLES_NAME).write_text("[]", encoding="utf-8")
            return ""

        monkeypatch.setattr(artifacts, "_run", run)
        output = tmp_path / "export"
        with pytest.raises(artifacts.PromotionArtifactError, match="size limit"):
            artifacts.export_artifacts(
                repo=tmp_path, commit="d" * 40, content_tree="c" * 40,
                dev_head="d" * 40, main_before="b" * 40, out_dir=output,
                runtime_profiles=[record],
            )
        assert not output.exists() and removed == [tmp_path]
        assert not list(tmp_path.glob("python-artifacts-*"))

    @pytest.mark.parametrize("offset", [-1, 0, 1], ids=["below-limit", "exact-limit", "over-limit"])
    def test_export_measures_the_exact_published_bytes(self, tmp_path, monkeypatch, offset):
        record = _record()
        expected = _index(record)
        size = len((json.dumps(expected, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        record["target"]["environment"]["platform_version"] += "x" * (
            artifacts._MANIFEST_LIMIT + offset - size
        )
        monkeypatch.setitem(sys.modules, "promote_release", SimpleNamespace(
            add_scratch_worktree=lambda *a, **kw: tmp_path,
            remove_scratch_worktree=lambda *a, **kw: None,
            _is_ancestor=lambda *a, **kw: True,
        ))
        monkeypatch.setitem(sys.modules, "reuse_promotion_artifacts", SimpleNamespace(
            reuse_artifacts=lambda *a, **kw: None,
        ))
        monkeypatch.setattr(artifacts, "candidate_projects", lambda *a: {})
        monkeypatch.setenv("GITHUB_WORKFLOW_SHA", "a" * 40)
        monkeypatch.setenv("GITHUB_SHA", "a" * 40)
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")

        def run(argv, **kwargs):
            if "--out-dir" in argv:
                output = Path(argv[argv.index("--out-dir") + 1])
                (output / artifacts._BUNDLES_NAME).write_text("[]", encoding="utf-8")
            return ""

        monkeypatch.setattr(artifacts, "_run", run)
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        output = tmp_path / "export"
        kwargs = dict(
            repo=tmp_path, commit="d" * 40, content_tree="c" * 40,
            dev_head="d" * 40, main_before="b" * 40, out_dir=output,
            reuse_repository="example/plugins", runtime_profiles=[record],
        )
        if offset > 0:
            with pytest.raises(artifacts.PromotionArtifactError, match="size limit"):
                artifacts.export_artifacts(**kwargs)
            assert not output.exists()
        else:
            index = artifacts.export_artifacts(**kwargs)
            path = output / artifacts.INDEX_NAME
            published = path.read_bytes()
            assert len(published) == artifacts._MANIFEST_LIMIT + offset
            assert b"\r\n" not in published and json.loads(published) == index
            assert _select(path, target=record["target"]).profile == validate_profile(record)
        assert not list(tmp_path.glob("python-artifacts-*"))

    def test_export_retains_profiles_before_build_activity(self, tmp_path, monkeypatch):
        record = _record()
        monkeypatch.setitem(sys.modules, "promote_release", SimpleNamespace(
            add_scratch_worktree=lambda *a, **kw: tmp_path,
            remove_scratch_worktree=lambda *a, **kw: None,
        ))
        monkeypatch.setattr(artifacts, "candidate_projects", lambda *a: {})

        def run(argv, **kwargs):
            record["acquisition_policy"] = "changed-after-retention"
            if "--out-dir" in argv:
                output = Path(argv[argv.index("--out-dir") + 1])
                (output / artifacts._BUNDLES_NAME).write_text("[]", encoding="utf-8")
            return ""

        monkeypatch.setattr(artifacts, "_run", run)
        output = tmp_path / "export"
        index = artifacts.export_artifacts(
            repo=tmp_path, commit="d" * 40, content_tree="c" * 40,
            dev_head="d" * 40, main_before="b" * 40, out_dir=output,
            runtime_profiles=[record],
        )
        assert index["runtime_profiles"] == [_record()]
        assert json.loads((output / artifacts.INDEX_NAME).read_text()) == index
        assert "admission" not in index

    def test_existing_pinned_main_verifier_receives_retained_exact_subject(self, tmp_path, monkeypatch):
        path = tmp_path / artifacts.INDEX_NAME
        subject = json.dumps(_index()).encode()
        path.write_bytes(subject)
        retained = []

        def verify(argv, **kwargs):
            selected = Path(argv[2])
            retained.append(selected)
            assert selected != path and selected.read_bytes() == subject
            assert argv[:2] == ["attestation", "verify"]
            assert argv[argv.index("--repo") + 1] == "example/plugins"
            assert argv[argv.index("--signer-workflow") + 1] == (
                "example/plugins/.github/workflows/validate-and-promote.yml"
            )
            assert argv[argv.index("--signer-digest") + 1] == "a" * 40
            assert argv[argv.index("--source-digest") + 1] == "a" * 40
            assert argv[argv.index("--source-ref") + 1] == "refs/heads/main"
            assert "--deny-self-hosted-runners" in argv
            assert kwargs == {"verification": True}
            path.write_text("original input changed after retention", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "verified", "")

        monkeypatch.setattr(publisher, "_gh", verify)
        selected = _select(path)
        assert selected.profile == validate_profile(_record())
        assert selected.approval_source == _record()["approval_source"]
        assert len(retained) == 1 and not retained[0].parent.exists()

    @pytest.mark.parametrize("failure", ["signature", "retained-mutation"])
    def test_signature_failure_or_subject_mutation_never_approves(self, tmp_path, monkeypatch, failure):
        path = tmp_path / artifacts.INDEX_NAME
        path.write_text(json.dumps(_index()), encoding="utf-8")
        retained = []

        def verify(argv, **kwargs):
            selected = Path(argv[2])
            retained.append(selected)
            if failure == "retained-mutation":
                selected.write_text(json.dumps(_index(_record() | {"backend": "other"})))
            return subprocess.CompletedProcess(
                argv, 1 if failure == "signature" else 0, "", "invalid attestation",
            )

        monkeypatch.setattr(publisher, "_gh", verify)
        with pytest.raises((artifacts.PromotionArtifactError, ProfileError)):
            _select(path)
        assert len(retained) == 1 and not retained[0].parent.exists()

    @pytest.mark.parametrize("field,value", [
        ("schema", "other"), ("schema_version", 1), ("schema_version", True),
        ("artifacts", None),
        ("dev_head", "e" * 40), ("content_tree", "f" * 40),
        ("main_before", "b" * 40),
        ("admission", {"signer_digest": "b" * 40, "source_digest": "b" * 40,
                       "source_ref": "refs/heads/main"}),
    ])
    def test_signed_but_wrong_release_or_admission_refuses(
        self, tmp_path, monkeypatch, field, value,
    ):
        path = tmp_path / artifacts.INDEX_NAME
        index = _index()
        index[field] = value
        path.write_text(json.dumps(index), encoding="utf-8")
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        with pytest.raises(ProfileError, match="selected release"):
            _select(path)

    @pytest.mark.parametrize("kind", ["missing", "foreign-source", "wrong-target", "wrong-release"])
    def test_approval_does_not_replace_source_or_selection_checks(self, tmp_path, monkeypatch, kind):
        path = tmp_path / artifacts.INDEX_NAME
        index = _index()
        if kind == "missing":
            index.pop("runtime_profiles")
        elif kind == "foreign-source":
            index["runtime_profiles"][0]["approval_source"] = "another-authority"
        elif kind == "wrong-target":
            index["runtime_profiles"][0]["target"]["environment"]["platform_version"] = "different"
        else:
            index["runtime_profiles"][0]["release"]["sha256"] = "sha256:" + "9" * 64
        path.write_text(json.dumps(index), encoding="utf-8")
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        with pytest.raises(ProfileError):
            _select(path)

    @pytest.mark.parametrize("overrides", [
        {"signer_digest": "main"}, {"dev_head": True}, {"content_tree": "bad"},
        {"approval_source": "https://untrusted.example"},
    ])
    def test_expected_authority_is_not_inferred_from_index(self, tmp_path, monkeypatch, overrides):
        path = tmp_path / artifacts.INDEX_NAME
        path.write_text(json.dumps(_index()), encoding="utf-8")
        calls = []
        monkeypatch.setattr(publisher, "_gh", lambda *a, **kw: calls.append(a))
        with pytest.raises(ProfileError):
            _select(path, **overrides)
        assert calls == []

    @pytest.mark.parametrize("kind", ["directory", "oversize", "invalid-json"])
    def test_unsafe_or_malformed_subject_refuses(self, tmp_path, monkeypatch, kind):
        path = tmp_path / artifacts.INDEX_NAME
        if kind == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"x" * (artifacts._MANIFEST_LIMIT + 1) if kind == "oversize" else b"{")
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        with pytest.raises(ProfileError):
            _select(path)

    def test_exact_existing_subject_limit_is_accepted(self, tmp_path, monkeypatch):
        path = tmp_path / artifacts.INDEX_NAME
        subject = json.dumps(_index()).encode()
        path.write_bytes(subject + b" " * (artifacts._MANIFEST_LIMIT - len(subject)))
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        assert _select(path).profile == validate_profile(_record())

    def test_core_selector_still_owns_ambiguity_and_capability_retention(self, tmp_path, monkeypatch):
        path = tmp_path / artifacts.INDEX_NAME
        index = _index()
        second = deepcopy(_record())
        second["wheels"][0]["sha256"] = "sha256:" + "8" * 64
        index["runtime_profiles"].append(second)
        path.write_text(json.dumps(index), encoding="utf-8")
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        with pytest.raises(ProfileError, match="ambiguous"):
            _select(path)
        preferred = validate_profile(second)
        assert _select(path, preference=(preferred.lock_id,)).profile == preferred
        with pytest.raises(ProfileError, match="migration"):
            _select(path, existing_installation=True)
        with pytest.raises(ProfileError, match="unavailable"):
            _select(path, retained_backend="cuda-12")

    def test_artifact_approval_is_not_source_mode_or_artifact_integrity(self, tmp_path, monkeypatch):
        record = _record()
        record.update(mode="artifact", artifact={
            "artifact_id": "sha256:" + "3" * 64,
            "manifest_sha256": "sha256:" + "4" * 64,
        })
        path = tmp_path / artifacts.INDEX_NAME
        path.write_text(json.dumps(_index(record)), encoding="utf-8")
        monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "verified", "",
        ))
        assert _select(path, mode="artifact").profile == validate_profile(record)
        with pytest.raises(ProfileError, match="unavailable"):
            _select(path)
        assert list(tmp_path.iterdir()) == [path]
