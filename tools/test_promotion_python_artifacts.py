"""Promotion composition, transfer integrity, signing and immutable publication."""
from __future__ import annotations

import hashlib
import base64
import csv
import io
import inspect
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from _promotion_artifact_fixture import artifact_repo as _artifact_repo_fixture, copy_fixture_seed
from _promotion_workflow_test_support import (
    assert_source_owned_boundary, heredoc, reviewed_helpers, source_owned,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_python_artifacts as builder
import promote_release as promotion
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from test_promote_release import (
    _commit, _git, no_ambient_git_identity as _identity_fixture, repo as _repo_fixture,
)

repo = _artifact_repo_fixture
no_ambient_git_identity = _identity_fixture

DEV, MAIN, TREE = "a" * 40, "b" * 40, "c" * 40
_STATIC_BUILD = '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'


def _wheel_bytes(
    name: str, version: str, *, payload: bytes = b"VALUE = 'first-party fixture'\n",
    members: dict[str, bytes] | None = None, flat: bool = False,
) -> bytes:
    prefix = f"{name}-{version}.dist-info/"
    files = {
        name + ".py" if flat else f"{name}/__init__.py": payload,
        prefix + "WHEEL": (
            "Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode(),
        prefix + "METADATA": f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n".encode(),
    }
    files.update(members or {})
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for path, payload in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        writer.writerow([path, "sha256=" + digest, str(len(payload))])
    writer.writerow([prefix + "RECORD", "", ""])
    files[prefix + "RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for path, payload in sorted(files.items()):
            bundle.writestr(zipfile.ZipInfo(path), payload)
    return output.getvalue()


def _complete_manifest(manifest: dict, *, toolchain=None) -> dict:
    toolchain = toolchain or builder.ToolchainLock(Path("."), {
        "setuptools": "80.9.0", "wheel": "0.45.1", "packaging": "25.0",
    }, provenance={
        "source_policy": "governed", "python": "/python",
        "validated_index_identity": "sha256:" + "f" * 64,
    })
    manifest["build_toolchain"] = toolchain.record
    manifest.setdefault("payload_hash", "sha256:" + "e" * 64)
    infos = []
    for wheel in manifest["wheels"]:
        info = builder.parse_wheel_filename(Path(wheel["filename"]))
        infos.append(info)
        wheel.update({key: info[key] for key in ("name", "python_tag", "abi_tag", "platform_tag")})
        wheel["generator"] = toolchain.generator
        wheel["source"] = (
            f"plugins/{manifest['plugin']}" if wheel["role"] == "plugin"
            else f"plugins/{manifest['plugin']}/libs/library"
        )
    tags = builder.overall_identity_tags(infos)
    manifest.update(tags)
    wheel_fields = [
        field for wheel in sorted(manifest["wheels"], key=lambda item: item["filename"])
        for field in (wheel["filename"], wheel["sha256"])
    ]
    manifest["artifact_id"] = "sha256:" + builder._hash_fields(
        manifest["payload_hash"], tags["python_tag"], tags["abi_tag"], tags["platform_tag"],
        toolchain.lock_id, *wheel_fields,
    )
    return manifest


def _manifest(
    directory: Path, *, role: str = "plugin", candidate_root: Path | None = None,
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "demo-1.0-py3-none-any.whl"
    wheel.write_bytes(_wheel_bytes("demo", "1.0"))
    manifest = {
        "schema": "copilot-extensions.python-artifact-manifest", "schema_version": 3,
        "plugin": "demo", "version": "1.0", "artifact_id": "sha256:" + "d" * 64,
        "wheels": [{
            "filename": wheel.name, "role": role, "sha256": f"sha256:{artifacts._digest(wheel)}",
        }],
    }
    if candidate_root is not None:
        trusted = promotion._load_module(Path(builder.__file__), "test_artifact_sources")
        trusted.REPO = candidate_root
        manifest["payload_hash"] = trusted.compute_payload_hash([candidate_root / "plugins" / "demo"])
    return _complete_manifest(manifest)


def _index(directory: Path, *, candidate_root: Path | None = None) -> dict:
    directory.mkdir()
    manifest = _manifest(directory.parent / "wheels", candidate_root=candidate_root)
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


def test_repo_preparation_seed_isolates_git_metadata_and_payload(
    repo: Path, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory,
    pytestconfig: pytest.Config,
) -> None:
    initial = _git(["rev-parse", "HEAD"], repo)
    tool = (repo / "tools/changefile.py").read_bytes()
    readme = (repo / "README.md").read_bytes()
    (repo / "README.md").write_text("mutated checkout\n", encoding="utf-8")
    (repo / "tools/changefile.py").write_text("raise RuntimeError('mutated tool')\n")
    _git(["config", "core.autocrlf", "true"], repo)
    changed = _commit(repo, "independent adversarial mutation")
    _git(["update-ref", "refs/heads/dev", changed], repo)
    fresh = copy_fixture_seed(
        pytestconfig, tmp_path_factory, _repo_fixture.__wrapped__, tmp_path / "fresh",
    )
    assert not (fresh / ".git").is_symlink()
    assert not os.path.samefile(repo / ".git/config", fresh / ".git/config")
    assert _git(["rev-parse", "HEAD", "main", "dev"], fresh).splitlines() == [initial] * 3
    assert _git(["config", "core.autocrlf"], fresh) == "false"
    assert _git(["status", "--porcelain"], fresh) == ""
    assert _git(["worktree", "list", "--porcelain"], fresh).startswith(f"worktree {fresh.as_posix()}\n")
    assert (fresh / "README.md").read_bytes() == readme
    assert (fresh / "tools/changefile.py").read_bytes() == tool


def test_seed_git_maintenance_policy_is_process_local(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory,
    pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "fixture.sentinel")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "preserved")
    expected = {
        "gc.auto": "0", "maintenance.auto": "false",
        "gc.autoDetach": "false", "maintenance.autoDetach": "false",
    }
    prepared = []

    def prepare(directory: Path) -> Path:
        prepared.append(directory)
        assert {key: _git(["config", "--get", key], directory) for key in expected} == expected
        assert _git(["config", "--get", "fixture.sentinel"], directory) == "preserved"
        _git(["init", "-q"], directory)
        _git(["config", "--local", "gc.auto", "1"], directory)
        assert _git(["config", "--get", "gc.auto"], directory) == "0"
        (directory / "payload").write_bytes(b"immutable seed\n")
        return directory

    first = copy_fixture_seed(pytestconfig, tmp_path_factory, prepare, tmp_path / "first")
    (first / "payload").write_bytes(b"mutable copy\n")
    second = copy_fixture_seed(pytestconfig, tmp_path_factory, prepare, tmp_path / "second")
    assert len(prepared) == 1
    assert (prepared[0] / "payload").read_bytes() == (second / "payload").read_bytes() == b"immutable seed\n"
    assert _git(["config", "--local", "--get", "gc.auto"], second) == "1"
    assert os.environ["GIT_CONFIG_COUNT"] == "1"
    assert os.environ["GIT_CONFIG_KEY_0"] == "fixture.sentinel"
    assert os.environ["GIT_CONFIG_VALUE_0"] == "preserved"


def test_bundle_and_transfer_roundtrip(tmp_path):
    directory = tmp_path / "export"
    index = _index(directory)
    assert _validate(directory, projects={"demo": "1.0"}) == index
    entry = index["artifacts"][0]
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        assert set(bundle.namelist()) == {"manifest.json", "demo-1.0-py3-none-any.whl"}
        assert json.loads(bundle.read("manifest.json"))["plugin"] == "demo"
        assert bundle.read("demo-1.0-py3-none-any.whl") == _wheel_bytes("demo", "1.0")

@pytest.mark.parametrize("failure", ["missing", "unreadable", "copy"])
def test_bundle_wheel_read_failures_are_structured(tmp_path, monkeypatch, failure):
    wheel_dir, out_dir = tmp_path / "wheels", tmp_path / "bundles"
    manifest = _manifest(wheel_dir)
    out_dir.mkdir()
    wheel = wheel_dir / manifest["wheels"][0]["filename"]
    if failure == "missing":
        wheel.unlink()
    elif failure == "unreadable":
        original = Path.open

        def open_file(path, *args, **kwargs):
            if path == wheel:
                raise PermissionError("wheel read denied")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_file)
    else:
        def copy_failure(*args, **kwargs):
            raise OSError("wheel copy failed")

        monkeypatch.setattr(artifacts.shutil, "copyfileobj", copy_failure)
    with pytest.raises(artifacts.PromotionArtifactError, match="cannot read wheel for bundling"):
        artifacts.bundle_wheels(manifest, wheel_dir, out_dir)


def test_build_cli_creates_missing_output_directory(tmp_path, monkeypatch):
    import build_toolchain_lock

    monkeypatch.setattr(promotion, "_load_module", lambda *args: SimpleNamespace())
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", lambda *args, **kwargs: None)
    directory = tmp_path / "nested" / "bundles"
    assert artifacts.main(["--repo", str(tmp_path), "--out-dir", str(directory)]) == 0
    assert json.loads((directory / artifacts._BUNDLES_NAME).read_text()) == []


@pytest.mark.parametrize("kind", ["file", "nonempty"])
def test_build_cli_reports_invalid_output_directory(tmp_path, monkeypatch, capsys, kind):
    monkeypatch.setattr(promotion, "_load_module", lambda *args: SimpleNamespace())
    directory = tmp_path / "bundles"
    if kind == "file":
        directory.write_text("not a directory")
    else:
        directory.mkdir()
        (directory / "existing").write_text("must not replace")
    assert artifacts.main(["--repo", str(tmp_path), "--out-dir", str(directory)]) == 1
    assert "promotion-python-artifacts: FAILED:" in capsys.readouterr().err


def test_identical_artifact_bundles_ignore_clock_mtimes_permissions_and_wheel_order(tmp_path, monkeypatch):
    wheel_dir = tmp_path / "wheels"
    manifest = _manifest(wheel_dir)
    library = wheel_dir / "library-1.0-py3-none-any.whl"
    library.write_bytes(_wheel_bytes("library", "1.0"))
    manifest["wheels"].append({
        "filename": library.name, "role": "vendored-lib",
        "sha256": "sha256:" + artifacts._digest(library),
    })
    _complete_manifest(manifest)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    original = artifacts.bundle_wheels(manifest, wheel_dir, first)
    for path in wheel_dir.iterdir():
        os.utime(path, (1000000000, 1000000000))
        path.chmod(0o444)
    monkeypatch.setattr(zipfile.time, "localtime", lambda *_: (2050, 12, 31, 23, 59, 58, 0, 0, 0))
    manifest["wheels"].reverse()
    try:
        rebuilt = artifacts.bundle_wheels(manifest, wheel_dir, second)
    finally:
        for path in wheel_dir.iterdir():
            path.chmod(0o644)
    assert original == rebuilt
    assert (first / original["filename"]).read_bytes() == (second / rebuilt["filename"]).read_bytes()
    with zipfile.ZipFile(first / original["filename"]) as bundle:
        for info in bundle.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3
            assert info.external_attr == (stat.S_IFREG | 0o644) << 16
            assert info.compress_type == zipfile.ZIP_STORED


@pytest.mark.parametrize("mutation", [
    "plugin", "version", "artifact_id", "schema", "schema-version", "list-manifest",
    "wheel-digest", "bare-digest", "third-party", "no-plugin", "two-plugins",
    "duplicate-record", "list-record", "missing-wheels", "traversal", "backslash",
    "missing-member", "extra-member", "duplicate-member", "symlink", "directory",
    "oversized-manifest", "oversized-bundle", "invalid-json", "invalid-zip",
])
def test_trusted_validator_rejects_archive_corruption_even_with_valid_outer_digest(
    tmp_path, monkeypatch, mutation,
):
    directory = tmp_path / "export"
    index = _index(directory)
    entry = index["artifacts"][0]
    path = directory / entry["filename"]
    with zipfile.ZipFile(path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        wheel_name = manifest["wheels"][0]["filename"]
        wheel_bytes = bundle.read(wheel_name)
    members = [(wheel_name, wheel_bytes)]
    if mutation in ("plugin", "version", "artifact_id", "schema"):
        manifest[mutation] = "wrong"
    elif mutation == "schema-version":
        manifest["schema_version"] = True
    elif mutation == "list-manifest":
        manifest = []
    elif mutation == "wheel-digest":
        members = [(wheel_name, b"altered")]
    elif mutation == "bare-digest":
        manifest["wheels"][0]["sha256"] = "a" * 64
    elif mutation == "third-party":
        manifest["wheels"][0]["role"] = "third-party"
    elif mutation == "no-plugin":
        manifest["wheels"][0]["role"] = "vendored-lib"
    elif mutation == "two-plugins":
        extra = dict(manifest["wheels"][0], filename="other.whl")
        manifest["wheels"].append(extra)
        members.append(("other.whl", wheel_bytes))
    elif mutation == "duplicate-record":
        manifest["wheels"].append(dict(manifest["wheels"][0]))
    elif mutation == "list-record":
        manifest["wheels"] = [[]]
    elif mutation == "missing-wheels":
        del manifest["wheels"]
    elif mutation in ("traversal", "backslash"):
        name = "../escape.whl" if mutation == "traversal" else "..\\escape.whl"
        manifest["wheels"][0]["filename"] = name
        members = [(name, wheel_bytes)]
    elif mutation == "missing-member":
        members = []
    elif mutation == "extra-member":
        members.append(("third-party.whl", b"undeclared"))
    elif mutation == "duplicate-member":
        members.append(members[0])
    elif mutation == "symlink":
        link = zipfile.ZipInfo(wheel_name)
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        members = [(link, wheel_bytes)]
    elif mutation == "directory":
        members.append(("directory/", b""))
    elif mutation == "oversized-manifest":
        monkeypatch.setattr(artifacts, "_MANIFEST_LIMIT", 1)
    elif mutation == "oversized-bundle":
        monkeypatch.setattr(artifacts, "_BUNDLE_LIMIT", 1)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(
            "manifest.json", b"not json" if mutation == "invalid-json" else json.dumps(manifest),
        )
        for name, payload in members:
            if mutation == "duplicate-member" and name in bundle.namelist():
                with pytest.warns(UserWarning, match="Duplicate name"):
                    bundle.writestr(name, payload)
                continue
            bundle.writestr(name, payload)
    if mutation == "invalid-zip":
        path.write_bytes(b"not a ZIP")
    entry["sha256"] = artifacts._digest(path)
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError):
        _validate(directory)


def test_trusted_validate_cli_does_not_import_candidate_code(repo, tmp_path, monkeypatch):
    directory = tmp_path / "export"
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch)
    dev = _commit(repo, "candidate")
    main = _git(["rev-parse", "main"], repo)
    promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", build_python_artifacts_dir=directory,
    )
    for name in ("changefile.py", "accumulate_bumps.py", "materialize_main.py"):
        (repo / "tools" / name).write_text("raise RuntimeError('must not execute candidate code')")
    dev = _commit(repo, "poison candidate tooling")
    scratch = promotion.add_scratch_worktree(dev, repo=repo)
    try:
        promotion.consume_pending_changes(
            scratch, main_head=main, repo=repo,
            trusted_source=Path(promotion.__file__).resolve().parent.parent,
        )
        content_tree = promotion.build_promotion_tree(scratch)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    index.update(dev_head=dev, content_tree=content_tree)
    _write_index(directory, index)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for name in ("build_toolchain_lock.py", "build_python_artifacts.py"):
        (candidate / name).write_text("raise RuntimeError('must not execute candidate code')")
    result = artifacts._run([
        sys.executable, str(Path(artifacts.__file__)), "validate",
        "--out-dir", str(directory), "--dev-head", dev, "--main-before", main, "--repo", str(repo),
    ], cwd=candidate)
    assert result == ""


