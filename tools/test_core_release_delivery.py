"""A released core feeds a staged, source-bound native delivery."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

import core_release_inputs as intake
import promote_release as promotion
import pytest
from runtime_profile import ProfileError
from test_core_native_delivery import _fixture_git
from test_core_native_delivery import delivery as original_delivery
from test_core_release_inputs import DEV, release  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def delivery(tmp_path, monkeypatch, request):
    import test_core_python_artifacts as fixtures

    original = fixtures._fixture

    def tracked_fixture(root):
        source = original(root)
        for directory in sorted(root.rglob("*")):
            if directory.is_dir() and not any(directory.iterdir()):
                (directory / ".fixture").write_bytes(b"tracked fixture resource\n")
        return source

    monkeypatch.setattr(fixtures, "_fixture", tracked_fixture)
    return original_delivery.__wrapped__(tmp_path, monkeypatch, request)


@pytest.fixture
def selected_release(tmp_path, monkeypatch, release):  # noqa: F811
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required")
    producer, arguments, server, proofs, entry, _options = release
    repository = tmp_path / "source-repository"
    repository.mkdir()
    for name in (".github", "plugins", "libs", "services"):
        source = tmp_path / name
        if source.exists():
            shutil.copytree(source, repository / name)
    state = repository / ".github" / "release-pipeline-state.json"
    state.write_text(json.dumps({"last_promotion": {"dev_head": DEV}}), encoding="utf-8")
    _fixture_git(repository, git, "init", "-q")
    _fixture_git(repository, git, "add", "-A")
    _fixture_git(repository, git, "commit", "-qm", "selected source")
    commit = _fixture_git(repository, git, "rev-parse", "HEAD")
    tree = _fixture_git(repository, git, "rev-parse", "HEAD^{tree}")
    content_tree = promotion._tree_excluding_state(tree, repo=repository)
    index_asset = server.assets["python-artifacts.json"]
    index = json.loads(index_asset["payload"])
    index["content_tree"] = content_tree
    index["admission"]["signer_digest"] = commit
    index["admission"]["source_digest"] = commit
    data = json.dumps(index).encode()
    index_asset.update(payload=data, size=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest())

    def verify(path, *, repository, signer_digest, source_digest, source_ref, runner):
        assert path.read_bytes() == server.assets[path.name]["payload"]
        assert (repository, signer_digest, source_digest, source_ref) == (
            "example/plugins", commit, commit, "refs/heads/main",
        )
        proofs.append(path.name)

    monkeypatch.setattr(intake, "verify_attestation_subject", verify)
    options = {
        "repository": "example/plugins",
        "source_checkout": repository,
        "source_commit": commit,
        "main_head": commit,
        "materials": {},
        "material_paths": {},
        "destination": tmp_path / "released-delivery.zip",
        "uv": "fixture-uv",
        "download_origins": (),
        "deadline": time.monotonic() + 120,
        "max_bytes": 4 << 20,
        "max_wheels": 256,
        "native_lock": arguments["native_lock"],
    }
    return producer, server, proofs, entry, state, options, git


def test_released_core_is_retained_and_staged_through_real_producer(selected_release, monkeypatch):
    import core_release_delivery as orchestration

    producer, server, proofs, entry, state, options, _git = selected_release
    state.write_text(json.dumps({"last_promotion": {"dev_head": "f" * 40}}), encoding="utf-8")
    original = producer._build_from_snapshot
    held = []

    def observe(*args, **kwargs):
        selected = inspect.signature(original).bind(*args, **kwargs).arguments
        index, wheels = selected["artifact_index"], selected["wheel_dir"]
        assert index.is_file() and any(wheels.iterdir())
        assert not options["destination"].exists()
        result = original(*args, **kwargs)
        assert selected["destination"].is_file() and not options["destination"].exists()
        held.extend((index, wheels))
        return result

    monkeypatch.setattr(producer, "_build_from_snapshot", observe)
    result = orchestration.prepare_release_delivery(**options)
    assert proofs == ["python-artifacts.json", entry["filename"]]
    assert not server.writes and held and all(not path.exists() for path in held)
    assert result["approval"] == "unattested" and result["source_commit"] == options["source_commit"]
    with zipfile.ZipFile(options["destination"]) as archive:
        profile = json.loads(archive.read("profile.json"))
    assert result["candidate_index"]["runtime_profiles"]["profiles"] == [profile]


@pytest.mark.parametrize("kind", ["missing", "malformed", "duplicate", "bad-dev"])
def test_release_delivery_refuses_unusable_committed_state_before_network(
    selected_release, kind,
):
    import core_release_delivery as orchestration

    _producer, server, _proofs, _entry, state, options, git = selected_release
    if kind == "missing":
        state.unlink()
    elif kind == "malformed":
        state.write_text("{", encoding="utf-8")
    elif kind == "duplicate":
        state.write_text(
            '{"last_promotion":{"dev_head":"' + DEV + '","dev_head":"' + DEV + '"}}',
            encoding="utf-8",
        )
    else:
        state.write_text(json.dumps({"last_promotion": {"dev_head": "z" * 40}}), encoding="utf-8")
    _fixture_git(options["source_checkout"], git, "add", "-A")
    _fixture_git(options["source_checkout"], git, "commit", "-qm", "invalid source state")
    commit = _fixture_git(options["source_checkout"], git, "rev-parse", "HEAD")
    options.update(source_commit=commit, main_head=commit)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        orchestration.prepare_release_delivery(**options)
    assert not server.calls and not options["destination"].exists()


def test_release_delivery_rejects_source_outside_selected_main_before_network(selected_release):
    import core_release_delivery as orchestration

    _producer, server, _proofs, _entry, _state, options, git = selected_release
    tree = _fixture_git(options["source_checkout"], git, "rev-parse", "HEAD^{tree}")
    options["main_head"] = _fixture_git(
        options["source_checkout"], git, "commit-tree", tree, "-m", "unrelated history",
    )
    with pytest.raises((ValueError, RuntimeError, OSError)):
        orchestration.prepare_release_delivery(**options)
    assert not server.calls and not options["destination"].exists()


def test_source_selection_ignores_foreign_git_selectors(selected_release, monkeypatch, tmp_path):
    import core_release_delivery as orchestration

    _producer, server, _proofs, _entry, _state, options, git = selected_release
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _fixture_git(foreign, git, "init", "-q")
    (foreign / "sentinel").write_bytes(b"foreign")
    _fixture_git(foreign, git, "add", "-A")
    _fixture_git(foreign, git, "commit", "-qm", "foreign source")
    foreign_index = foreign / ".git" / "index"
    original = foreign_index.read_bytes()
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign_index))
    selected = orchestration._selected_source(
        options["source_checkout"], options["source_commit"], options["main_head"],
        deadline=options["deadline"],
    )
    expected_tree = json.loads(server.assets["python-artifacts.json"]["payload"])["content_tree"]
    assert selected == (DEV, expected_tree)
    assert foreign_index.read_bytes() == original


def test_source_git_timeout_is_a_clean_refusal(selected_release, monkeypatch):
    import core_release_delivery as orchestration

    _producer, server, _proofs, _entry, _state, options, _git = selected_release

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(orchestration.source, "_git", timeout)
    with pytest.raises(ProfileError, match="source Git.*timed out"):
        orchestration.prepare_release_delivery(**options)
    assert not server.calls and not options["destination"].exists()


def test_release_delivery_never_clobbers_existing_output_before_network(selected_release):
    import core_release_delivery as orchestration

    _producer, server, _proofs, _entry, _state, options, _git = selected_release
    options["destination"].write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        orchestration.prepare_release_delivery(**options)
    assert options["destination"].read_bytes() == b"existing" and not server.calls


@pytest.mark.parametrize("kind", ["late-index", "input-cleanup", "output-collision"])
def test_release_delivery_publishes_only_after_intake_cleanup(
    selected_release, monkeypatch, kind,
):
    import core_release_delivery as orchestration

    producer, _server, _proofs, _entry, _state, options, _git = selected_release
    original_build = producer._build_from_snapshot
    built = []

    def observe(*args, **kwargs):
        result = original_build(*args, **kwargs)
        assert kwargs["destination"].exists() and not options["destination"].exists()
        built.append(True)
        return result

    monkeypatch.setattr(producer, "_build_from_snapshot", observe)
    if kind == "input-cleanup":
        temporary_inputs = intake._temporary_inputs

        @contextmanager
        def cleanup_failure(*args, **kwargs):
            with temporary_inputs(*args, **kwargs) as path:
                yield path
            raise OSError("input cleanup failed")

        monkeypatch.setattr(intake, "_temporary_inputs", cleanup_failure)
    else:
        retain = orchestration.retain_released_core

        @contextmanager
        def late_change(**kwargs):
            with retain(**kwargs) as core:
                yield core
                if kind == "late-index":
                    core.index_path.write_bytes(b"changed")
                else:
                    options["destination"].write_bytes(b"other producer")

        monkeypatch.setattr(orchestration, "retain_released_core", late_change)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        orchestration.prepare_release_delivery(**options)
    assert built
    if kind == "output-collision":
        assert options["destination"].read_bytes() == b"other producer"
    else:
        assert not options["destination"].exists()


def test_release_delivery_rechecks_staged_bytes_after_intake(selected_release, monkeypatch):
    import core_release_delivery as orchestration

    producer, _server, _proofs, _entry, _state, options, _git = selected_release
    original = producer._build_from_snapshot
    changed = []

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        kwargs["destination"].write_bytes(b"changed after producer")
        changed.append(True)
        return result

    monkeypatch.setattr(producer, "_build_from_snapshot", mutate)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        orchestration.prepare_release_delivery(**options)
    assert changed and not options["destination"].exists()


def test_release_delivery_keeps_material_custody_before_publication(selected_release, monkeypatch, tmp_path):
    import core_release_delivery as orchestration

    producer, _server, _proofs, entry, _state, options, _git = selected_release
    material = tmp_path / "NOTICE.txt"
    material.write_bytes(b"approved material bytes")
    with zipfile.ZipFile(tmp_path / "release-assets" / entry["filename"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    options["materials"] = {
        material.name: {
            "kind": "notice", "wheels": [manifest["wheels"][0]["sha256"]],
            "sha256": "sha256:" + hashlib.sha256(material.read_bytes()).hexdigest(),
            "size": material.stat().st_size,
        },
    }
    options["material_paths"] = {material.name: material}
    original = producer.build_profile_delivery
    mutated = []

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        mutated.append(True)
        material.write_bytes(b"changed after delivery")
        return result

    monkeypatch.setattr(producer, "build_profile_delivery", mutate)
    with pytest.raises((ValueError, RuntimeError, OSError)) as failure:
        orchestration.prepare_release_delivery(**options)
    assert mutated, str(failure.value)
    assert not options["destination"].exists()


def test_release_delivery_cli_passes_explicit_inputs(tmp_path, monkeypatch, capsys):
    import core_release_delivery as orchestration

    materials = tmp_path / "materials.json"
    materials.write_text("{}", encoding="utf-8")
    captured = []
    signature = inspect.signature(orchestration.prepare_release_delivery)

    def prepare(*args, **kwargs):
        captured.append(signature.bind(*args, **kwargs).arguments)
        return {"approval": "unattested"}

    monkeypatch.setattr(orchestration, "prepare_release_delivery", prepare)
    code = orchestration.main([
        "--repository", "example/plugins", "--source-checkout", str(tmp_path),
        "--source-commit", "a" * 40, "--main-head", "b" * 40,
        "--materials", str(materials), "--material-dir", str(tmp_path),
        "--offline-wheel-pool", str(tmp_path / "pool.json"), "--uv", "fixture-uv",
        "--output", str(tmp_path / "delivery.zip"), "--timeout", "60", "--max-bytes", "1024",
    ])
    assert code == 0 and json.loads(capsys.readouterr().out) == {"approval": "unattested"}
    assert captured[0]["repository"] == "example/plugins"
    assert captured[0]["source_commit"] == "a" * 40 and captured[0]["main_head"] == "b" * 40
    assert captured[0]["materials"] == {} and captured[0]["material_paths"] == {}
    assert captured[0]["offline_wheel_pool"] == tmp_path / "pool.json"


def test_release_delivery_command_imports_without_ambient_python_paths(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools" / "core_release_delivery.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "--repository" in result.stdout and "--guarded-feed" in result.stdout
