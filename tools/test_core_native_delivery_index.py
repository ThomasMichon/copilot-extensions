"""Native core delivery and release-index candidate handoff."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from runtime_profile import validate_profile
from test_core_guarded_feed import _resolve
from test_core_native_delivery import (
    _configure_native_tls,
    _fixture_git,
    _write_offline_pool,
    delivery,  # noqa: F401
    tls_source,  # noqa: F401
)
from test_installer_wheel_acquisition import feeds as local_tls_feed  # noqa: F401


def _index(manifest):
    return {
        "schema": "copilot-extensions.promotion-python-artifacts",
        "schema_version": 2,
        "dev_head": "a" * 40,
        "content_tree": "b" * 40,
        "artifacts": [{"artifact_id": manifest["artifact_id"]}],
    }


def _selected_index(arguments):
    path = arguments["source"] / "source-index.json"
    index = _index(json.loads(arguments["manifest_data"]))
    path.write_text(json.dumps(index), encoding="utf-8")
    state = arguments["source"] / ".github" / "release-pipeline-state.json"
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({"last_promotion": {"dev_head": index["dev_head"]}}), encoding="utf-8")
    arguments["artifact_index"] = path
    return path, index


def test_native_delivery_emits_the_matching_index_candidate(delivery):  # noqa: F811
    producer, arguments, _calls = delivery
    path, original = _selected_index(arguments)
    original_bytes = path.read_bytes()

    result = producer._build_from_snapshot(**arguments)

    candidate = result["candidate_index"]
    content = json.dumps(
        candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    assert result["candidate_index_sha256"] == "sha256:" + hashlib.sha256(content).hexdigest()
    assert result["source_index_sha256"] == "sha256:" + hashlib.sha256(original_bytes).hexdigest()
    assert {key: value for key, value in candidate.items() if key != "runtime_profiles"} == original
    assert path.read_bytes() == original_bytes
    with zipfile.ZipFile(arguments["destination"]) as archive:
        profile = json.loads(archive.read("profile.json"))
    assert candidate["runtime_profiles"]["profiles"] == [profile]
    assert validate_profile(profile).lock_id == result["profile_sha256"]
    assert result["approval"] == "unattested"


@pytest.mark.parametrize("kind", ["duplicate", "oversized", "wrong-source", "missing-source", "unbound"])
def test_native_delivery_refuses_bad_source_indexes_without_output(delivery, kind):  # noqa: F811
    producer, arguments, calls = delivery
    path, index = _selected_index(arguments)
    if kind == "duplicate":
        path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    elif kind == "oversized":
        path.write_bytes(b" " * ((1 << 20) + 1))
    elif kind == "wrong-source":
        index["dev_head"] = "d" * 40
        path.write_text(json.dumps(index), encoding="utf-8")
    elif kind == "missing-source":
        (arguments["source"] / ".github" / "release-pipeline-state.json").unlink()
    else:
        index["artifacts"][0]["artifact_id"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises((ValueError, OSError)):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()
    if kind != "unbound":
        assert not calls


@pytest.mark.parametrize("kind", ["index", "release-state"])
def test_native_delivery_retains_the_source_index_until_publication(delivery, monkeypatch, kind):  # noqa: F811
    producer, arguments, _calls = delivery
    path, _index_record = _selected_index(arguments)
    if kind == "release-state":
        path = arguments["source"] / ".github" / "release-pipeline-state.json"
    original = producer.build_profile_delivery

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text("changed", encoding="utf-8")
        return result

    monkeypatch.setattr(producer, "build_profile_delivery", mutate)
    with pytest.raises((ValueError, OSError)):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


@pytest.mark.parametrize("kind", ["index", "release-state"])
def test_guarded_delivery_retains_index_inputs_during_final_verification(
    tmp_path, monkeypatch, tls_source, kind,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    index, _ = _selected_index({"source": tmp_path, "manifest_data": json.dumps(manifest)})
    path = index if kind == "index" else tmp_path / ".github" / "release-pipeline-state.json"
    original = producer.verify_guarded_projects
    attempts = []

    def mutate(*args, **kwargs):
        original(*args, **kwargs)
        attempts.append(True)
        path.write_text("changed", encoding="utf-8")

    monkeypatch.setattr(producer, "verify_guarded_projects", mutate)
    _configure_native_tls(tmp_path, monkeypatch, state)
    with pytest.raises((ValueError, OSError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv, artifact_index=index)
    assert attempts
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_delivery_finishes_custody_checks_before_publication(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    index, _ = _selected_index({"source": tmp_path, "manifest_data": json.dumps(manifest)})
    original_held = producer._held
    original_verify = producer.verify_guarded_projects
    verified = []

    @contextmanager
    def fail_on_exit(paths, files, deadline):
        with original_held(paths, files, deadline) as held:
            yield held
        if "artifact-index" in paths:
            raise RuntimeError("final custody check failed")

    def verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        verified.append(True)
        return result

    monkeypatch.setattr(producer, "_held", fail_on_exit)
    monkeypatch.setattr(producer, "verify_guarded_projects", verify)
    _configure_native_tls(tmp_path, monkeypatch, state)
    with pytest.raises(RuntimeError, match="final custody check failed"):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv, artifact_index=index)
    assert verified
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_actual_command_emits_index_for_the_delivered_profile(tmp_path, monkeypatch, tls_source):  # noqa: F811
    uv, git = shutil.which("uv"), shutil.which("git")
    if uv is None or git is None:
        pytest.skip("native uv and git are required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    release_state = tmp_path / ".github" / "release-pipeline-state.json"
    release_state.parent.mkdir(exist_ok=True)
    release_state.write_text(json.dumps({"last_promotion": {"dev_head": "a" * 40}}), encoding="utf-8")
    _fixture_git(tmp_path, git, "init", "-q")
    _fixture_git(tmp_path, git, "add", "-A")
    _fixture_git(tmp_path, git, "commit", "-qm", "fixture source")
    commit = _fixture_git(tmp_path, git, "rev-parse", "HEAD")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pool = _write_offline_pool(tmp_path, wheel, digest)
    _configure_native_tls(tmp_path, monkeypatch, state)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))
    materials = tmp_path / "materials.json"
    materials.write_text("{}", encoding="utf-8")
    index = tmp_path / "source-index.json"
    index.write_text(json.dumps(_index(manifest)), encoding="utf-8")
    output = tmp_path / "delivery.zip"
    command = [
        sys.executable, "-I", str(Path(producer.__file__)),
        "--source-checkout", str(tmp_path), "--source-commit", commit, "--main-head", commit,
        "--core-manifest", str(manifest_path), "--wheel-dir", str(wheels),
        "--materials", str(materials), "--material-dir", str(tmp_path),
        "--offline-wheel-pool", str(pool), "--uv", uv, "--output", str(output),
        "--artifact-index", str(index), "--timeout", "120", "--max-bytes", str(4 << 20),
    ]
    result = subprocess.run(
        command, env=dict(os.environ), capture_output=True, text=True, timeout=140, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)
    with zipfile.ZipFile(output) as archive:
        profile = json.loads(archive.read("profile.json"))
    assert result["candidate_index"]["runtime_profiles"]["profiles"] == [profile]
    assert result["approval"] == "unattested"