def test_promotion_cli_never_executes_candidate_composer_with_release_authority(repo, tmp_path):
    remote = tmp_path / "origin.git"
    _git(["init", "--bare", str(remote)], repo)
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["checkout", "-q", "dev"], repo)
    tools = repo / "tools"
    for name in ("changefile.py", "accumulate_bumps.py", "materialize_main.py"):
        (tools / name).write_text("raise RuntimeError('candidate accessed release authority')")
    script = (
        repo / "plugins" / "customizing-copilot" / "skills"
        / "reviewing-customizations" / "scripts" / "manage-instruction-projections.py"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("raise RuntimeError('candidate projection accessed release authority')")
    _commit(repo, "poison candidate composition and projections")
    result = subprocess.run(
        [sys.executable, str(Path(promotion.__file__)), "--repo", str(repo),
         "--dev-ref", "dev", "--main-ref", "main", "--push",
         "--candidate-branch", "release/trusted-composition"],
        capture_output=True, text=True, check=False,
        env={**os.environ, "GH_TOKEN": "release-authority-sentinel"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "pushed candidate branch: release/trusted-composition" in result.stdout
    assert "candidate accessed release authority" not in result.stderr
    assert "candidate projection accessed release authority" not in result.stderr
    workflow = yaml.safe_load(
        (Path(promotion.__file__).resolve().parent.parent / ".github"
         / "workflows" / "validate-and-promote.yml").read_text(),
    )
    checkout = next(
        step for step in workflow["jobs"]["promote"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"


def test_validator_accepts_declared_vendored_library_wheel(tmp_path):
    directory = tmp_path / "export"
    index = _index(directory)
    path = directory / index["artifacts"][0]["filename"]
    with zipfile.ZipFile(path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        name = manifest["wheels"][0]["filename"]
        payload = bundle.read(name)
    manifest["wheels"].append({
        "filename": "library-1.0-py3-none-any.whl", "role": "vendored-lib",
        "sha256": "sha256:" + hashlib.sha256(_wheel_bytes("library", "1.0")).hexdigest(),
    })
    _complete_manifest(manifest)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr(name, payload)
        bundle.writestr("library-1.0-py3-none-any.whl", _wheel_bytes("library", "1.0"))
    new_name = f"demo-1.0-{manifest['artifact_id'][7:]}.zip"
    new_path = path.with_name(new_name)
    path.rename(new_path)
    index["artifacts"][0].update(
        artifact_id=manifest["artifact_id"], filename=new_name, sha256=artifacts._digest(new_path),
    )
    _write_index(directory, index)
    assert _validate(directory) == index


@pytest.mark.parametrize("mutation", [
    "missing-payload", "payload-format", "missing-tag", "wrong-tag", "missing-lock",
    "lock-shape", "missing-package", "invalid-pin", "missing-provenance",
    "missing-interpreter", "invalid-policy", "forged-lock", "missing-wheel-name",
    "wrong-wheel-tag", "wrong-generator", "missing-source", "escaping-source",
    "third-party-source", "wrong-wheel-version", "forged-artifact-id",
])
def test_schema3_requires_complete_provenance_and_computed_identity(tmp_path, mutation):
    manifest = _manifest(tmp_path / "wheels")
    if mutation == "missing-payload":
        del manifest["payload_hash"]
    elif mutation == "payload-format":
        manifest["payload_hash"] = "invalid"
    elif mutation == "missing-tag":
        del manifest["python_tag"]
    elif mutation == "wrong-tag":
        manifest["python_tag"] = "cp310"
    elif mutation == "missing-lock":
        del manifest["build_toolchain"]
    elif mutation == "lock-shape":
        manifest["build_toolchain"] = []
    elif mutation == "missing-package":
        del manifest["build_toolchain"]["packages"]["setuptools"]
    elif mutation == "invalid-pin":
        manifest["build_toolchain"]["packages"]["wheel"] = False
    elif mutation == "missing-provenance":
        del manifest["build_toolchain"]["provenance"]
    elif mutation == "missing-interpreter":
        del manifest["build_toolchain"]["provenance"]["python"]
    elif mutation == "invalid-policy":
        manifest["build_toolchain"]["provenance"]["source_policy"] = "public"
    elif mutation == "forged-lock":
        manifest["build_toolchain"]["lock_id"] = "sha256:" + "a" * 64
    elif mutation == "missing-wheel-name":
        del manifest["wheels"][0]["name"]
    elif mutation == "wrong-wheel-tag":
        manifest["wheels"][0]["platform_tag"] = "win_amd64"
    elif mutation == "wrong-generator":
        manifest["wheels"][0]["generator"] = "unknown"
    elif mutation == "missing-source":
        del manifest["wheels"][0]["source"]
    elif mutation == "escaping-source":
        manifest["wheels"][0]["source"] = "plugins/demo/../other"
    elif mutation == "third-party-source":
        manifest["wheels"][0]["source"] = "third-party/demo"
    elif mutation == "wrong-wheel-version":
        manifest["wheels"][0]["filename"] = "demo-2.0-py3-none-any.whl"
    else:
        manifest["artifact_id"] = "sha256:" + "a" * 64
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.validate_manifest(manifest)


def test_forged_content_address_fails_even_when_manifest_index_filename_and_digest_agree(tmp_path):
    directory = tmp_path / "export"
    index = _index(directory)
    entry = index["artifacts"][0]
    path = directory / entry["filename"]
    with zipfile.ZipFile(path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        name = manifest["wheels"][0]["filename"]
        payload = bundle.read(name)
    manifest["artifact_id"] = "sha256:" + "a" * 64
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr(name, payload)
    entry["artifact_id"] = manifest["artifact_id"]
    entry["filename"] = "demo-1.0-" + "a" * 64 + ".zip"
    target = path.with_name(entry["filename"])
    path.rename(target)
    entry["sha256"] = artifacts._digest(target)
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError, match="artifact identity mismatch"):
        _validate(directory)


@pytest.mark.parametrize("mutation", [
    "opaque", "generator", "duplicate-generator", "name", "version", "missing-metadata",
    "missing-record", "wrong-tag", "traversal", "backslash", "symlink", "duplicate-member",
    "extra-dist-info", "record-digest", "record-set", "compressed-bomb",
])
def test_trusted_wheel_validation_reads_real_metadata_and_members(tmp_path, monkeypatch, mutation):
    manifest = _manifest(tmp_path / "wheels")
    wheel = manifest["wheels"][0]
    with zipfile.ZipFile(io.BytesIO(_wheel_bytes("demo", "1.0"))) as bundle:
        members = [(info, bundle.read(info.filename)) for info in bundle.infolist()]
    if mutation in ("missing-metadata", "missing-record"):
        suffix = "/METADATA" if mutation == "missing-metadata" else "/RECORD"
        members = [(info, payload) for info, payload in members if not info.filename.endswith(suffix)]
    for position, (info, payload) in enumerate(members):
        if info.filename.endswith("/WHEEL"):
            if mutation == "generator":
                payload = payload.replace(b"setuptools (80.9.0)", b"unknown")
            elif mutation == "duplicate-generator":
                payload += b"Generator: setuptools (80.9.0)\n"
            elif mutation == "wrong-tag":
                payload = payload.replace(b"py3-none-any", b"cp310-none-any")
        elif info.filename.endswith("/METADATA"):
            if mutation == "name":
                payload = payload.replace(b"Name: demo", b"Name: other")
            elif mutation == "version":
                payload = payload.replace(b"Version: 1.0", b"Version: 2.0")
        elif info.filename.endswith("/RECORD") and mutation == "record-set":
            payload += b"undeclared.py,,\n"
        elif info.filename.endswith("__init__.py"):
            if mutation == "record-digest":
                payload = b"altered package bytes"
            elif mutation in ("traversal", "backslash"):
                info = zipfile.ZipInfo("../escape.py" if mutation == "traversal" else "..\\escape.py")
            elif mutation == "symlink":
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            elif mutation == "compressed-bomb":
                payload = b"x" * 20000
                monkeypatch.setattr(artifacts, "_BUNDLE_LIMIT", 10000)
        members[position] = info, payload
    if mutation == "duplicate-member":
        members.append(members[0])
    elif mutation == "extra-dist-info":
        members.append((zipfile.ZipInfo("other-1.0.dist-info/WHEEL"), b"wrong"))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for info, payload in members:
            info.compress_type = zipfile.ZIP_DEFLATED
            if info.filename in bundle.namelist():
                with pytest.warns(UserWarning, match="Duplicate name"):
                    bundle.writestr(info, payload)
            else:
                bundle.writestr(info, payload)
    archive = io.BytesIO(b"not a wheel" if mutation == "opaque" else output.getvalue())
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.validate_wheel(archive, wheel)


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


@pytest.mark.parametrize("case", ["public-match", "wrong-source", "wrong-floor", "mixed-locks"])
def test_signer_requires_configured_policy_and_one_shared_lock(tmp_path, case):
    wheel_dir, directory = tmp_path / "wheels", tmp_path / "export"
    directory.mkdir()
    manifest = _manifest(wheel_dir)
    packages = manifest["build_toolchain"]["packages"]
    provenance = {
        "source_policy": "seasoned-public", "index": "https://pypi.org/simple",
        "min_age_days": 0 if case == "wrong-floor" else 7,
        "packages": packages, "file_hashes": {name: ["a" * 64] for name in packages},
        "python": "/python",
    }
    if case != "wrong-source":
        _complete_manifest(manifest, toolchain=builder.ToolchainLock(Path("."), packages, provenance=provenance))
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 1,
        "dev_head": DEV, "main_before": MAIN, "content_tree": TREE,
        "artifacts": [artifacts.bundle_wheels(manifest, wheel_dir, directory)],
    }
    if case == "mixed-locks":
        manifest["plugin"] = "other"
        provenance["python"] = "/different-python"
        _complete_manifest(manifest, toolchain=builder.ToolchainLock(Path("."), packages, provenance=provenance))
        index["artifacts"].append(artifacts.bundle_wheels(manifest, wheel_dir, directory))
    _write_index(directory, index)
    kwargs = {
        "content_tree": TREE, "dev_head": DEV, "main_before": MAIN,
        "source_policy": "seasoned-public", "min_age_days": 7,
    }
    if case == "public-match":
        artifacts.validate_artifacts(directory, **kwargs)
    else:
        with pytest.raises(artifacts.PromotionArtifactError, match="policy|share one"):
            artifacts.validate_artifacts(directory, **kwargs)


def _fake_builder(repo: Path, monkeypatch, *, public: bool = False) -> None:
    project = repo / "plugins" / "demo-plugin" / "pyproject.toml"
    project.write_text(_STATIC_BUILD + '[project]\nname = "demo"\nversion = "0.1.0-dev1"\n', encoding="utf-8")
    package = project.parent / "src" / "demo"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    template = _manifest(repo / "fixture-wheels")
    if public:
        packages = template["build_toolchain"]["packages"]
        _complete_manifest(template, toolchain=builder.ToolchainLock(Path("."), packages, provenance={
            "source_policy": "seasoned-public", "index": "https://pypi.org/simple",
            "min_age_days": 7, "packages": packages,
            "file_hashes": {name: ["a" * 64] for name in packages}, "python": "/python",
        }))
    shutil.rmtree(repo / "fixture-wheels")
    (repo / "tools" / "build_python_artifacts.py").write_text(
        "TEMPLATE = " + repr(template) + "\n" + inspect.getsource(_wheel_bytes) + "\n"
        + """import base64, copy, csv, hashlib, io, pathlib, re, zipfile
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
def resolve_vendored_libs(directory):
    return []
def build_plugin_artifacts(plugin, *, out_dir, toolchain):
    assert toolchain.record == TEMPLATE['build_toolchain']
    project = tomllib.loads((REPO / 'plugins' / plugin / 'pyproject.toml').read_text())['project']
    version = project['version']
    name = re.sub(r'[-_.]+', '_', project['name']).lower()
    out_dir.mkdir()
    wheel = out_dir / (name + '-' + version.replace('-', '.') + '-py3-none-any.whl')
    wheel.write_bytes(_wheel_bytes(name, version.replace('-', '.')))
    (REPO / 'backend-residue.txt').write_text('must not enter promotion')
    manifest = copy.deepcopy(TEMPLATE)
    manifest.update(plugin=plugin, version=version)
    manifest['wheels'][0].update(name=name, filename=wheel.name, source='plugins/' + plugin,
                               sha256='sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest())
    def hash_fields(fields):
        digest = hashlib.sha256()
        for field in fields:
            encoded = field.encode('utf-8')
            digest.update(len(encoded).to_bytes(8, 'big'))
            digest.update(encoded)
        return digest.hexdigest()
    source = REPO / 'plugins' / plugin
    file_fields = []
    for path in sorted(source.rglob('*')):
        if path.is_file():
            file_fields += [path.relative_to(source).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
    manifest['payload_hash'] = 'sha256:' + hash_fields([source.relative_to(REPO).as_posix(), hash_fields(file_fields)])
    fields = [manifest['payload_hash'], manifest['python_tag'], manifest['abi_tag'],
              manifest['platform_tag'], manifest['build_toolchain']['lock_id'],
              wheel.name, manifest['wheels'][0]['sha256']]
    manifest['artifact_id'] = 'sha256:' + hash_fields(fields)
    return manifest
""", encoding="utf-8",
    )
    (repo / "tools" / "build_toolchain_lock.py").write_text(
        "RECORD = " + repr(template["build_toolchain"]) + "\n"
        + """class ArtifactBuildError(RuntimeError): pass
class Toolchain:
    record = RECORD
calls = 0
def resolve_toolchain_lock(*args, **kwargs):
    global calls
    calls += 1
    assert calls == 1
    return Toolchain()
""", encoding="utf-8",
    )
    shutil.copy(Path(artifacts.__file__), repo / "tools" / "promotion_python_artifacts.py")
    driver = repo.parent / "trusted" / "tools"
    shutil.copytree(repo / "tools", driver)
    shutil.copy(Path(promotion.__file__), driver / "promote_release.py")
    shutil.copy(Path(promotion.__file__).with_name("promotion_candidate.py"), driver / "promotion_candidate.py")
    for name in (
        "promotion_python_artifacts.py", "build_python_artifacts.py", "build_toolchain_lock.py",
        "promotion_candidate.py",
    ):
        (repo / "tools" / name).write_text("raise RuntimeError('candidate forged toolchain provenance')")
    real_run = artifacts._run

    def trusted_fixture_run(argv, *, cwd=None):
        if argv[2:3] == ["--repo"]:
            assert Path(argv[1]) == Path(artifacts.__file__).resolve()
            argv = [argv[0], str(driver / "promotion_python_artifacts.py"), *argv[2:]]
            cwd = driver
        return real_run(argv, cwd=cwd)

    monkeypatch.setattr(artifacts, "_run", trusted_fixture_run)


def test_real_export_subprocess_uses_bumped_candidate_without_polluting_it(repo, tmp_path, monkeypatch):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch)
    other = repo / "plugins" / "other-plugin"
    other.mkdir()
    (other / "plugin.json").write_text(
        json.dumps({"name": "other-plugin", "version": "1.0"}), encoding="utf-8",
    )
    (other / "pyproject.toml").write_text(
        _STATIC_BUILD + '[project]\nname = "other"\nversion = "1.0"\n', encoding="utf-8",
    )
    (other / "src" / "other").mkdir(parents=True)
    (other / "src" / "other" / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
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
    assert _git(["tag", "--points-at", prepared["commit"]], repo) == ""
    assert prepared["tag"] is None
    assert prepared["tag_stem"].startswith(f"{promotion.PROMOTION_TAG_PREFIX}-")
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1
    promoted = promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", python_artifacts_dir=directory,
    )
    assert promoted["promoted"]
    assert prepared["bumps"] == promoted["bumps"]
    assert promoted["tag"] in _git(["tag", "--points-at", promoted["commit"]], repo).splitlines()


@pytest.mark.parametrize("attributes,eol,mutation", [
    (".gitattributes", "lf", "none"),
    (".gitattributes", "crlf", "none"),
    ("plugins/demo-plugin/.gitattributes", "lf", "none"),
    ("plugins/demo-plugin/.gitattributes", "crlf", "none"),
    (".gitattributes", "crlf", "plugin-source"),
    (".gitattributes", "crlf", "vendored-source"),
    (".gitattributes", "crlf", "bundle-bytes"),
    (".gitattributes", "crlf", "wheel-bytes"),
])
def test_materialized_producer_verifier_roundtrip(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    attributes: str, eol: str, mutation: str,
) -> None:
    _git(["checkout", "-q", "dev"], repo)
    _git(["config", "core.autocrlf", "false"], repo)
    plugin = repo / "plugins" / "demo-plugin"
    (plugin / "pyproject.toml").write_text(
        _STATIC_BUILD
        + '[project]\nname = "demo"\nversion = "0.1.0-dev1"\n'
        + '[tool.uv.sources]\nlibrary = { path = "../../libs/library", editable = true }\n',
        encoding="utf-8",
    )
    for source, name, version in (
        (plugin, "demo", "0.1.0-dev1"),
        (repo / "libs" / "library", "library", "1.0"),
    ):
        package = source / "src" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
        if name == "library":
            (source / "pyproject.toml").write_text(
                _STATIC_BUILD + f'[project]\nname = "{name}"\nversion = "{version}"\n',
                encoding="utf-8",
            )
    (repo / attributes).write_text(f"*.ps1 text eol={eol}\n", encoding="utf-8")
    (plugin / "generated.ps1").write_bytes(b"first line\nsecond line\n")
    dev = _commit(repo, "declared source closure and checkout policy")
    main = _git(["rev-parse", "main"], repo)
    scratch = promotion.add_scratch_worktree(dev, repo=repo)
    try:
        summary = promotion.consume_pending_changes(
            scratch, main_head=main, repo=repo,
            trusted_source=Path(promotion.__file__).resolve().parent.parent,
        )
        tree = promotion.build_promotion_tree(scratch, control=summary.get("control_preserved"))
        commit = _git(["commit-tree", tree, "-p", main, "-m", "materialized candidate"], repo)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)
    candidate = promotion.add_scratch_worktree(commit, repo=repo)
    try:
        assert not builder.uer.find_uv_editable_refs(candidate / "plugins" / "demo-plugin")
        assert (candidate / "plugins/demo-plugin/generated.ps1").read_bytes() == (
            b"first line\r\nsecond line\r\n" if eol == "crlf" else b"first line\nsecond line\n"
        )
        producer = promotion._load_module(Path(builder.__file__), "roundtrip_artifact_producer")
        producer.REPO = candidate
        producer.PLUGINS_DIR = candidate / "plugins"
        producer.LIBS_DIR = candidate / "libs"

        def encode_wheel(
            source_dir: Path, out_dir: Path, *, toolchain: builder.ToolchainLock,
            reserved_names: set[str],
        ) -> Path:
            # Only the external build backend is replaced: closure discovery,
            # pre-build source hashing, identity generation and verification are real.
            project = artifacts.tomllib.loads(
                (source_dir / "pyproject.toml").read_text(encoding="utf-8"),
            )["project"]
            name = project["name"]
            version = project["version"].replace("-", ".")
            wheel = out_dir / f"{name}-{version}-py3-none-any.whl"
            assert toolchain.generator == "setuptools (80.9.0)"
            assert wheel.name not in reserved_names
            reserved_names.add(wheel.name)
            out_dir.mkdir(parents=True, exist_ok=True)
            wheel.write_bytes(_wheel_bytes(
                name, version, payload=(source_dir / "src" / name / "__init__.py").read_bytes(),
            ))
            return wheel

        monkeypatch.setattr(producer, "build_wheel", encode_wheel)
        toolchain = builder.ToolchainLock(Path("."), {
            "setuptools": "80.9.0", "wheel": "0.45.1", "packaging": "25.0",
        }, provenance={
            "source_policy": "governed", "python": "/python",
            "validated_index_identity": "sha256:" + "f" * 64,
        })
        wheels = tmp_path / "produced-wheels"
        manifest = producer.build_plugin_artifacts("demo-plugin", out_dir=wheels, toolchain=toolchain)
        assert {wheel["source"] for wheel in manifest["wheels"]} == {
            "plugins/demo-plugin", "plugins/demo-plugin/libs/library",
        }
        directory = tmp_path / "produced-bundles"
        directory.mkdir()
        entry = artifacts.bundle_wheels(manifest, wheels, directory)
        index = {
            "schema": artifacts.SCHEMA, "schema_version": 1,
            "dev_head": dev, "main_before": main, "content_tree": tree, "artifacts": [entry],
        }
        _write_index(directory, index)
        assert artifacts.validate_candidate_artifacts(
            directory, repo=repo, dev_head=dev, main_before=main,
        ) == index
        if mutation == "none":
            return
        archive = directory / entry["filename"]
        if mutation in ("plugin-source", "vendored-source"):
            relative = "src/demo/__init__.py" if mutation == "plugin-source" else (
                "libs/library/src/library/__init__.py"
            )
            (candidate / "plugins" / "demo-plugin" / relative).write_bytes(b"changed shipped source\n")
            diagnostic = "manifest payload hash differs"
        elif mutation == "bundle-bytes":
            archive.write_bytes(archive.read_bytes() + b"\n")
            diagnostic = "artifact digest mismatch"
        else:
            with zipfile.ZipFile(archive) as bundle:
                members = {name: bundle.read(name) for name in bundle.namelist()}
            wheel_name = next(wheel["filename"] for wheel in manifest["wheels"] if wheel["role"] == "plugin")
            members[wheel_name] += b"\n"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, payload in members.items():
                    bundle.writestr(name, payload)
            entry["sha256"] = artifacts._digest(archive)
            _write_index(directory, index)
            diagnostic = "wheel digest mismatch"
        with pytest.raises(artifacts.PromotionArtifactError, match=diagnostic):
            artifacts.validate_artifacts(
                directory, content_tree=tree, dev_head=dev, main_before=main,
                projects=artifacts.candidate_projects(candidate), candidate_root=candidate,
            )
    finally:
        promotion.remove_scratch_worktree(candidate, repo=repo)


@pytest.mark.parametrize("suffix", ["ps1", "cmd"])
@pytest.mark.parametrize("eol", ["crlf", "lf"])
@pytest.mark.parametrize("entrypoint", ["cli", "workflow", "promote"])
def test_candidate_artifact_validation_checks_out_composed_attributes(
    repo, tmp_path, monkeypatch, suffix, eol, entrypoint,
):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch, public=entrypoint == "workflow")
    (repo / ".gitattributes").write_text(f"*.{suffix} text eol={eol}\n", encoding="utf-8")
    relative = Path("plugins/demo-plugin") / f"generated.{suffix}"
    payload = b"first line\nsecond line\n"
    (repo / relative).write_bytes(payload)
    dev = _commit(repo, "generated payload with checkout attributes")
    main = _git(["rev-parse", "main"], repo)
    consume = promotion.consume_pending_changes

    def materialize(scratch, **kwargs):
        summary = consume(scratch, **kwargs)
        (scratch / relative).write_bytes(payload)
        return summary

    monkeypatch.setattr(promotion, "consume_pending_changes", materialize)
    directory = tmp_path / "export"
    promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", build_python_artifacts_dir=directory,
        toolchain_source="seasoned-public" if entrypoint == "workflow" else "governed",
        toolchain_min_age_days=7 if entrypoint == "workflow" else None,
    )
    if entrypoint == "workflow":
        _run_workflow_validator(repo, directory, dev, main, monkeypatch)
        index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    elif entrypoint == "promote":
        report = promotion.promote(
            repo=repo, dev_ref="dev", main_ref="main", python_artifacts_dir=directory,
        )
        assert report["promoted"]
        index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    else:
        index = artifacts.validate_candidate_artifacts(
            directory, repo=repo, dev_head=dev, main_before=main,
        )
    assert index["dev_head"] == dev
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


@pytest.mark.parametrize("mutation", ["changed-index", "invalid-tree"])
def test_composed_source_identity_refuses_before_checkout_or_artifact_reads(
    repo, tmp_path, monkeypatch, mutation,
):
    tree = _git(["write-tree"], repo)
    if mutation == "changed-index":
        (repo / "README.md").write_text("another composed snapshot\n", encoding="utf-8")
        _git(["add", "README.md"], repo)
    else:
        tree = "--not-a-tree"
    run = artifacts._run

    def guarded_run(argv, **kwargs):
        assert "checkout-index" not in argv
        return run(argv, **kwargs)

    monkeypatch.setattr(artifacts, "_run", guarded_run)
    monkeypatch.setattr(artifacts, "validate_artifacts", lambda *a, **kw: pytest.fail(
        "artifact reads must not precede composed source identity admission"
    ))
    with pytest.raises(artifacts.PromotionArtifactError, match="composed source"):
        artifacts.validate_composed_artifacts(
            tmp_path / "unread-artifacts", scratch=repo, content_tree=tree,
            dev_head=DEV, main_before=MAIN,
        )


@pytest.mark.parametrize(
    "attribute_noise", ["none", "unstaged-root", "untracked-nested", "staged-policy"],
)
def test_composed_source_ignores_working_tree_representation_and_noise(
    repo, tmp_path, monkeypatch, attribute_noise,
):
    relative = Path("plugins/demo-plugin/generated.ps1")
    (repo / ".gitattributes").write_text("*.ps1 text eol=crlf\n", encoding="utf-8")
    (repo / relative).write_bytes(b"first line\nsecond line\n")
    _commit(repo, "canonical source contract")
    tree = _git(["write-tree"], repo)
    expected = b"first line\r\nsecond line\r\n"
    if attribute_noise == "staged-policy":
        (repo / ".gitattributes").write_text("*.ps1 text eol=lf\n", encoding="utf-8")
        _git(["add", ".gitattributes"], repo)
        tree = _git(["write-tree"], repo)
        (repo / ".gitattributes").write_text("*.ps1 text eol=crlf\n", encoding="utf-8")
        expected = b"first line\nsecond line\n"
    elif attribute_noise != "none":
        attributes = repo / (
            ".gitattributes" if attribute_noise == "unstaged-root"
            else "plugins/demo-plugin/.gitattributes"
        )
        attributes.write_text("*.ps1 text eol=lf\n", encoding="utf-8")
    (repo / relative).write_bytes(b"first line\nsecond line\n")
    os.utime(repo / relative, (1, 1))
    noise = repo / "plugins/demo-plugin/.pytest_cache"
    noise.mkdir()
    (noise / "cache.json").write_text("untracked fixture noise", encoding="utf-8")
    checked = []

    def validate(directory, **kwargs):
        candidate = kwargs["candidate_root"]
        checked.append(candidate)
        assert kwargs["content_tree"] == tree
        assert (candidate / relative).read_bytes() == expected
        assert not (candidate / "plugins/demo-plugin/.pytest_cache").exists()
        return {"canonical": True}

    monkeypatch.setattr(artifacts, "validate_artifacts", validate)
    assert artifacts.validate_composed_artifacts(
        tmp_path / "artifacts", scratch=repo, content_tree=tree,
        dev_head=DEV, main_before=MAIN,
    ) == {"canonical": True}
    assert _git(["write-tree"], repo) == tree
    assert len(checked) == 1 and not checked[0].exists()
    assert (repo / relative).read_bytes() == b"first line\nsecond line\n"
    assert (noise / "cache.json").read_text() == "untracked fixture noise"


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


def test_promoter_script_reports_canonical_checkout_failure(repo, tmp_path, monkeypatch, capsys):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch)
    _commit(repo, "candidate")
    directory = tmp_path / "export"
    promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", build_python_artifacts_dir=directory,
    )
    run = subprocess.run

    def fail_checkout(argv, *args, **kwargs):
        if argv[0] == "git" and "checkout-index" in argv:
            return subprocess.CompletedProcess(argv, 128, "", "forced canonical checkout failure")
        return run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_checkout)
    monkeypatch.setattr(sys, "argv", [
        str(promotion.__file__), "--repo", str(repo), "--dev-ref", "dev",
        "--main-ref", "main", "--python-artifacts-dir", str(directory),
    ])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(promotion.__file__), run_name="__main__")
    assert exited.value.code == 1
    captured = capsys.readouterr()
    assert "forced canonical checkout failure" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


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
    _fake_builder(repo, monkeypatch)
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
        "--merged-commit", TREE, "--candidate-commit", TREE, "--dev-head", DEV,
    ]) == 1
    assert len(calls) == 1
    assert calls[0][:2] == ["attestation", "verify"]

