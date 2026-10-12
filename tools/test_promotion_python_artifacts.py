"""Promotion composition, transfer integrity, signing and immutable publication."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote_release as promotion
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from test_promote_release import (
    _commit, _git, no_ambient_git_identity as _identity_fixture, repo as _repo_fixture,
)

repo = _repo_fixture
no_ambient_git_identity = _identity_fixture

DEV, MAIN, TREE = "a" * 40, "b" * 40, "c" * 40


def _manifest(directory: Path, *, role: str = "plugin") -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "demo-1.0-py3-none-any.whl"
    wheel.write_bytes(b"first-party wheel")
    return {
        "plugin": "demo", "version": "1.0", "artifact_id": "sha256:" + "d" * 64,
        "wheels": [{
            "filename": wheel.name, "role": role, "sha256": f"sha256:{artifacts._digest(wheel)}",
        }],
    }


def _index(directory: Path) -> dict:
    directory.mkdir()
    manifest = _manifest(directory.parent / "wheels")
    entry = artifacts.bundle_wheels(manifest, directory.parent / "wheels", directory)
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 1,
        "dev_head": DEV, "main_before": MAIN, "content_tree": TREE, "artifacts": [entry],
    }
    _write_index(directory, index)
    return index


def _write_index(directory: Path, index: dict) -> None:
    (directory / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")


def _validate(directory: Path, **kwargs) -> dict:
    return artifacts.validate_artifacts(
        directory, content_tree=TREE, dev_head=DEV, main_before=MAIN, **kwargs
    )


def test_bundle_and_transfer_roundtrip(tmp_path):
    directory = tmp_path / "export"
    index = _index(directory)
    assert _validate(directory, projects={"demo": "1.0"}) == index
    entry = index["artifacts"][0]
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        assert set(bundle.namelist()) == {"manifest.json", "demo-1.0-py3-none-any.whl"}
        assert json.loads(bundle.read("manifest.json"))["plugin"] == "demo"
        assert bundle.read("demo-1.0-py3-none-any.whl") == b"first-party wheel"


@pytest.mark.parametrize("payload", [b"", b"chunked digest" * 200000], ids=["empty", "multi-chunk"])
def test_digest_matches_sha256_across_multiple_chunks(tmp_path, payload):
    path = tmp_path / "data.bin"
    path.write_bytes(payload)
    assert artifacts._digest(path) == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("field", ["dev_head", "main_before", "content_tree"])
def test_transfer_rejects_another_composition(tmp_path, field):
    directory = tmp_path / "export"
    index = _index(directory)
    index[field] = "e" * 40
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError, match=f"{field} mismatch"):
        _validate(directory)


@pytest.mark.parametrize("mutation", [
    "digest", "filename", "plugin", "version", "identity", "duplicate", "extra",
    "missing", "bool-schema", "list-root", "list-entry", "wrong-project-set",
])
def test_transfer_rejects_corruption(tmp_path, mutation):
    directory = tmp_path / "export"
    index = _index(directory)
    entry = index["artifacts"][0]
    if mutation == "digest":
        (directory / entry["filename"]).write_bytes(b"altered")
    elif mutation == "filename":
        entry["filename"] = "../elsewhere.zip"
    elif mutation == "plugin":
        entry["plugin"] = "../demo"
    elif mutation == "version":
        entry["version"] = "1.0/escape"
    elif mutation == "identity":
        entry["artifact_id"] = "sha256:invalid"
    elif mutation == "duplicate":
        index["artifacts"].append(dict(entry))
    elif mutation == "extra":
        (directory / "unexpected.txt").write_text("unexpected")
    elif mutation == "missing":
        (directory / entry["filename"]).unlink()
    elif mutation == "bool-schema":
        index["schema_version"] = True
    elif mutation == "list-root":
        index = []
    elif mutation == "list-entry":
        index["artifacts"] = [[]]
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError):
        _validate(directory, projects={"another": "1.0"} if mutation == "wrong-project-set" else None)


@pytest.mark.parametrize("mutation", [
    "third-party", "changed-digest", "traversal", "duplicate", "bare-digest",
])
def test_bundle_rejects_unsafe_wheels(tmp_path, mutation):
    wheel_dir = tmp_path / "wheels"
    manifest = _manifest(wheel_dir)
    if mutation == "third-party":
        manifest["wheels"][0]["role"] = "third-party"
    elif mutation == "changed-digest":
        (wheel_dir / manifest["wheels"][0]["filename"]).write_bytes(b"altered")
    elif mutation == "traversal":
        manifest["wheels"][0]["filename"] = "../escape.whl"
    elif mutation == "bare-digest":
        manifest["wheels"][0]["sha256"] = manifest["wheels"][0]["sha256"].removeprefix("sha256:")
    else:
        manifest["wheels"].append(dict(manifest["wheels"][0]))
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.bundle_wheels(manifest, wheel_dir, tmp_path)


def _fake_builder(repo: Path) -> None:
    project = repo / "plugins" / "demo-plugin" / "pyproject.toml"
    project.write_text('[project]\nname = "demo"\nversion = "0.1.0-dev1"\n', encoding="utf-8")
    (repo / "tools" / "build_python_artifacts.py").write_text(
        """import hashlib, pathlib
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
def build_plugin_artifacts(plugin, *, out_dir, toolchain):
    assert toolchain == {'shared': True}
    version = tomllib.loads((pathlib.Path('plugins') / plugin / 'pyproject.toml').read_text())['project']['version']
    out_dir.mkdir()
    wheel = out_dir / 'demo-1.0-py3-none-any.whl'
    wheel.write_bytes(b'wheel')
    pathlib.Path('backend-residue.txt').write_text('must not enter promotion')
    return {'plugin': plugin, 'version': version, 'artifact_id': 'sha256:' + 'd' * 64,
            'wheels': [{'role': 'plugin', 'filename': wheel.name, 'sha256': 'sha256:' + hashlib.sha256(b'wheel').hexdigest()}]}
