"""Authenticated first-party core input for the existing delivery producer."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import core_release_inputs as intake
import promotion_python_artifacts as artifacts
import pytest
from test_core_native_delivery import delivery  # noqa: F401
from test_promotion_artifact_readiness import HostedRelease
from test_promotion_python_artifacts import _wheel_bytes

DEV = "a" * 40
TREE = "b" * 40
SIGNER = "c" * 40
MAIN = "d" * 40
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release(tmp_path, monkeypatch, delivery):  # noqa: F811
    producer, arguments, _calls = delivery
    directory = tmp_path / "release-assets"
    directory.mkdir()
    manifest = json.loads(arguments["manifest_data"])
    entry = artifacts.bundle_wheels(manifest, arguments["wheel_dir"], directory)
    index = {
        "schema": "copilot-extensions.promotion-python-artifacts",
        "schema_version": 2,
        "dev_head": DEV,
        "content_tree": TREE,
        "admission": {
            "signer_digest": SIGNER, "source_digest": SIGNER, "source_ref": "refs/heads/main",
        },
        "artifacts": [entry],
    }
    (directory / "python-artifacts.json").write_text(json.dumps(index), encoding="utf-8")
    server = HostedRelease(directory)
    server.tag = "python-artifacts-" + DEV
    server.target = DEV
    server.release = {"id": 1, "tag_name": server.tag, "draft": False, "prerelease": True, "assets": []}
    for number, path in enumerate(sorted(directory.iterdir()), start=1):
        data = path.read_bytes()
        server.assets[path.name] = {
            "id": number, "name": path.name, "state": "uploaded", "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "payload": data,
        }
    proofs = []

    def verify(path, *, repository, signer_digest, source_digest, source_ref, runner):
        proofs.append(path.name)
        assert path.read_bytes() == server.assets[path.name]["payload"]
        assert (repository, signer_digest, source_digest, source_ref) == (
            "example/plugins", SIGNER, SIGNER, "refs/heads/main",
        )

    monkeypatch.setattr(intake, "_gh", server.gh)
    monkeypatch.setattr(intake, "verify_attestation_subject", verify)
    options = {
        "repository": "example/plugins", "dev_head": DEV, "main_head": MAIN,
        "content_tree": TREE, "is_ancestor": lambda ancestor, descendant: (
            ancestor == SIGNER and descendant == MAIN
        ),
        "deadline": time.monotonic() + 120, "max_bytes": 4 << 20, "max_wheels": 256,
    }
    return producer, arguments, server, proofs, entry, options


def test_release_intake_feeds_existing_core_producer(release):
    producer, arguments, server, proofs, entry, options = release
    destination = arguments["destination"]
    arguments["destination"] = destination.with_name("staged-delivery.zip")
    with intake.retain_released_core(**options) as core:
        assert proofs == ["python-artifacts.json", entry["filename"]]
        assert json.loads(core.manifest_data)["artifact_id"] == entry["artifact_id"]
        assert core.manifest_path.read_bytes() == core.manifest_data
        assert {path.name for path in core.wheel_dir.iterdir()} == {
            row["filename"] for row in json.loads(core.manifest_data)["wheels"]
        }
        arguments["manifest_data"] = core.manifest_data
        arguments["wheel_dir"] = core.wheel_dir
        result = producer._build_from_snapshot(**arguments)
        assert result["approval"] == "unattested"
        index_path, manifest_path, wheel_dir = core.index_path, core.manifest_path, core.wheel_dir
    assert arguments["destination"].exists() and not destination.exists()
    assert not index_path.exists() and not manifest_path.exists() and not wheel_dir.exists()
    assert not server.writes


@pytest.fixture
def reconstruction(release, tmp_path):
    _producer, arguments, server, proofs, entry, options = release
    with zipfile.ZipFile(io.BytesIO(server.assets[entry["filename"]]["payload"])) as archive:
        manifest_data = archive.read("manifest.json")
    manifest = json.loads(manifest_data)
    paths = {row["filename"]: arguments["wheel_dir"] / row["filename"] for row in manifest["wheels"]}
    expected = {row["filename"]: row["sha256"] for row in manifest["wheels"]}
    extra = tmp_path / "extra-1.0-py3-none-any.whl"
    extra.write_bytes(_wheel_bytes("extra", "1.0"))
    paths[extra.name] = extra
    expected[extra.name] = "sha256:" + hashlib.sha256(extra.read_bytes()).hexdigest()
    with intake.retain_wheel_inputs(
        paths, expected, max_wheels=256, max_archive_bytes=4 << 20,
        max_total_bytes=4 << 20, max_snapshot_bytes=4 << 20,
        deadline=options["deadline"],
    ) as snapshot:
        yield {
            "manifest_data": manifest_data, "entry": entry, "wheels": snapshot,
            "deadline": options["deadline"], "max_bytes": 4 << 20, "max_wheels": 256,
        }, server, proofs


def test_reconstructed_core_matches_source_and_remains_available_for_attestation(reconstruction):
    options, server, proofs = reconstruction
    with intake.retain_reconstructed_core(**options) as path:
        assert path.read_bytes() == server.assets[options["entry"]["filename"]]["payload"]
        intake.verify_attestation_subject(
            path, repository="example/plugins", signer_digest=SIGNER,
            source_digest=SIGNER, source_ref="refs/heads/main", runner=server.gh,
        )
        assert proofs == [options["entry"]["filename"]]
        assert not server.calls
    assert not path.exists()


@pytest.mark.parametrize("failure", [
    "digest", "identity", "filename", "version", "manifest", "missing-wheel",
    "bytes", "count", "deadline",
])
def test_reconstruction_refuses_changed_or_over_budget_inputs(reconstruction, failure):
    options, _server, _proofs = reconstruction
    options = dict(options)
    options["entry"] = dict(options["entry"])
    if failure == "digest":
        options["entry"]["sha256"] = "0" * 64
    elif failure == "identity":
        options["entry"]["artifact_id"] = "sha256:" + "f" * 64
    elif failure == "filename":
        options["entry"]["filename"] = "other.zip"
    elif failure == "version":
        options["entry"]["version"] = "999"
    elif failure == "manifest":
        options["manifest_data"] = json.dumps(json.loads(options["manifest_data"]), indent=2).encode()
    elif failure == "missing-wheel":
        missing = json.loads(options["manifest_data"])["wheels"][0]["filename"]
        options["wheels"] = options["wheels"].subset(tuple(
            name for name in options["wheels"].paths if name != missing
        ))
    elif failure == "bytes":
        names = {row["filename"] for row in json.loads(options["manifest_data"])["wheels"]}
        options["max_bytes"] = len(options["manifest_data"]) + sum(
            os.fstat(options["wheels"].archives[name].fileno()).st_size for name in names
        )
    elif failure == "count":
        options["max_wheels"] = 1
    else:
        options["deadline"] = time.monotonic() - 1
    with pytest.raises((ValueError, OSError)), intake.retain_reconstructed_core(**options):
        pytest.fail("unmatched core bytes were exposed")


def test_reconstruction_holds_its_subject_until_exit(reconstruction):
    options, _server, _proofs = reconstruction
    with pytest.raises((ValueError, OSError)), intake.retain_reconstructed_core(**options) as path:
        path.write_bytes(b"changed")


def test_reconstruction_obeys_its_own_deadline_during_final_wheel_check(reconstruction, monkeypatch):
    options, _server, _proofs = reconstruction
    deadline = time.monotonic() + 10
    assert deadline < options["wheels"]._deadline
    verify = intake.WheelSnapshot.verify
    calls = []

    def expire_during_final_check(snapshot):
        calls.append(None)
        if len(calls) == 3:
            with monkeypatch.context() as scope:
                scope.setattr(time, "monotonic", lambda: deadline + 1)
                return verify(snapshot)
        return verify(snapshot)

    monkeypatch.setattr(intake.WheelSnapshot, "verify", expire_during_final_check)
    with pytest.raises(ValueError, match="deadline"):
        with intake.retain_reconstructed_core(**(options | {"deadline": deadline})):
            pass
    assert len(calls) == 3


def test_reconstruction_rejects_a_different_retained_wheel(reconstruction, tmp_path):
    options, _server, _proofs = reconstruction
    original = options["wheels"]
    name = json.loads(options["manifest_data"])["wheels"][0]["filename"]
    changed = tmp_path / name
    changed.write_bytes(b"different wheel bytes")
    paths = dict(original.paths)
    paths[name] = changed
    expected = dict(original.digests)
    expected[name] = "sha256:" + hashlib.sha256(changed.read_bytes()).hexdigest()
    with intake.retain_wheel_inputs(
        paths, expected, max_wheels=256, max_archive_bytes=4 << 20,
        max_total_bytes=4 << 20, max_snapshot_bytes=4 << 20, deadline=options["deadline"],
    ) as snapshot:
        with pytest.raises(ValueError, match="retained wheels differ"), \
                intake.retain_reconstructed_core(**(options | {"wheels": snapshot})):
            pytest.fail("different wheel was exposed")


def test_authenticated_core_still_requires_the_selected_source(release):
    producer, arguments, _server, _proofs, _entry, options = release
    source = arguments["source"] / "plugins" / "agent-sample" / "src" / "agent_sample" / "__init__.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with intake.retain_released_core(**options) as core:
        arguments["manifest_data"] = core.manifest_data
        arguments["wheel_dir"] = core.wheel_dir
        with pytest.raises(artifacts.PromotionArtifactError, match="payload hash differs"):
            producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


@pytest.mark.parametrize("failure", [
    "missing", "permission", "bundle-digest", "bundle-signature", "source", "ancestry", "manifest-identity",
])
def test_release_intake_rejects_untrusted_or_unavailable_inputs(release, monkeypatch, failure):
    _producer, _arguments, server, proofs, entry, options = release
    if failure == "missing":
        server.release = None
    elif failure == "permission":
        server.failure = "permission"
    elif failure == "source":
        options["content_tree"] = "e" * 40
    elif failure == "ancestry":
        options["is_ancestor"] = lambda _ancestor, _descendant: False
    elif failure == "manifest-identity":
        asset = server.assets["python-artifacts.json"]
        index = json.loads(asset["payload"])
        index["artifacts"][0]["artifact_id"] = "sha256:" + "f" * 64
        data = json.dumps(index).encode()
        asset.update(payload=data, size=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest())
    elif failure == "bundle-signature":
        original = intake.verify_attestation_subject

        def verify(path, **identity):
            if path.name == entry["filename"]:
                raise artifacts.PromotionArtifactError("bundle signature refused")
            return original(path, **identity)

        monkeypatch.setattr(intake, "verify_attestation_subject", verify)
    else:
        original = server.gh
        bundle_id = str(server.assets[entry["filename"]]["id"])

        def gh(argv, **kwargs):
            result = original(argv, **kwargs)
            if kwargs.get("binary") and any(value.endswith("/" + bundle_id) for value in argv):
                return subprocess.CompletedProcess(argv, 0, b"tampered", b"")
            return result

        monkeypatch.setattr(intake, "_gh", gh)
    with pytest.raises((artifacts.PromotionArtifactError, ValueError, OSError)), \
            intake.retain_released_core(**options):
        pytest.fail("untrusted core inputs were exposed")
    assert not server.writes
    if failure in ("missing", "permission", "source", "ancestry"):
        assert not proofs


def test_release_intake_rejects_index_mutation_during_bundle_verification(release, monkeypatch):
    _producer, _arguments, _server, _proofs, entry, options = release
    original = intake.verify_attestation_subject

    def mutate(path, **identity):
        original(path, **identity)
        if path.name == entry["filename"]:
            (path.parent / "python-artifacts.json").write_bytes(b"changed")

    monkeypatch.setattr(intake, "verify_attestation_subject", mutate)
    with pytest.raises((artifacts.PromotionArtifactError, ValueError, OSError)), \
            intake.retain_released_core(**options):
        pytest.fail("changed source index was exposed")


def test_release_intake_rejects_an_extra_archive_member(release, tmp_path):
    _producer, _arguments, server, _proofs, entry, options = release
    bundle = server.assets[entry["filename"]]
    archive = io.BytesIO(bundle["payload"])
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("../escaped", b"unexpected")
    data = archive.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    bundle.update(payload=data, size=len(data), digest="sha256:" + digest)
    index_asset = server.assets["python-artifacts.json"]
    index = json.loads(index_asset["payload"])
    index["artifacts"][0]["sha256"] = digest
    index_data = json.dumps(index).encode()
    index_asset.update(
        payload=index_data, size=len(index_data),
        digest="sha256:" + hashlib.sha256(index_data).hexdigest(),
    )
    with pytest.raises(ValueError), intake.retain_released_core(**options):
        pytest.fail("unexpected archive member was exposed")
    assert not (tmp_path / "escaped").exists()


def test_release_intake_rejects_duplicate_signed_index_properties(release):
    _producer, _arguments, server, _proofs, _entry, options = release
    asset = server.assets["python-artifacts.json"]
    data = b'{"schema":"other",' + asset["payload"][1:]
    asset.update(payload=data, size=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError), intake.retain_released_core(**options):
        pytest.fail("ambiguous signed index was exposed")


def test_release_intake_refuses_an_expired_deadline_before_network(release):
    _producer, _arguments, server, proofs, _entry, options = release
    options["deadline"] = time.monotonic() - 1
    with pytest.raises(ValueError), intake.retain_released_core(**options):
        pytest.fail("expired intake was exposed")
    assert not server.calls and not proofs


@pytest.mark.parametrize("stage", ["release", "index", "bundle", "attestation"])
def test_release_intake_normalizes_a_timed_out_request(release, monkeypatch, stage):
    _producer, _arguments, server, _proofs, entry, options = release
    original_gh = intake._gh
    original_verify = intake.verify_attestation_subject
    reached = []

    def request(argv, **kwargs):
        target = (
            stage == "release" and any("/releases/tags/" in item for item in argv)
            or stage == "index" and kwargs.get("binary") and any(
                item.endswith("/" + str(server.assets["python-artifacts.json"]["id"])) for item in argv
            )
            or stage == "bundle" and kwargs.get("binary") and any(
                item.endswith("/" + str(server.assets[entry["filename"]]["id"])) for item in argv
            )
            or stage == "attestation" and argv[0] == "attestation"
        )
        if target:
            reached.append(stage)
            raise subprocess.TimeoutExpired(["gh", "sensitive-request-details"], 1)
        return original_gh(argv, **kwargs)

    def verify(path, **identity):
        if stage == "attestation":
            identity["runner"](["attestation", "fixture"], verification=True)
        return original_verify(path, **identity)

    monkeypatch.setattr(intake, "_gh", request)
    monkeypatch.setattr(intake, "verify_attestation_subject", verify)
    with pytest.raises(artifacts.PromotionArtifactError, match="timed out") as failure, \
            intake.retain_released_core(**options):
        pytest.fail("timed-out release inputs were exposed")
    assert reached == [stage] and "sensitive-request-details" not in str(failure.value)


def test_release_intake_refuses_an_oversized_core_before_downloading_it(release):
    _producer, _arguments, server, proofs, entry, options = release
    options["max_bytes"] = 1
    with pytest.raises(artifacts.PromotionArtifactError), intake.retain_released_core(**options):
        pytest.fail("oversized core was exposed")
    bundle_id = str(server.assets[entry["filename"]]["id"])
    assert proofs == ["python-artifacts.json"]
    assert not any(
        kwargs.get("binary") and any(value.endswith("/" + bundle_id) for value in argv)
        for argv, kwargs in server.calls
    )


@pytest.mark.parametrize("kind", ["index", "bundle", "manifest"])
def test_release_intake_holds_authenticated_inputs_until_exit(release, kind):
    _producer, _arguments, _server, _proofs, _entry, options = release
    with pytest.raises((ValueError, OSError)), intake.retain_released_core(**options) as core:
        path = {"index": core.index_path, "bundle": core.bundle_path, "manifest": core.manifest_path}[kind]
        path.write_bytes(b"changed")


def test_release_intake_imports_without_ambient_python_paths(tmp_path):
    script = "import sys; sys.path.insert(0, sys.argv[1]); import core_release_inputs"
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT / "tools")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