@pytest.mark.parametrize("mutation", ["extra-zip", "missing-bundle", "unsafe-name", "invalid-index"])
def test_verified_index_rejects_invalid_subject_set_before_bundle_attestations(
    tmp_path, monkeypatch, mutation,
):
    directory = tmp_path / "export"
    index = _index(directory)
    if mutation == "extra-zip":
        (directory / "unrelated.zip").write_bytes(b"unrelated")
    elif mutation == "missing-bundle":
        (directory / index["artifacts"][0]["filename"]).unlink()
    elif mutation == "unsafe-name":
        index["artifacts"][0]["filename"] = "../unrelated.zip"
        _write_index(directory, index)
    else:
        (directory / artifacts.INDEX_NAME).write_text("[]")
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda argv, **kwargs: (
        calls.append(argv) or subprocess.CompletedProcess(argv, 0, "verified", "")
    ))
    with pytest.raises(artifacts.PromotionArtifactError):
        publisher.verify_attestations(
            directory, repository="example/plugins", signer_digest=MAIN, source_digest=MAIN,
            source_ref="refs/heads/main",
        )
    assert len(calls) == 1
    assert Path(calls[0][2]).name == artifacts.INDEX_NAME


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
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(_STATIC_BUILD + '[project]\nname = "demo"\nversion = "1.0"\n')
    (plugin_dir / "src" / "demo").mkdir(parents=True)
    (plugin_dir / "src" / "demo" / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    index = _index(directory, candidate_root=tmp_path)
    monkeypatch.setattr(promotion, "_git", lambda argv, **kw: MAIN if argv[1].endswith("^") else TREE)
    monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *a, **kw: TREE)
    monkeypatch.setattr(promotion, "add_scratch_worktree", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(promotion, "remove_scratch_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(publisher, "candidate_projects", lambda *a: {"demo": "1.0"})
    return directory, index["artifacts"][0]


@pytest.mark.parametrize("status", ["absent", "exists", "same", "different", "unauthorized", "draft"])
@pytest.mark.parametrize("not_found", [
    "gh: Not Found (HTTP 404)", "HTTP 404: Not Found", "gh: HTTP 404: Not Found",
])
def test_release_publication_never_clobbers_and_only_treats_404_as_absence(
    tmp_path, monkeypatch, status, not_found,
):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    calls = []
    created = False

    def gh(argv, **kwargs):
        nonlocal created
        calls.append(argv)
        if argv[0] != "api":
            if argv[:2] == ["release", "create"]:
                created = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "/git/ref/tags/" in argv[1]:
            if status == "absent" and not created:
                return subprocess.CompletedProcess(argv, 1, "", not_found)
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"object": {"type": "commit", "sha": TREE}}), "",
            )
        if (status == "absent" and not created) or status == "unauthorized":
            return subprocess.CompletedProcess(
                argv, 1, "", not_found if status == "absent" else "HTTP 403",
            )
        assets = [] if status in ("exists", "absent", "draft") else [{
            "name": entry["filename"],
            "digest": f"sha256:{entry['sha256']}" if status == "same" else "sha256:wrong",
        }]
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "assets": assets, "draft": status == "draft", "tag_name": "demo-v1.0",
        }), "")

    monkeypatch.setattr(publisher, "_gh", gh)
    kwargs = dict(
        repository="example/plugins", merged_commit=TREE, candidate_commit=TREE,
        dev_head=DEV, repo=tmp_path,
    )
    if status in ("different", "unauthorized", "draft"):
        with pytest.raises(artifacts.PromotionArtifactError):
            publisher.publish_artifacts(directory, **kwargs)
        assert not any(argv[0] == "release" for argv in calls)
    else:
        publisher.publish_artifacts(directory, **kwargs)
        writes = [argv for argv in calls if argv[0] == "release"]
        if status == "same":
            assert not writes
        else:
            assert writes[-1][:2] == ["release", "upload"]
            assert all("--clobber" not in argv for argv in writes)
            if status == "absent":
                assert writes[0][:2] == ["release", "create"]
                assert writes[0][writes[0].index("--target") + 1] == TREE
                assert str(directory / entry["filename"]) not in writes[0]