""", encoding="utf-8",
    )
    (repo / "tools" / "build_toolchain_lock.py").write_text(
        """class ArtifactBuildError(RuntimeError): pass
calls = 0
def resolve_toolchain_lock(*args, **kwargs):
    global calls
    calls += 1
    assert calls == 1
    return {'shared': True}
""", encoding="utf-8",
    )
    shutil.copy(Path(artifacts.__file__), repo / "tools" / "promotion_python_artifacts.py")


def test_real_export_subprocess_uses_bumped_candidate_without_polluting_it(repo, tmp_path):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo)
    other = repo / "plugins" / "other-plugin"
    other.mkdir()
    (other / "plugin.json").write_text(
        json.dumps({"name": "other-plugin", "version": "1.0"}), encoding="utf-8",
    )
    (other / "pyproject.toml").write_text(
        '[project]\nname = "other"\nversion = "1.0"\n', encoding="utf-8",
    )
    (repo / ".changefiles").mkdir()
    (repo / ".changefiles" / "bump.json").write_text(json.dumps({
        "comment": "test", "changes": [{"plugin": "demo-plugin", "type": "patch"}],
    }), encoding="utf-8")
    dev = _commit(repo, "candidate")
    directory = tmp_path / "export"
    prepared = promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", build_python_artifacts_dir=directory,
    )
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    assert index["dev_head"] == dev
    assert index["artifacts"][0]["version"] == "0.1.1-dev1"
    assert [entry["plugin"] for entry in index["artifacts"]] == ["demo-plugin", "other-plugin"]
    assert "backend-residue.txt" not in _git(["ls-tree", "-r", "--name-only", prepared["commit"]], repo)
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1
    promoted = promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", python_artifacts_dir=directory,
    )
    assert promoted["promoted"]
    assert prepared["bumps"] == promoted["bumps"]


def test_missing_artifacts_fail_before_candidate_push(repo, tmp_path, monkeypatch):
    _git(["checkout", "-q", "dev"], repo)
    (repo / "README.md").write_text("changed\n")
    _commit(repo, "change")
    real_git = promotion._git
    pushes = []

    def git(argv, **kwargs):
        if argv[0] == "push":
            pushes.append(argv)
        return real_git(argv, **kwargs)

    monkeypatch.setattr(promotion, "_git", git)
    with pytest.raises(promotion.PromotionError, match="cannot read promotion artifacts"):
        promotion.promote(
            repo=repo, dev_ref="dev", main_ref="main", push=True,
            candidate_branch="release/test", python_artifacts_dir=tmp_path / "missing",
        )
    assert not pushes


def test_noop_does_not_build_or_require_artifacts(repo, tmp_path):
    report = promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", python_artifacts_dir=tmp_path / "missing",
    )
    assert not report["promoted"]
    report = promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main",
        build_python_artifacts_dir=tmp_path / "never-built",
    )
    assert not report["promoted"]
    assert not (tmp_path / "never-built").exists()


def test_export_and_push_cannot_share_authority(repo, tmp_path):
    with pytest.raises(promotion.PromotionError, match="separate"):
        promotion.promote(repo=repo, push=True, build_python_artifacts_dir=tmp_path / "export")


def test_export_refuses_an_existing_output_without_touching_it(repo, tmp_path):
    directory = tmp_path / "export"
    directory.mkdir()
    marker = directory / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(artifacts.PromotionArtifactError, match="already exists"):
        artifacts.export_artifacts(
            repo=repo, commit=DEV, content_tree=TREE, dev_head=DEV, main_before=MAIN,
            out_dir=directory,
        )
    assert marker.read_text() == "untouched"


def test_failed_builder_never_publishes_partial_export(repo, tmp_path, monkeypatch):
    _fake_builder(repo)
    commit = _commit(repo, "Python project")
    monkeypatch.setattr(artifacts, "_run", lambda *a, **kw: (_ for _ in ()).throw(
        artifacts.PromotionArtifactError("build failed")
    ))
    with pytest.raises(artifacts.PromotionArtifactError, match="build failed"):
        artifacts.export_artifacts(
            repo=repo, commit=commit, content_tree=TREE, dev_head=DEV, main_before=MAIN,
            out_dir=tmp_path / "export",
        )
    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob("python-artifacts-*"))
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


def test_attestation_checks_exact_workflow_ref_and_digests(tmp_path, monkeypatch):
    directory = tmp_path / "export"
    _index(directory)
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: (
        calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0, "verified", "")
    ))
    publisher.verify_attestations(
        directory, repository="example/plugins", signer_digest=MAIN, source_digest=MAIN,
        source_ref="refs/heads/main",
    )
    assert len(calls) == 2
    assert Path(calls[0][0][2]).name == artifacts.INDEX_NAME
    for argv, kwargs in calls:
        assert argv[argv.index("--signer-workflow") + 1] == "example/plugins/.github/workflows/validate-and-promote.yml"
        assert argv[argv.index("--signer-digest") + 1] == MAIN
        assert argv[argv.index("--source-digest") + 1] == MAIN
        assert argv[argv.index("--source-ref") + 1] == "refs/heads/main"
        assert "--deny-self-hosted-runners" in argv
        assert kwargs == {"verification": True}


def test_attestation_failure_stops_before_any_publication(tmp_path, monkeypatch):
    directory = tmp_path / "export"
    _index(directory)
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda argv, **kw: (
        calls.append(argv) or subprocess.CompletedProcess(argv, 1, "", "bad signer")
    ))
    assert publisher.main([
        "publish", "--directory", str(directory), "--repository", "example/plugins",
        "--signer-digest", MAIN, "--source-digest", MAIN, "--source-ref", "refs/heads/main",
        "--merged-commit", TREE, "--dev-head", DEV,
    ]) == 1
    assert len(calls) == 1
    assert calls[0][:2] == ["attestation", "verify"]


def test_verification_uses_read_token_not_release_authority(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "release-test-token")
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "verification-test-token")
    environments = []
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **kw: (
        environments.append(kw["env"]) or subprocess.CompletedProcess(a, 0, "", "")
    ))
    publisher._gh(["attestation", "verify"], verification=True)
    publisher._gh(["release", "upload"])
    assert environments[0]["GH_TOKEN"] == "verification-test-token"
    assert environments[1]["GH_TOKEN"] == "release-test-token"
    assert all("ARTIFACT_VERIFY_TOKEN" not in env for env in environments)


def _publish_fixture(tmp_path, monkeypatch):
    directory = tmp_path / "export"
    index = _index(directory)
    monkeypatch.setattr(promotion, "_git", lambda argv, **kw: MAIN if argv[1].endswith("^") else TREE)
    monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *a, **kw: TREE)
    monkeypatch.setattr(promotion, "add_scratch_worktree", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(promotion, "remove_scratch_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(publisher, "candidate_projects", lambda *a: {"demo": "1.0"})
    return directory, index["artifacts"][0]


@pytest.mark.parametrize("status", ["absent", "exists", "same", "different", "unauthorized"])
def test_release_publication_never_clobbers_and_only_treats_404_as_absence(tmp_path, monkeypatch, status):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    calls = []

    def gh(argv, **kwargs):
        calls.append(argv)
        if argv[0] != "api":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if status in ("absent", "unauthorized"):
            return subprocess.CompletedProcess(
                argv, 1, "", "gh: Not Found (HTTP 404)" if status == "absent" else "HTTP 403",
            )
        assets = [] if status == "exists" else [{
            "name": entry["filename"],
            "digest": f"sha256:{entry['sha256']}" if status == "same" else "sha256:wrong",
        }]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"assets": assets}), "")

    monkeypatch.setattr(publisher, "_gh", gh)
    kwargs = dict(repository="example/plugins", merged_commit=TREE, dev_head=DEV, repo=tmp_path)
    if status in ("different", "unauthorized"):
        with pytest.raises(artifacts.PromotionArtifactError):
            publisher.publish_artifacts(directory, **kwargs)
        assert len(calls) == 1
    else:
        publisher.publish_artifacts(directory, **kwargs)
        if status == "same":
            assert len(calls) == 1
        else:
            assert calls[1][:2] == ["release", "create" if status == "absent" else "upload"]
            assert "--clobber" not in calls[1]
            if status == "absent":
                assert calls[1][calls[1].index("--target") + 1] == TREE


def test_actual_merge_composition_mismatch_blocks_all_release_writes(tmp_path, monkeypatch):
    directory, _entry = _publish_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *a, **kw: "e" * 40)
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda *a, **kw: calls.append(a))
    with pytest.raises(artifacts.PromotionArtifactError, match="content_tree mismatch"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=TREE, dev_head=DEV,
            repo=tmp_path,
        )
    assert not calls


def test_workflow_separates_signing_from_release_authority_and_gates_publication():
    root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load((root / ".github" / "workflows" / "validate-and-promote.yml").read_text())
    jobs = workflow["jobs"]
    builder = jobs["python-artifacts"]
    assert builder["permissions"] == {
        "contents": "read", "id-token": "write", "attestations": "write",
    }
    assert "environment" not in builder
    assert "APERTURE_RELEASE_TOKEN" not in json.dumps(builder)
    assert "default_branch" in builder["if"]
    gate = next(step for step in jobs["gate"]["steps"] if step.get("id") == "check")
    assert gate["env"]["DEFAULT_REF"] == "refs/heads/${{ github.event.repository.default_branch }}"
    assert '[ "$GITHUB_REF" = "$DEFAULT_REF" ]' in gate["run"]
    assert "python-artifacts" in jobs["promote"]["needs"]
    assert "python-artifacts" in jobs["report-failure"]["needs"]
    assert "needs.python-artifacts.result" in jobs["report-failure"]["if"]
    steps = jobs["promote"]["steps"]
    decision = next(step for step in steps if step.get("id") == "decide")
    assert "needs.python-artifacts.result" in decision["run"]
    publishing = jobs["publish-python-artifacts"]
    assert "needs.promote.outputs.merge_sha != ''" in publishing["if"]
    assert jobs["promote"]["outputs"]["merge_sha"] == "${{ steps.merged.outputs.merge_sha }}"
    assert publishing["env"]["MERGE_SHA"] == "${{ needs.promote.outputs.merge_sha }}"
    assert publishing["env"]["ARTIFACT_VERIFY_TOKEN"] == "${{ github.token }}"
    assert "id-token" not in publishing["permissions"]
    assert "publish-python-artifacts" in jobs["report-failure"]["needs"]
