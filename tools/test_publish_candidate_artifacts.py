"""Final publication/readback is a premerge qualification, never a postmerge repair."""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

import pytest

import promotion_artifact_readiness as readiness
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
import qualify_promotion_candidate as qualification
import test_promotion_python_artifacts as artifact_fixtures
from test_promotion_python_artifacts import DEV, MAIN, TREE, _index, _publish_fixture, _write_index
from verify_promotion_workflows import VerificationError


@pytest.mark.parametrize("failure", [None, "signer", "timeout"])
def test_bundle_attestations_are_bounded_joined_and_keep_every_identity(
    tmp_path, monkeypatch, failure,
) -> None:
    index = {"schema_version": 1, "artifacts": []}
    for identifier in range(9):
        filename = f"bundle-{identifier}.zip"
        (tmp_path / filename).write_bytes(b"synthetic subject")
        index["artifacts"].append({"filename": filename})
    (tmp_path / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    lock, first_wave = threading.Lock(), threading.Event()
    active = peak = 0
    observed = []
    validated = False

    def validate(*args, **kwargs):
        nonlocal validated
        assert observed == [artifacts.INDEX_NAME]
        validated = True
        return index

    def verify(argv, **kwargs):
        nonlocal active, peak
        subject = Path(argv[2]).name
        assert argv == [
            "attestation", "verify", str(tmp_path / subject), "--repo", "example/plugins",
            "--signer-workflow", "example/plugins/.github/workflows/validate-and-promote.yml",
            "--signer-digest", MAIN, "--source-digest", MAIN,
            "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
        ]
        assert kwargs == {"verification": True}
        if subject == artifacts.INDEX_NAME:
            assert not observed and not validated
            observed.append(subject)
            return subprocess.CompletedProcess(argv, 0, "verified", "")
        assert validated
        with lock:
            active += 1
            peak = max(peak, active)
            observed.append(subject)
            if active == 4:
                first_wave.set()
        try:
            assert first_wave.wait(5), "bundle attestations were serialized"
            if subject == "bundle-0.zip" and failure:
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(argv, 120)
                return subprocess.CompletedProcess(argv, 1, "", "invalid signer")
            return subprocess.CompletedProcess(argv, 0, "verified", "")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(publisher, "validate_artifacts", validate)
    monkeypatch.setattr(publisher, "_gh", verify)
    kwargs = dict(repository="example/plugins", signer_digest=MAIN, source_digest=MAIN,
                  source_ref="refs/heads/main")
    if failure:
        error = subprocess.TimeoutExpired if failure == "timeout" else artifacts.PromotionArtifactError
        with pytest.raises(error):
            publisher.verify_attestations(tmp_path, **kwargs)
    else:
        publisher.verify_attestations(tmp_path, **kwargs)
        assert sorted(observed[1:]) == sorted(entry["filename"] for entry in index["artifacts"])
    assert peak == 4 and active == 0
    assert len(observed) == len(set(observed))


@pytest.mark.parametrize("failure", [None, "ancestry", "signer"])
def test_original_admission_is_verified_before_any_bundle_worker(
    prepared, monkeypatch, failure,
) -> None:
    directory, service, _, _, _ = prepared
    index = json.loads((directory / artifacts.INDEX_NAME).read_text(encoding="utf-8"))
    original = "d" * 40
    index["admission"]["signer_digest"] = original
    index["admission"]["source_digest"] = original
    _write_index(directory, index)
    observed = []

    def ancestor(before, after, **kwargs):
        assert (before, after) == (original, MAIN)
        assert observed == [(artifacts.INDEX_NAME, MAIN)]
        return failure != "ancestry"

    def verify(argv, **kwargs):
        assert argv[:2] == ["attestation", "verify"]
        assert kwargs == {"verification": True}
        subject = Path(argv[2]).name
        signer = argv[argv.index("--signer-digest") + 1]
        observed.append((subject, signer))
        if signer == original and failure == "signer":
            return subprocess.CompletedProcess(argv, 1, "", "invalid original signer")
        return subprocess.CompletedProcess(argv, 0, "verified", "")

    import promote_release as promotion

    monkeypatch.setattr(promotion, "_is_ancestor", ancestor)
    monkeypatch.setattr(publisher, "_gh", verify)
    before = list(service.writes)
    kwargs = dict(repository="example/plugins", signer_digest=MAIN, source_digest=MAIN,
                  source_ref="refs/heads/main", repo=directory.parent)
    if failure:
        with pytest.raises(artifacts.PromotionArtifactError):
            publisher.verify_attestations(directory, **kwargs)
        assert all(subject == artifacts.INDEX_NAME for subject, _ in observed)
    else:
        publisher.verify_attestations(directory, **kwargs)
        assert observed == [
            (artifacts.INDEX_NAME, MAIN), (artifacts.INDEX_NAME, original),
            (index["artifacts"][0]["filename"], MAIN),
        ]
    assert service.writes == before


def test_raw_readback_is_bounded_parallel_and_checks_every_exact_asset(monkeypatch) -> None:
    payloads = {identifier: bytes([identifier]) for identifier in range(1, 10)}
    expected = [
        (f"release-{identifier}/payload.zip", identifier, hashlib.sha256(payload).hexdigest())
        for identifier, payload in payloads.items()
    ]
    lock, first_wave = threading.Lock(), threading.Event()
    active = peak = 0
    observed = []

    def download(argv, **kwargs):
        nonlocal active, peak
        identifier = int(argv[1].rsplit("/", 1)[1])
        assert argv == [
            "api", f"repos/example/plugins/releases/assets/{identifier}",
            "-H", "Accept: application/octet-stream",
        ]
        assert kwargs == {"verification": True, "binary": True, "timeout": 120}
        with lock:
            active += 1
            peak = max(peak, active)
            observed.append(identifier)
            if active == 4:
                first_wave.set()
        try:
            assert first_wave.wait(5), "raw reads were serialized"
            return subprocess.CompletedProcess(argv, 0, payloads[identifier], b"")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(publisher, "_gh", download)
    publisher.verify_asset_downloads("example/plugins", expected)
    assert peak == 4 and active == 0
    assert sorted(observed) == sorted(payloads)


@pytest.mark.parametrize("failure", ["http", "digest", "text", "timeout"])
def test_raw_readback_propagates_worker_failure_and_joins_all_workers(
    monkeypatch, failure,
) -> None:
    active = 0
    observed = []
    lock, first_wave = threading.Lock(), threading.Event()
    payload = b"expected"
    digest = hashlib.sha256(payload).hexdigest()

    def download(argv, **kwargs):
        nonlocal active
        identifier = int(argv[1].rsplit("/", 1)[1])
        with lock:
            active += 1
            observed.append(identifier)
            if active == 4:
                first_wave.set()
        try:
            assert first_wave.wait(5)
            if identifier == 1:
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(argv, 120)
                if failure == "http":
                    return subprocess.CompletedProcess(argv, 1, b"", b"HTTP 403: Forbidden")
                return subprocess.CompletedProcess(
                    argv, 0, "expected" if failure == "text" else b"corrupt", b"",
                )
            return subprocess.CompletedProcess(argv, 0, payload, b"")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(publisher, "_gh", download)
    error = subprocess.TimeoutExpired if failure == "timeout" else artifacts.PromotionArtifactError
    with pytest.raises(error):
        publisher.verify_asset_downloads(
            "example/plugins", [(f"subject-{i}", i, digest) for i in range(1, 10)],
        )
    assert active == 0
    assert len(observed) == len(set(observed))


@pytest.mark.parametrize("invalid_last_metadata", [False, True])
def test_final_raw_reads_start_only_after_every_release_identity_is_validated(
    tmp_path, monkeypatch, invalid_last_metadata,
) -> None:
    entries = [
        {"plugin": name, "version": "1.0", "filename": f"{name}.zip", "sha256": str(i) * 64}
        for i, name in enumerate(("first", "second"), 1)
    ]
    index = {"schema_version": 1, "artifacts": entries}
    monkeypatch.setattr(publisher, "validate_commit_artifacts", lambda *a, **kw: index)
    monkeypatch.setattr(publisher, "_tag_commit", lambda *a: TREE)
    bindings = []
    monkeypatch.setattr(publisher, "_validate_tag_binding", lambda *a, **kw: bindings.append(a[1]))
    downloads = []
    monkeypatch.setattr(
        publisher, "verify_asset_downloads", lambda *args: downloads.append((args, list(bindings))),
    )

    def metadata(argv, **kwargs):
        assert kwargs.get("verification") and not kwargs.get("binary")
        if "/releases/tags/" in argv[1]:
            tag = argv[1].rsplit("/", 1)[1]
            identifier = 1 if tag.startswith("first-") else 2
            payload = {"draft": False, "tag_name": tag, "id": identifier, "assets": []}
        else:
            identifier = int(argv[-1].split("/releases/", 1)[1].split("/", 1)[0])
            entry = entries[identifier - 1]
            payload = [[{
                "name": entry["filename"], "id": identifier, "state": "uploaded",
                "digest": "sha256:" + (
                    "0" * 64 if invalid_last_metadata and identifier == 2 else entry["sha256"]
                ),
            }]]
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(publisher, "_gh", metadata)
    kwargs = dict(
        repository="example/plugins", candidate_commit=TREE, dev_head=DEV, repo=tmp_path,
    )
    if invalid_last_metadata:
        with pytest.raises(artifacts.PromotionArtifactError):
            publisher.verify_published_artifacts(tmp_path, **kwargs)
        assert not downloads
    else:
        assert publisher.verify_published_artifacts(tmp_path, **kwargs) is index
        assert downloads == [(("example/plugins", [
            ("first-v1.0/first.zip", 1, "1" * 64),
            ("second-v1.0/second.zip", 2, "2" * 64),
        ]), entries)]


class PublicationService:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.releases: dict[str, dict] = {}
        self.tags: dict[str, str] = {}
        self.assets: dict[int, dict] = {}
        self.failure: str | None = None

    def gh(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append((argv, kwargs))
        binary = kwargs.get("binary", False)

        def result(payload: object = "", code: int = 0, error: str = "") -> subprocess.CompletedProcess:
            if not binary and not isinstance(payload, str):
                payload = json.dumps(payload)
            return subprocess.CompletedProcess(argv, code, payload, error)

        if argv[:2] == ["attestation", "verify"]:
            refused = self.failure == "attestation" or (
                self.failure == "bundle-attestation" and Path(argv[2]).name != artifacts.INDEX_NAME
            )
            return result(code=1, error="invalid signer") if refused else result()
        if argv[:2] == ["release", "create"]:
            tag = argv[2]
            assert tag not in self.releases and "--clobber" not in argv
            self.tags[tag] = argv[argv.index("--target") + 1]
            self.releases[tag] = {
                "id": len(self.releases) + 1, "tag_name": tag, "draft": False,
                "prerelease": "--prerelease" in argv, "assets": [],
            }
            return result()
        if argv[:2] == ["release", "upload"]:
            tag = argv[2]
            assert "--clobber" not in argv
            for filename in argv[argv.index("--repo") + 2:]:
                path = Path(filename)
                payload = path.read_bytes()
                asset = {
                    "id": len(self.assets) + 1, "name": path.name, "state": "uploaded",
                    "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                }
                assert not any(existing["name"] == path.name for existing in self.releases[tag]["assets"])
                self.releases[tag]["assets"].append(asset)
                self.assets[asset["id"]] = {**asset, "payload": payload, "tag": tag}
            return result()
        assert argv[0] == "api", argv
        endpoint = next(item for item in argv if item.startswith("repos/"))
        if "/git/ref/tags/" in endpoint:
            tag = endpoint.split("/git/ref/tags/", 1)[1]
            return (
                result({"object": {"type": "commit", "sha": self.tags[tag]}})
                if tag in self.tags else result(code=1, error="HTTP 404: Not Found")
            )
        if "/releases/tags/" in endpoint:
            tag = endpoint.split("/releases/tags/", 1)[1]
            return (
                result(self.releases[tag]) if tag in self.releases
                else result(code=1, error="HTTP 404: Not Found")
            )
        if "/releases/assets/" in endpoint:
            assert binary and kwargs.get("verification"), "availability must use raw consumer reads"
            asset = self.assets[int(endpoint.rsplit("/", 1)[1])]
            if self.failure in ("read", "digest"):
                return (
                    result(code=1, error="HTTP 403: Forbidden") if self.failure == "read"
                    else result(b"tampered after publication")
                )
            return result(asset["payload"])
        if endpoint.endswith("/assets?per_page=100"):
            identifier = int(endpoint.split("/releases/", 1)[1].split("/", 1)[0])
            release = next(value for value in self.releases.values() if value["id"] == identifier)
            return result([release["assets"]])
        raise AssertionError(argv)

    @property
    def writes(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls if argv[0] == "release"]


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    monkeypatch.setenv("GH_TOKEN", "synthetic-publication-authority")
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "synthetic-consumer-read-authority")
    directory, _ = _publish_fixture(tmp_path, monkeypatch)
    index = json.loads((directory / artifacts.INDEX_NAME).read_text("utf-8"))
    index["schema_version"] = 2
    del index["main_before"]
    index["admission"] = {
        "signer_digest": MAIN, "source_digest": MAIN, "source_ref": "refs/heads/main",
    }
    _write_index(directory, index)
    service = PublicationService()
    monkeypatch.setattr(publisher, "_gh", service.gh)
    readiness.prepare_artifacts(
        directory, repository="example/plugins", candidate_commit=TREE, dev_head=DEV, repo=tmp_path,
    )
    receipt, output = tmp_path / "qualification.json", tmp_path / "output"
    receipt.write_text("{}", encoding="utf-8")
    observed: list[dict] = []

    def verify(receipt: dict, **kwargs: object) -> str:
        observed.append(kwargs)
        assert kwargs["candidate_sha"] == TREE and kwargs["dev_sha"] == DEV
        assert kwargs["main_before"] == MAIN and kwargs["control_sha"] == MAIN
        assert kwargs["promotion_run"] == "123-1"
        return "f" * 64

    monkeypatch.setattr(qualification, "verify_receipt", verify)
    argv = [
        "publish-candidate", "--repo", str(tmp_path), "--directory", str(directory),
        "--repository", "example/plugins", "--candidate-commit", TREE, "--dev-head", DEV,
        "--main-before", MAIN, "--signer-digest", MAIN, "--source-digest", MAIN,
        "--source-ref", "refs/heads/main", "--promotion-run", "123-1",
        "--candidate-root", str(tmp_path), "--qualification-receipt", str(receipt),
        "--github-output", str(output),
    ]
    return directory, service, observed, argv, output


def test_real_prepublish_cli_checks_qualification_signatures_and_final_downloads_before_receipt(
    prepared: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, service, observed, argv, output = prepared
    validate = publisher.validate_commit_artifacts
    validations = []

    def record_validation(*args, **kwargs):
        validations.append((args, kwargs))
        return validate(*args, **kwargs)

    monkeypatch.setattr(publisher, "validate_commit_artifacts", record_validation)
    before = len(service.calls)
    assert publisher.main(argv) == 0
    assert len(validations) == 1
    assert observed
    fields = dict(line.split("=", 1) for line in output.read_text("utf-8").splitlines())
    assert fields["source_release"] == f"python-artifacts-{DEV}"
    assert len(fields["publication_digest"]) == 64
    source = service.releases[f"python-artifacts-{DEV}"]
    assert service.tags == {f"python-artifacts-{DEV}": DEV}
    assert len(source["assets"]) == 2
    assert sorted(
        int(call[1].rsplit("/", 1)[-1])
        for call, options in service.calls[before:]
        if call[0] == "api" and "/releases/assets/" in call[1]
        and options.get("binary") and options.get("verification")
    ) == sorted(asset["id"] for asset in source["assets"])
    writes = list(service.writes)
    assert publisher.main(argv) == 0
    assert len(validations) == 2
    assert service.writes == writes
    assert output.read_text("utf-8").count(f"source_release=python-artifacts-{DEV}") == 2
    assert (directory / artifacts.INDEX_NAME).is_file()


def test_source_publication_does_not_read_or_mutate_another_sources_version_alias(
    prepared: tuple,
) -> None:
    _, service, _, argv, output = prepared
    alias = "demo-v1.0"
    old_source = "e" * 40
    old_release = {"id": 99, "tag_name": alias, "draft": False, "assets": []}
    service.tags[alias] = old_source
    service.releases[alias] = old_release
    before = len(service.calls)

    assert publisher.main(argv) == 0

    assert service.tags[alias] == old_source
    assert service.releases[alias] is old_release
    assert not any(alias in argument for call, _ in service.calls[before:] for argument in call)
    assert f"source_release=python-artifacts-{DEV}" in output.read_text("utf-8")


def test_source_publication_still_refuses_changed_candidate_sources(
    prepared: tuple,
) -> None:
    directory, service, _, argv, output = prepared
    source = directory.parent / "plugins" / "demo" / "src" / "demo" / "__init__.py"
    source.write_bytes(b"VALUE = 'changed after artifact construction'\n")
    before = list(service.writes)

    assert publisher.main(argv) == 1

    assert service.writes == before
    assert not output.exists()


def test_source_publication_never_repairs_a_missing_prepared_set(prepared: tuple) -> None:
    directory, service, _, _, _ = prepared
    del service.releases[f"python-artifacts-{DEV}"]
    before = list(service.writes)

    with pytest.raises(artifacts.PromotionArtifactError, match="unavailable or misbound"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=TREE,
            dev_head=DEV, repo=directory.parent,
        )

    assert service.writes == before


def test_distinct_dev_sources_can_publish_different_bytes_with_the_same_plugin_version(
    prepared: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, service, _, argv, _ = prepared
    assert publisher.main(argv) == 0
    original = service.releases[f"python-artifacts-{DEV}"]
    original_assets = list(original["assets"])
    other_root = tmp_path / "other-source"
    _publish_fixture(other_root, monkeypatch)
    source = other_root / "plugins" / "demo" / "src" / "demo" / "__init__.py"
    source.write_bytes(b"VALUE = 'another development source'\n")
    wheel_bytes = artifact_fixtures._wheel_bytes
    monkeypatch.setattr(
        artifact_fixtures, "_wheel_bytes",
        lambda name, version: wheel_bytes(name, version, payload=source.read_bytes()),
    )
    other_directory = other_root / "qualified-artifacts"
    _index(other_directory, candidate_root=other_root)
    index = json.loads((other_directory / artifacts.INDEX_NAME).read_text("utf-8"))
    other_dev = "e" * 40
    index["schema_version"] = 2
    index["dev_head"] = other_dev
    del index["main_before"]
    _write_index(other_directory, index)
    original_index = json.loads((directory / artifacts.INDEX_NAME).read_text("utf-8"))
    assert index["artifacts"][0]["version"] == original_index["artifacts"][0]["version"]
    assert index["artifacts"][0]["sha256"] != original_index["artifacts"][0]["sha256"]
    arguments = dict(repository="example/plugins", dev_head=other_dev, repo=other_root)

    readiness.prepare_artifacts(other_directory, candidate_commit=TREE, **arguments)
    publisher.publish_artifacts(other_directory, merged_commit=TREE, **arguments)
    assert publisher.verify_published_artifacts(
        other_directory, candidate_commit=TREE, **arguments,
    )["dev_head"] == other_dev

    assert service.tags == {f"python-artifacts-{DEV}": DEV, f"python-artifacts-{other_dev}": other_dev}
    assert original["assets"] == original_assets


@pytest.mark.parametrize("failure", ["read", "digest", "attestation", "bundle-attestation"])
def test_no_success_receipt_when_required_final_publication_evidence_fails(
    prepared: tuple, failure: str,
) -> None:
    _, service, _, argv, output = prepared
    service.failure = failure
    assert publisher.main(argv) == 1
    assert not output.exists()
    assert not any("merge" in call for call in service.writes)
    if failure in ("attestation", "bundle-attestation"):
        assert "demo-v1.0" not in service.releases


def test_missing_or_stale_qualification_prevents_all_new_release_writes(
    prepared: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, _, argv, output = prepared
    before = list(service.writes)

    def refuse(*args: object, **kwargs: object) -> str:
        raise VerificationError("qualification is stale")

    monkeypatch.setattr(qualification, "verify_receipt", refuse)
    assert publisher.main(argv) == 1
    assert service.writes == before
    assert not output.exists()


def test_offline_v2_export_without_admission_cannot_create_an_unrecoverable_public_source_set(
    prepared: tuple,
) -> None:
    directory, service, _, argv, output = prepared
    index = json.loads((directory / artifacts.INDEX_NAME).read_text("utf-8"))
    del index["admission"]
    _write_index(directory, index)
    before = list(service.writes)
    assert publisher.main(argv) == 1
    assert service.writes == before
    assert not output.exists()


def test_writer_only_view_cannot_authorize_candidate_publication(
    prepared: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, observed, argv, output = prepared
    monkeypatch.delenv("ARTIFACT_VERIFY_TOKEN")
    before = list(service.writes)
    assert publisher.main(argv) == 1
    assert service.writes == before and not observed
    assert not output.exists()