@pytest.mark.parametrize("code,error,expected", [
    (1, "gh: Not Found (HTTP 404)\n", True),
    (1, "(HTTP 404)", True),
    (1, "HTTP 404: Not Found (https://api.github.com/endpoint)", True),
    (1, "gh: HTTP 404: Not Found", True),
    (1, "gh: Forbidden (HTTP 403); see docs for HTTP 404", False),
    (1, "HTTP 403: Forbidden; see docs for HTTP 404", False),
    (1, "HTTP 4040: invalid", False),
    (1, "request failed: HTTP 404", False),
    (1, "HTTP 404: Not Found\nHTTP 403: Forbidden", False),
    (0, "HTTP 404: Not Found", False),
])
def test_only_explicit_unambiguous_http_404_status_is_absence(code, error, expected):
    assert publisher._is_not_found(subprocess.CompletedProcess(["gh"], code, "", error)) is expected


@pytest.mark.parametrize("tag_state", ["current", "same-ancestor", "wrong-ancestor", "unshipped", "missing"])
def test_release_tag_must_bind_the_actual_plugin_payload(tmp_path, monkeypatch, tag_state):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    older = tmp_path / "older"
    shutil.copytree(tmp_path / "plugins", older / "plugins")
    if tag_state == "wrong-ancestor":
        (older / "plugins" / "demo" / "injected.py").write_text("different old payload")
    monkeypatch.setattr(publisher, "_tag_commit", lambda *_: (
        None if tag_state == "missing" else TREE if tag_state == "current" else MAIN
    ))
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *a, **kw: tag_state != "unshipped")
    monkeypatch.setattr(promotion, "add_scratch_worktree", lambda commit, **kw: (
        older if commit == MAIN else tmp_path
    ))
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "draft": False, "tag_name": "demo-v1.0", "assets": [],
        }), "")
    monkeypatch.setattr(publisher, "_gh", gh)
    if tag_state in ("wrong-ancestor", "unshipped", "missing"):
        with pytest.raises(artifacts.PromotionArtifactError):
            publisher.publish_artifacts(
                directory, repository="example/plugins", merged_commit=TREE, dev_head=DEV, repo=tmp_path,
            )
        assert not any(argv[0] == "release" for argv in calls)
    else:
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=TREE, dev_head=DEV, repo=tmp_path,
        )
        assert calls[-1][:2] == ["release", "upload"]
        assert calls[-1][-1] == str(directory / entry["filename"])


