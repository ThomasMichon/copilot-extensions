"""Actor-free contracts for release-to-delivery source and custody."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import core_release_delivery as delivery
import core_release_inputs as intake
import promotion_bundle_members as bundle_members
import build_toolchain_queries as toolchain_queries
import pytest
from artifact_attestation import PromotionArtifactError

COMMIT = "a" * 40
DEV = "b" * 40
TREE = "c" * 40
CONTENT = "d" * 40


@pytest.mark.parametrize("outcome", ["ok", "failed", "invalid", "missing"])
def test_toolchain_versions_retain_deadline_and_validate_response(monkeypatch, outcome):
    packages = ("setuptools", "wheel")
    versions = {"setuptools": "83.0", "wheel": "0.45"}
    environment = {"TOOLCHAIN": "selected"}

    def remaining(deadline, *, error_type):
        assert deadline == 123.0 and error_type is toolchain_queries.ArtifactBuildError
        return 4.5

    def run(command, **options):
        assert command[0] == "selected-python" and command[-2:] == list(packages)
        assert options["env"] is environment
        assert options["timeout"] == 4.5
        assert options["capture_output"] and options["text"]
        return SimpleNamespace(
            returncode=9 if outcome == "failed" else 0,
            stdout="noise" if outcome == "invalid" else json.dumps(
                {} if outcome == "missing" else versions,
            ),
            stderr="selected probe failed",
        )

    monkeypatch.setattr(toolchain_queries, "remaining_timeout", remaining)
    monkeypatch.setattr(toolchain_queries.subprocess, "run", run)
    if outcome == "ok":
        assert toolchain_queries.query_versions(
            Path("selected-python"), packages, environment, deadline=123.0,
        ) == versions
    else:
        with pytest.raises(toolchain_queries.ArtifactBuildError):
            toolchain_queries.query_versions(
                Path("selected-python"), packages, environment, deadline=123.0,
            )


def test_promotion_bundle_members_keep_manifest_first_and_wheels_sorted():
    manifest = {
        "composition": "core",
        "wheels": [
            {"filename": "zeta-1-py3-none-any.whl", "role": "plugin"},
            {"filename": "alpha-1-py3-none-any.whl", "role": "vendored-lib"},
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        def write_wheel(wheel, info):
            with archive.open(info, "w") as target:
                target.write(wheel["filename"].encode())

        bundle_members._write_bundle_members(archive, manifest, write_wheel)
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [
            "manifest.json", "alpha-1-py3-none-any.whl", "zeta-1-py3-none-any.whl",
        ]
        assert archive.read("manifest.json") == bundle_members._bundle_manifest(manifest)
        assert archive.read("alpha-1-py3-none-any.whl") == b"alpha-1-py3-none-any.whl"


@pytest.mark.parametrize("failure", ["entry-id", "entry-digest", "manifest", "snapshot", "deadline"])
def test_reconstructed_core_refuses_unselected_inputs_before_use(monkeypatch, failure):
    manifest = {
        "composition": "core", "version": "1.0", "artifact_id": "sha256:" + "d" * 64,
        "wheels": [{
            "filename": "sample-1-py3-none-any.whl", "role": "plugin", "sha256": "sha256:" + "e" * 64,
        }],
    }
    monkeypatch.setattr(intake, "_core_manifest", lambda _data: manifest)
    raw = bundle_members._bundle_manifest(manifest)
    entry = {
        "composition": "core", "version": manifest["version"], "artifact_id": manifest["artifact_id"],
        "filename": intake._bundle_filename(manifest), "sha256": "f" * 64,
    }
    deadline = time.monotonic() + 60
    if failure == "entry-id":
        entry["artifact_id"] = "sha256:" + "0" * 64
    elif failure == "entry-digest":
        entry["sha256"] = "not-a-digest"
    elif failure == "manifest":
        raw += b" "
    elif failure == "deadline":
        deadline = time.monotonic() - 1
    expected = {
        "entry-id": "differs from its selected source entry",
        "entry-digest": "differs from its selected source entry",
        "manifest": "requires the exact canonical manifest bytes",
        "snapshot": "requires a retained wheel snapshot",
        "deadline": "deadline exhausted or invalid",
    }[failure]
    with pytest.raises(ValueError, match=expected), intake.retain_reconstructed_core(
        manifest_data=raw, entry=entry, wheels=None, deadline=deadline, max_bytes=4096, max_wheels=2,
    ):
        pytest.fail("unselected core was exposed")


@pytest.mark.parametrize("duplicate", [False, True], ids=["valid", "duplicate-state"])
def test_selected_source_derives_dev_and_content_from_committed_state(monkeypatch, duplicate):
    raw = json.dumps({"last_promotion": {"dev_head": DEV}})
    if duplicate:
        raw = '{"last_promotion":{"dev_head":"' + DEV + '","dev_head":"' + DEV + '"}}'
    calls = []

    def git(_repository, *arguments, deadline):
        calls.append(arguments)
        if arguments[0] == "rev-parse":
            return TREE if arguments[1].endswith("^{tree}") else COMMIT
        if arguments[0] == "merge-base":
            return ""
        if arguments[0] == "cat-file":
            return str(len(raw))
        if arguments[0] == "show":
            return raw
        raise AssertionError(arguments)

    monkeypatch.setattr(delivery, "_git", git)
    monkeypatch.setattr(delivery, "_content_tree", lambda _repo, tree, **_kw: (
        CONTENT if tree == TREE else pytest.fail("wrong source tree")
    ))
    if duplicate:
        with pytest.raises(ValueError):
            delivery._selected_source("fixture", COMMIT, COMMIT, deadline=time.monotonic() + 60)
        assert not any(args[0] == "rev-parse" and args[1].endswith("^{tree}") for args in calls)
    else:
        assert delivery._selected_source(
            "fixture", COMMIT, COMMIT, deadline=time.monotonic() + 60,
        ) == (DEV, CONTENT)


@pytest.mark.parametrize("fail_cleanup", [False, True], ids=["success", "cleanup-failure"])
def test_publication_follows_release_custody_exit(tmp_path, monkeypatch, fail_cleanup):
    repository = tmp_path / "source"
    repository.mkdir()
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    destination = tmp_path / "delivery.zip"
    content = b"candidate delivery"
    events = []
    core = SimpleNamespace(
        manifest_path=repository / "manifest.json", wheel_dir=repository / "wheels",
        index_path=repository / "index.json",
    )
    monkeypatch.setattr(delivery, "_selected_source", lambda *_a, **_kw: (DEV, CONTENT))

    @contextmanager
    def temporary(_parent):
        yield stage_root

    @contextmanager
    def retain(**options):
        assert options["dev_head"] == DEV and options["content_tree"] == CONTENT
        events.append("retained")
        yield core
        assert not destination.exists()
        events.append("released")
        if fail_cleanup:
            raise OSError("release cleanup failed")

    def prepare(*args, **kwargs):
        assert events == ["retained"]
        assert args[3:5] == (core.manifest_path, core.wheel_dir)
        assert kwargs["artifact_index"] == core.index_path
        args[7].write_bytes(content)
        events.append("built")
        return {
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size": len(content), "approval": "unattested",
        }

    original_link = os.link

    def link(source, target):
        events.append("linked")
        original_link(source, target)

    monkeypatch.setattr(delivery.delivery.native.dependencies, "_temporary_inputs", temporary)
    monkeypatch.setattr(delivery.delivery.native.dependencies, "_private_retained_root", lambda path: path)
    monkeypatch.setattr(delivery.delivery.source_owner, "native_retained_path", str)
    monkeypatch.setattr(delivery, "retain_released_core", retain)
    monkeypatch.setattr(delivery.delivery, "prepare_core_delivery", prepare)
    monkeypatch.setattr(delivery, "_open", lambda path: path.open("rb"))
    monkeypatch.setattr(delivery.os, "link", link)
    options = {
        "repository": "example/plugins", "source_checkout": repository, "source_commit": COMMIT,
        "main_head": COMMIT, "materials": {}, "material_paths": {}, "destination": destination,
        "uv": "fixture", "download_origins": (), "deadline": time.monotonic() + 60,
        "max_bytes": 1024, "native_lock": repository / "lock.toml",
    }
    if fail_cleanup:
        with pytest.raises(OSError, match="release cleanup failed"):
            delivery.prepare_release_delivery(**options)
        assert events == ["retained", "built", "released"] and not destination.exists()
    else:
        result = delivery.prepare_release_delivery(**options)
        assert events == ["retained", "built", "released", "linked"]
        assert destination.read_bytes() == content and result["approval"] == "unattested"


def test_release_intake_timeout_is_sanitized(tmp_path, monkeypatch):
    @contextmanager
    def temporary(_parent):
        yield tmp_path

    def request(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["gh", "sensitive-request-details"], 1)

    def discover(*_args, runner, **_kwargs):
        return runner(["api", "fixture"], verification=True)

    monkeypatch.setattr(intake, "_temporary_inputs", temporary)
    monkeypatch.setattr(intake, "_private_retained_root", lambda path: path)
    monkeypatch.setattr(intake, "native_retained_path", str)
    monkeypatch.setattr(intake, "_gh", request)
    monkeypatch.setattr(intake, "read_admitted_source_index", discover)
    with (
        pytest.raises(PromotionArtifactError, match="timed out") as failure,
        intake.retain_released_core(
            repository="example/plugins", dev_head=DEV, main_head=COMMIT, content_tree=CONTENT,
            is_ancestor=lambda _a, _b: True, deadline=time.monotonic() + 60,
            max_bytes=1024, max_wheels=2,
        ),
    ):
        pytest.fail("timed-out release inputs were exposed")
    assert "sensitive-request-details" not in str(failure.value)


def test_cli_sanitizes_unexpected_child_timeout(monkeypatch, capsys):
    def timeout(**_kwargs):
        raise subprocess.TimeoutExpired(["child", "sensitive-request-details"], 1)

    monkeypatch.setattr(delivery, "_read_json", lambda *_args: {})
    monkeypatch.setattr(delivery, "prepare_release_delivery", timeout)
    with pytest.raises(SystemExit) as failure:
        delivery.main([
            "--repository", "example/plugins", "--source-checkout", "fixture",
            "--source-commit", COMMIT, "--main-head", COMMIT, "--materials", "materials.json",
            "--material-dir", "materials", "--guarded-feed", "--uv", "fixture",
            "--output", "delivery.zip",
        ])
    error = capsys.readouterr().err
    assert failure.value.code == 1 and "release delivery refused" in error and "timed out" in error
    assert "sensitive-request-details" not in error