@pytest.mark.parametrize("kind", [
    "lightweight", "annotated", "absent", "cycle", "blob", "unauthorized", "missing-object",
])
def test_tag_resolution_is_bounded_and_only_initial_404_means_absence(monkeypatch, kind):
    def gh(argv, **kwargs):
        if kind in ("absent", "unauthorized"):
            return subprocess.CompletedProcess(
                argv, 1, "", "(HTTP 404)" if kind == "absent" else "(HTTP 403)",
            )
        if kind == "missing-object" and "/git/tags/" in argv[1]:
            return subprocess.CompletedProcess(argv, 1, "", "(HTTP 404)")
        object_type = (
            "blob" if kind == "blob" else "tag" if kind == "cycle"
            or (kind in ("annotated", "missing-object") and "/git/ref/" in argv[1]) else "commit"
        )
        sha = MAIN if object_type == "tag" else TREE
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "object": {"type": object_type, "sha": sha},
        }), "")
    monkeypatch.setattr(publisher, "_gh", gh)
    if kind in ("cycle", "blob", "unauthorized", "missing-object"):
        with pytest.raises(artifacts.PromotionArtifactError):
            publisher._tag_commit("example/plugins", "demo-v1.0")
    else:
        assert publisher._tag_commit("example/plugins", "demo-v1.0") == (
            None if kind == "absent" else TREE
        )


def test_tag_created_between_absence_check_and_release_creation_blocks_upload(tmp_path, monkeypatch):
    directory, _entry = _publish_fixture(tmp_path, monkeypatch)
    tags = iter([None, MAIN])
    monkeypatch.setattr(publisher, "_tag_commit", lambda *_: next(tags))
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 1 if argv[0] == "api" else 0, "", "(HTTP 404)" if argv[0] == "api" else "",
        )
    monkeypatch.setattr(publisher, "_gh", gh)
    with pytest.raises(artifacts.PromotionArtifactError, match="tag changed during creation"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=TREE, dev_head=DEV, repo=tmp_path,
        )
    assert [argv[:2] for argv in calls if argv[0] == "release"] == [["release", "create"]]


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


def test_invalid_bundle_blocks_publication_even_with_valid_index_digest(tmp_path, monkeypatch):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    path = directory / entry["filename"]
    path.write_bytes(b"not a ZIP")
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    index["artifacts"][0]["sha256"] = artifacts._digest(path)
    _write_index(directory, index)
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda *a, **kw: calls.append(a))
    with pytest.raises(artifacts.PromotionArtifactError, match="cannot read artifact bundle"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=TREE, dev_head=DEV,
            repo=tmp_path,
        )
    assert not calls


def test_changed_candidate_source_blocks_publication_before_any_release_write(tmp_path, monkeypatch):
    directory, _entry = _publish_fixture(tmp_path, monkeypatch)
    (tmp_path / "plugins" / "demo" / "injected.py").write_text("changed source")
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda *a, **kw: calls.append(a))
    with pytest.raises(artifacts.PromotionArtifactError, match="payload hash differs") as refused:
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=TREE, dev_head=DEV,
            repo=tmp_path,
        )
    assert "demo@1.0" in str(refused.value)
    assert "expected=sha256:" in str(refused.value)
    assert "actual=sha256:" in str(refused.value)
    assert str(tmp_path) not in str(refused.value)
    assert not calls


def test_first_party_label_does_not_substitute_for_candidate_closure(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(_STATIC_BUILD + '[project]\nname = "demo"\nversion = "1.0"\n')
    manifest = _manifest(tmp_path / "wheels", candidate_root=tmp_path)
    manifest["wheels"].append({
        "filename": "library-1.0-py3-none-any.whl", "role": "vendored-lib",
        "sha256": "sha256:" + "a" * 64,
    })
    _complete_manifest(manifest)
    artifacts.validate_manifest(manifest)
    with pytest.raises(artifacts.PromotionArtifactError, match="source closure differs"):
        artifacts.validate_source_binding(manifest, tmp_path)


def test_invalid_candidate_identity_fails_before_git_or_source_access(tmp_path):
    with pytest.raises(artifacts.PromotionArtifactError, match="candidate commit identity"):
        artifacts.validate_candidate_artifacts(
            tmp_path, repo=tmp_path, dev_head="--untrusted-option", main_before=MAIN,
        )


def _run_workflow_validator(repo, directory, dev, main, monkeypatch):
    root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text())
    current = source_owned(workflow["jobs"])
    validator = next(
        step for step in workflow["jobs"]["python-artifacts"]["steps"]
        if step.get("name") == (
            "Validate untrusted bundles using only pinned reviewed helpers" if current
            else "Validate untrusted bundles using only trusted default-branch code"
        )
    )
    script = heredoc(validator["run"])
    if current:
        if all(isinstance(value, str) and len(value) == 40
               and all(char in "0123456789abcdef" for char in value) for value in (dev, main)):
            _helpers, pin = reviewed_helpers(repo, root)
        else:
            pin = "f" * 40
        argv = ["-", str(repo), pin, dev, main, str(directory), "false"]
    else:
        argv = ["-", dev, main, str(directory)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(repo)
    exec(compile(script, "<trusted-workflow-validator>", "exec"), {})


def test_workflow_validator_rejects_invalid_identity_before_git(tmp_path, monkeypatch):
    with pytest.raises(artifacts.PromotionArtifactError, match="candidate commit identity"):
        _run_workflow_validator(tmp_path, tmp_path, "--untrusted-option", MAIN, monkeypatch)


def test_workflow_validator_rejects_changed_bundle(repo, tmp_path, monkeypatch):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch, public=True)
    dev = _commit(repo, "candidate")
    main = _git(["rev-parse", "main"], repo)
    directory = tmp_path / "export"
    promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", build_python_artifacts_dir=directory,
        toolchain_source="seasoned-public", toolchain_min_age_days=7,
    )
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    (directory / index["artifacts"][0]["filename"]).write_bytes(b"changed")
    with pytest.raises(artifacts.PromotionArtifactError, match="artifact digest mismatch"):
        _run_workflow_validator(repo, directory, dev, main, monkeypatch)
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


@pytest.mark.parametrize("entrypoint", ["cli", "workflow", "promote"])
def test_candidate_artifact_validation_resolves_temporary_root_aliases(
    repo, tmp_path, monkeypatch, entrypoint,
):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch, public=entrypoint == "workflow")
    dev = _commit(repo, "candidate")
    main = _git(["rev-parse", "main"], repo)
    directory = tmp_path / "export"
    promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", build_python_artifacts_dir=directory,
        toolchain_source="seasoned-public" if entrypoint == "workflow" else "governed",
        toolchain_min_age_days=7 if entrypoint == "workflow" else None,
    )
    temporary_directory = artifacts.tempfile.TemporaryDirectory

    class AliasedTemporaryDirectory:
        def __init__(self, **kwargs):
            self.directory = temporary_directory(**kwargs)

        def __enter__(self):
            root = Path(self.directory.__enter__())
            (root / "alias-child").mkdir()
            return str(root / "alias-child" / "..")

        def __exit__(self, *args):
            return self.directory.__exit__(*args)

    monkeypatch.setattr(artifacts.tempfile, "TemporaryDirectory", AliasedTemporaryDirectory)
    if entrypoint == "workflow":
        _run_workflow_validator(repo, directory, dev, main, monkeypatch)
    elif entrypoint == "promote":
        report = promotion.promote(
            repo=repo, dev_ref="dev", main_ref="main", python_artifacts_dir=directory,
        )
        assert report["promoted"]
    else:
        artifacts.validate_candidate_artifacts(
            directory, repo=repo, dev_head=dev, main_before=main,
        )
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


_ARTIFACT_SOURCE_BARRIER = frozenset({
    "full", "worktree-manager", "agent-index-service-linux",
    "agent-index-service-windows", "guards-full-sweep",
})


def _assert_artifact_source_barrier(jobs: dict) -> None:
    builder_needs = jobs["build-python-artifacts"]["needs"]
    signer_needs = set(jobs["python-artifacts"]["needs"])
    assert {"gate", "build-python-artifacts"} <= signer_needs
    if builder_needs == ["gate"]:
        assert _ARTIFACT_SOURCE_BARRIER <= signer_needs
    else:
        assert {"gate", *_ARTIFACT_SOURCE_BARRIER} <= set(builder_needs)


@pytest.mark.parametrize("early_build", [False, True], ids=["legacy-builder", "early-builder"])
def test_workflow_artifact_source_barrier_accepts_supported_profiles(early_build: bool) -> None:
    _assert_artifact_source_barrier({
        "build-python-artifacts": {
            "needs": ["gate"] if early_build else ["gate", *sorted(_ARTIFACT_SOURCE_BARRIER)],
        },
        "python-artifacts": {
            "needs": ["gate", "build-python-artifacts"]
            + (sorted(_ARTIFACT_SOURCE_BARRIER) if early_build else []),
        },
    })


@pytest.mark.parametrize("missing", ["gate", "build-python-artifacts", *sorted(_ARTIFACT_SOURCE_BARRIER)])
def test_workflow_early_artifact_signer_requires_each_source_dependency(missing: str) -> None:
    with pytest.raises(AssertionError):
        _assert_artifact_source_barrier({
            "build-python-artifacts": {"needs": ["gate"]},
            "python-artifacts": {
                "needs": sorted({"gate", "build-python-artifacts", *_ARTIFACT_SOURCE_BARRIER} - {missing}),
            },
        })


@pytest.mark.parametrize("missing", sorted(_ARTIFACT_SOURCE_BARRIER))
def test_workflow_legacy_artifact_builder_requires_each_source_dependency(missing: str) -> None:
    with pytest.raises(AssertionError):
        _assert_artifact_source_barrier({
            "build-python-artifacts": {
                "needs": sorted({"gate", *_ARTIFACT_SOURCE_BARRIER} - {missing}),
            },
            "python-artifacts": {
                "needs": ["gate", "build-python-artifacts", *sorted(_ARTIFACT_SOURCE_BARRIER)],
            },
        })


def test_workflow_separates_candidate_execution_signing_and_release_authority():
    root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load((root / ".github" / "workflows" / "validate-and-promote.yml").read_text())
    jobs = workflow["jobs"]
    current = source_owned(jobs)
    builder, signer = jobs["build-python-artifacts"], jobs["python-artifacts"]
    _assert_artifact_source_barrier(jobs)
    assert builder["permissions"] == (
        {"contents": "read", "actions": "read", "attestations": "read"} if current
        else {"contents": "read"}
    )
    assert signer["permissions"] == {
        "contents": "read", "id-token": "write", "attestations": "write",
    }
    assert "environment" not in builder
    assert "APERTURE_RELEASE_TOKEN" not in json.dumps(builder)
    assert "default_branch" in builder["if"]
    assert "default_branch" in signer["if"]
    assert "environment" not in signer
    assert "APERTURE_RELEASE_TOKEN" not in json.dumps(signer)
    assert "build-python-artifacts" in signer["needs"]
    steps = signer["steps"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] is False
    validator = next(
        step for step in steps
        if step.get("name") == (
            "Validate untrusted bundles using only pinned reviewed helpers" if current
            else "Validate untrusted bundles using only trusted default-branch code"
        )
    )
    if current:
        assert_source_owned_boundary(jobs)
        assert "artifacts.validate_composed_artifacts(" in validator["run"]
        assert "checkout-index" not in validator["run"]
        assert 'trusted_source=helpers' in validator["run"]
        assert "normal artifact validation cannot bypass the pause" in validator["run"]
    else:
        assert "artifacts.validate_artifacts(" in validator["run"]
        assert '"checkout-index", "--all"' in validator["run"]
    assert 'source_policy="seasoned-public", min_age_days=7' in validator["run"]
    assert "promote_release.py" not in json.dumps(signer)
    assert "build_python_artifacts.py" not in json.dumps(signer)
    attestation = next(step for step in steps if "attest-build-provenance" in step.get("uses", ""))
    assert steps.index(validator) < steps.index(attestation)
    assert not any("attest-build-provenance" in step.get("uses", "") for step in builder["steps"])
    assert "build-python-artifacts" in jobs["report-failure"]["needs"]
    assert "needs.build-python-artifacts.result" in jobs["report-failure"]["if"]
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
    if current:
        assert "needs.qualify-candidate.result == 'success'" in publishing["if"]
        assert "merge-promotion" not in publishing["needs"]
        assert "MERGE_SHA" not in publishing["env"]
    else:
        assert "needs.merge-promotion.outputs.merge_sha != ''" in publishing["if"]
        assert publishing["env"]["MERGE_SHA"] == "${{ needs.merge-promotion.outputs.merge_sha }}"
    assert jobs["merge-promotion"]["outputs"]["merge_sha"] == "${{ steps.merged.outputs.merge_sha }}"
    assert "GH_TOKEN" not in publishing["env"]
    assert "ARTIFACT_VERIFY_TOKEN" not in publishing["env"]
    publisher_step = next(step for step in publishing["steps"] if "publish_python_artifacts.py publish" in step.get("run", ""))
    assert publisher_step["env"]["GH_TOKEN"] == "${{ secrets.APERTURE_RELEASE_TOKEN }}"
    assert publisher_step["env"]["ARTIFACT_VERIFY_TOKEN"] == "${{ github.token }}"
    assert all("env" not in step for step in publishing["steps"] if "uses" in step)
    assert "id-token" not in publishing["permissions"]
    assert "publish-python-artifacts" in jobs["report-failure"]["needs"]
    assert "promote" in jobs["report-failure"]["needs"]
    assert "needs.promote.result == 'failure'" in jobs["report-failure"]["if"]
