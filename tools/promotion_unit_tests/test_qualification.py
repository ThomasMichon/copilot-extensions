"""In-process unit qualification and immutable producer/artifact bindings."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import unquote
import zipfile

import pytest

import candidate_qualification_receipt as receipt
import qualify_promotion_candidate as qualification
import publish_python_artifacts as publisher
import verify_promotion_workflows as workflows
from promotion_unit_contract import PROFILE, UnitEvidenceError, manifest_digest
from verify_promotion_workflows import VerificationError

DEV, MAIN, CANDIDATE, CONTROL, TREE = (letter * 40 for letter in "abcde")
REPOSITORY = "example/project"
MANIFEST = ".github/promotion-units.json"
NODE = "tools/promotion_unit_tests/test_example.py::test_example"


@pytest.fixture
def inline_readback(monkeypatch):
    lifecycle = []

    class Workers:
        def __init__(self, *, max_workers):
            assert max_workers == 4

        def __enter__(self):
            lifecycle.append("entered")
            return self

        def __exit__(self, *args):
            lifecycle.append("exited")

        def map(self, operation, assets):
            return map(operation, assets)

    monkeypatch.setattr(publisher, "ThreadPoolExecutor", Workers)
    return lifecycle


def test_raw_readback_requires_consumer_authority_and_every_exact_subject(
    monkeypatch, inline_readback,
):
    observed = []
    payloads = {1: b"first", 2: b"second", 3: b"third"}

    def read(argv, **kwargs):
        identifier = int(argv[1].rsplit("/", 1)[1])
        observed.append(identifier)
        assert argv == [
            "api", f"repos/{REPOSITORY}/releases/assets/{identifier}",
            "-H", "Accept: application/octet-stream",
        ]
        assert kwargs == {"verification": True, "binary": True, "timeout": 120}
        return publisher.subprocess.CompletedProcess(argv, 0, payloads[identifier], b"")

    monkeypatch.setattr(publisher, "_gh", read)
    publisher.verify_asset_downloads(REPOSITORY, [
        (f"subject-{identifier}", identifier, hashlib.sha256(raw).hexdigest())
        for identifier, raw in payloads.items()
    ])
    assert observed == [1, 2, 3]
    assert inline_readback == ["entered", "exited"]


def test_raw_readback_cannot_ignore_a_later_corrupt_subject(monkeypatch, inline_readback):
    observed = []

    def read(argv, **kwargs):
        identifier = int(argv[1].rsplit("/", 1)[1])
        observed.append(identifier)
        return publisher.subprocess.CompletedProcess(
            argv, 0, b"expected" if identifier == 1 else b"corrupt", b"",
        )

    monkeypatch.setattr(publisher, "_gh", read)
    digest = hashlib.sha256(b"expected").hexdigest()
    with pytest.raises(publisher.PromotionArtifactError, match="second"):
        publisher.verify_asset_downloads(REPOSITORY, [("first", 1, digest), ("second", 2, digest)])
    assert observed == [1, 2]
    assert inline_readback == ["entered", "exited"]


def test_raw_readback_provider_refusal_never_becomes_success(monkeypatch, inline_readback):
    monkeypatch.setattr(
        publisher, "_gh",
        lambda argv, **kwargs: publisher.subprocess.CompletedProcess(
            argv, 1, b"", b"HTTP 403: Forbidden",
        ),
    )
    with pytest.raises(publisher.PromotionArtifactError, match="gh failed"):
        publisher.verify_asset_downloads(REPOSITORY, [("subject", 1, "f" * 64)])
    assert inline_readback == ["entered", "exited"]


@pytest.mark.parametrize("operation", ["publish", "verify"])
def test_source_owned_publication_uses_the_dev_set_not_plugin_version_aliases(
    tmp_path, monkeypatch, operation,
):
    import promotion_artifact_readiness as readiness

    index = {"schema_version": 2, "dev_head": DEV, "artifacts": []}
    validated = []
    observed = []

    def validate(directory, **kwargs):
        validated.append((directory, kwargs))
        return index

    def source_set(directory, **kwargs):
        assert validated
        observed.append((directory, kwargs))
        return f"python-artifacts-{DEV}"

    def refuse_alias(*args, **kwargs):
        pytest.fail("source publication must neither consult version aliases nor repair the ready set")

    monkeypatch.setattr(publisher, "validate_commit_artifacts", validate)
    monkeypatch.setattr(publisher, "_tag_commit", refuse_alias)
    monkeypatch.setattr(publisher, "_gh", refuse_alias)
    monkeypatch.setattr(readiness, "prepare_artifacts", refuse_alias)
    monkeypatch.setattr(readiness, "verify_ready_artifacts", source_set)
    arguments = dict(repository=REPOSITORY, dev_head=DEV, repo=tmp_path)
    if operation == "publish":
        publisher.publish_artifacts(tmp_path, merged_commit=CANDIDATE, **arguments)
    else:
        assert publisher.verify_published_artifacts(
            tmp_path, candidate_commit=CANDIDATE, **arguments,
        ) is index
    assert observed == [(tmp_path, {**arguments, "candidate_commit": CANDIDATE})]
    assert validated == [(tmp_path, dict(commit=CANDIDATE, dev_head=DEV, repo=tmp_path))]


@pytest.mark.parametrize("operation", ["publish", "verify"])
def test_source_owned_publication_propagates_source_set_verification_failure(
    tmp_path, monkeypatch, operation,
):
    import promotion_artifact_readiness as readiness

    monkeypatch.setattr(
        publisher, "validate_commit_artifacts",
        lambda *args, **kwargs: {"schema_version": 2},
    )

    def refuse(*args, **kwargs):
        raise publisher.PromotionArtifactError("source set has a corrupt asset")

    monkeypatch.setattr(readiness, "prepare_artifacts", refuse)
    monkeypatch.setattr(readiness, "verify_ready_artifacts", refuse)
    arguments = dict(repository=REPOSITORY, dev_head=DEV, repo=tmp_path)
    with pytest.raises(publisher.PromotionArtifactError, match="corrupt asset"):
        if operation == "publish":
            publisher.publish_artifacts(tmp_path, merged_commit=CANDIDATE, **arguments)
        else:
            publisher.verify_published_artifacts(
                tmp_path, candidate_commit=CANDIDATE, **arguments,
            )


@pytest.mark.parametrize("failed_readback", [False, True])
def test_source_owned_candidate_cli_uses_one_verified_set_for_its_receipt(
    tmp_path, monkeypatch, failed_readback,
):
    import promotion_artifact_readiness as readiness

    directory = tmp_path / "artifacts"
    directory.mkdir()
    index = {"schema_version": 2, "artifacts": [{"filename": "verified.zip"}]}
    raw_index = json.dumps(index).encode()
    (directory / publisher.INDEX_NAME).write_bytes(raw_index)
    receipt_path, output = tmp_path / "qualification.json", tmp_path / "output"
    receipt_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "fixture-consumer-read")
    monkeypatch.setattr(qualification, "verify_receipt", lambda *a, **k: "f" * 64)
    observed = []

    def attest(*args, **kwargs):
        observed.append("attest")
        return {"schema_version": 2}

    def readback(*args, **kwargs):
        assert observed == ["attest"]
        assert args == (directory,)
        assert kwargs == dict(
            repository=REPOSITORY, candidate_commit=CANDIDATE, dev_head=DEV, repo=tmp_path,
        )
        observed.append("readback")
        if failed_readback:
            raise publisher.PromotionArtifactError("source readback failed")
        return f"python-artifacts-{DEV}", index

    def redundant(*args, **kwargs):
        pytest.fail("schema-2 CLI repeated validation, readback or legacy publication")

    monkeypatch.setattr(publisher, "verify_attestations", attest)
    monkeypatch.setattr(readiness, "verify_ready_artifact_set", readback)
    monkeypatch.setattr(readiness, "verify_ready_artifacts", redundant)
    monkeypatch.setattr(publisher, "publish_artifacts", redundant)
    monkeypatch.setattr(publisher, "verify_published_artifacts", redundant)
    arguments = _publish_arguments(
        {"args": {"candidate_root": tmp_path}}, directory, receipt_path,
    )
    assert publisher.main([
        *arguments, "--repo", str(tmp_path), "--github-output", str(output),
    ]) == (1 if failed_readback else 0)
    assert observed == ["attest", "readback"]
    if failed_readback:
        assert not output.exists()
    else:
        publication = {
            "dev_head": DEV, "candidate": CANDIDATE, "qualification": "f" * 64,
            "index_digest": hashlib.sha256(raw_index).hexdigest(), "artifacts": index["artifacts"],
        }
        digest = hashlib.sha256(json.dumps(publication, sort_keys=True).encode()).hexdigest()
        assert output.read_text("utf-8").splitlines() == [
            f"source_release=python-artifacts-{DEV}", f"publication_digest={digest}",
        ]


def _producer():
    repository = {"id": 77, "full_name": REPOSITORY}
    return {
        "workflow": {"id": 12, "path": receipt.WORKFLOW, "state": "active"},
        # A later job-only retry may run while original successful unit proof
        # remains bound to attempt 1; the entire VP need not be complete.
        "run": {
            "id": 123, "run_attempt": 2, "workflow_id": 12, "status": "in_progress",
            "path": receipt.WORKFLOW, "event": "workflow_dispatch",
            "head_branch": "main", "head_sha": CONTROL,
            "repository": repository, "head_repository": repository.copy(),
        },
        "jobs": [{
            "id": 456, "run_id": 123, "run_attempt": 1, "head_sha": CONTROL,
            "name": receipt.UNIT_JOB, "status": "completed", "conclusion": "success",
            "started_at": "2026-10-19T20:00:00Z", "completed_at": "2026-10-19T20:00:20Z",
            "steps": [{
                "name": receipt.UNIT_UPLOAD_STEP, "number": 4,
                "status": "completed", "conclusion": "success",
                "started_at": "2026-10-19T20:00:10Z", "completed_at": "2026-10-19T20:00:15Z",
            }],
        }],
        "artifact": {
            "id": 789, "name": receipt.UNIT_ARTIFACT, "expired": False,
            "size_in_bytes": 123, "digest": "sha256:" + "f" * 64,
            "created_at": "2026-10-19T20:00:12Z",
            "workflow_run": {
                "id": 123, "head_branch": "main", "head_sha": CONTROL,
                "repository_id": 77, "head_repository_id": 77,
            },
        },
    }


def _bind(producer):
    return receipt.producer_binding(
        repository=REPOSITORY, run_id=123, attempt=1, control_sha=CONTROL,
        artifact_id=789, artifact_digest="sha256:" + "f" * 64, **producer,
    )


def _source_inputs(tmp_path):
    source, candidate = tmp_path / "source", tmp_path / "candidate"
    content = {
        NODE.split("::")[0]: b"def test_example(): assert True\n",
        "tools/unit_feature.py": b"def value(): return 1\n",
    }
    manifest = {
        "profile": PROFILE, "nodes": [NODE],
        "sources": {name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()},
        "coverage_targets": ["tools/unit_feature.py"], "max_seconds": 300,
    }
    for root in (source, candidate):
        for name, raw in {**content, MANIFEST: json.dumps(manifest).encode()}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    return source, candidate, manifest


def _write_json(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return raw


@pytest.fixture
def unit_case(tmp_path, monkeypatch):
    source, candidate, manifest = _source_inputs(tmp_path)
    directory = tmp_path / "packet"
    directory.mkdir()
    coverage = {
        "meta": {}, "totals": {},
        "files": {"tools/unit_feature.py": {
            "executed_lines": [1], "missing_lines": [], "excluded_lines": [],
            "summary": {}, "functions": {}, "classes": {},
            "contexts": {"1": ["", NODE + "|call"]},
        }},
    }
    coverage_raw = _write_json(directory / "coverage.json", coverage)
    proof = {
        "profile": PROFILE, "source_sha": DEV, "manifest_sha256": manifest_digest(manifest),
        "elapsed_seconds": 0.125, "collected": [NODE],
        "reports": [{"node": NODE, "phase": phase, "outcome": "passed"}
                    for phase in ("setup", "call", "teardown")],
        "effects": [], "imported_sources": sorted(manifest["sources"]),
        "coverage_file": "coverage.json", "coverage_sha256": hashlib.sha256(coverage_raw).hexdigest(),
    }
    _write_json(directory / "unit-manifest.json", manifest)
    _write_json(directory / "unit-proof.json", proof)
    state = {
        "producer": _producer(), "refs": {"main": MAIN, "release/promote-123": CANDIDATE},
        "calls": [], "downloads": [], "validations": [], "source_head": DEV,
        "args": {
            "repository": REPOSITORY, "candidate_ref": "release/promote-123",
            "candidate_sha": CANDIDATE, "dev_sha": DEV, "main_before": MAIN,
            "control_sha": CONTROL, "promotion_run": "123-1",
            "candidate_root": candidate, "dev_root": source, "unit_packet_dir": directory,
            "unit_manifest": MANIFEST, "unit_artifact_id": 789,
        },
        "manifest": manifest, "proof": proof, "coverage": coverage,
    }

    def reseal():
        raw = _archive({name: (directory / name).read_bytes() for name in receipt.PACKET_FILES})
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        state["archive"] = raw
        state["producer"]["artifact"].update(digest=digest, size_in_bytes=len(raw))
        state["args"]["unit_artifact_digest"] = digest

    state["reseal"] = reseal
    reseal()

    def api(path, *, body=None, timeout=60):
        assert body is None and 0 < timeout <= 60
        assert "/dispatches" not in path and "ci.yml" not in path
        state["calls"].append(path)
        if "/git/ref/heads/" in path:
            branch = unquote(path.split("/git/ref/heads/", 1)[1])
            return {"object": {"type": "commit", "sha": state["refs"][branch]}}
        if path == f"repos/{REPOSITORY}/actions/workflows/validate-and-promote.yml":
            return copy.deepcopy(state["producer"]["workflow"])
        if path == f"repos/{REPOSITORY}/actions/runs/123":
            return copy.deepcopy(state["producer"]["run"])
        if path.startswith(f"repos/{REPOSITORY}/actions/runs/123/attempts/1/jobs?"):
            return {"jobs": copy.deepcopy(state["producer"]["jobs"])}
        if path == f"repos/{REPOSITORY}/actions/artifacts/789":
            return copy.deepcopy(state["producer"]["artifact"])
        raise AssertionError(f"unexpected provider read: {path}")

    def git(root, *arguments):
        assert root == source
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(source.resolve())
        if arguments == ("rev-parse", "HEAD"):
            return state["source_head"]
        assert arguments == ("status", "--porcelain", "--untracked-files=no")
        return ""

    def validate(root, candidate_sha, dev_sha, main_before):
        assert (root, candidate_sha, dev_sha, main_before) == (candidate, CANDIDATE, DEV, MAIN)
        state["validations"].append(root)
        return {"candidate_sha": CANDIDATE, "dev_sha": DEV, "main_before": MAIN, "tree": TREE}

    def download(repository, artifact_id, *, timeout):
        assert repository == REPOSITORY and artifact_id == 789 and 0 < timeout <= 60
        state["downloads"].append(artifact_id)
        return state["archive"]

    def forbidden(*args, **kwargs):
        raise AssertionError("unit reuse must never dispatch or inventory general CI")

    monkeypatch.setenv("GH_TOKEN", "synthetic-read-authority")
    monkeypatch.setattr(qualification, "_api", api)
    monkeypatch.setattr(workflows, "_api", api)
    monkeypatch.setattr(qualification, "_source_git", git)
    monkeypatch.setattr(qualification, "validate", validate)
    monkeypatch.setattr(qualification, "_unit_artifact_bytes", download)
    for name in ("required_jobs", "_complete_run", "matrix", "_runs"):
        monkeypatch.setattr(qualification, name, forbidden)
    return state


def _verify_units(case, value, *, expected_profile=qualification.UNIT_PROFILE):
    arguments = {
        key: val for key, val in case["args"].items()
        if key not in ("candidate_ref", "unit_artifact_id", "unit_artifact_digest")
    }
    return qualification.verify_receipt(value, expected_profile=expected_profile, **arguments)


def test_qualification_reuses_the_real_verified_packet_without_another_test_execution(unit_case):
    value = qualification.qualify_units(**unit_case["args"])
    assert value["version"] == 2 and value["profile"] == "source-units-v2"
    assert value["dev_sha"] == DEV and value["control_sha"] == CONTROL
    assert value["run_id"] == 123 and value["run_attempt"] == 1
    assert value["unit_job_id"] == 456 and value["unit_artifact_id"] == 789
    assert value["unit_artifact_digest"] != "sha256:" + value["packet_digest"]
    assert _verify_units(unit_case, value) == qualification.receipt_digest(value)
    assert len(unit_case["validations"]) == 4
    assert unit_case["downloads"] == [789, 789]
    assert sum(path.endswith("/git/ref/heads/main") for path in unit_case["calls"]) == 4
    assert any("/attempts/1/jobs?" in path for path in unit_case["calls"])


def test_v2_cli_writes_canonical_receipt_and_explicit_producer_outputs(unit_case, tmp_path):
    output, path = tmp_path / "outputs", tmp_path / "qualification.json"
    arguments = ["--profile", qualification.UNIT_PROFILE, "--receipt", str(path), "--github-output", str(output)]
    for name, value in unit_case["args"].items():
        arguments.extend(("--" + name.replace("_", "-"), str(value)))
    assert qualification.main(arguments) == 0
    value = json.loads(path.read_bytes())
    assert path.read_bytes() == qualification.receipt_bytes(value)
    fields = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert fields["qualification_profile"] == qualification.UNIT_PROFILE
    assert fields["qualification_run"] == "123" and fields["qualification_attempt"] == "1"
    assert fields["qualification_unit_job_id"] == "456"
    assert fields["qualification_unit_artifact_digest"] == unit_case["args"]["unit_artifact_digest"]
    assert fields["qualification_digest"] == qualification.receipt_digest(value)


@pytest.mark.parametrize("mutation", ["missing-job", "failed-job", "wrong-control", "missing-artifact", "archive-tamper"])
def test_valid_packet_cannot_hide_wrong_provider_or_archive(unit_case, mutation):
    producer = unit_case["producer"]
    if mutation == "missing-job":
        producer["jobs"] = []
    elif mutation == "failed-job":
        producer["jobs"][0]["conclusion"] = "failure"
    elif mutation == "wrong-control":
        producer["run"]["head_sha"] = DEV
    elif mutation == "missing-artifact":
        producer["artifact"] = None
    else:
        unit_case["archive"] += b"changed bytes"
    with pytest.raises(receipt.UnitQualificationError):
        qualification.qualify_units(**unit_case["args"])


@pytest.mark.parametrize("mutation", ["missing-phase", "skip", "wrong-source", "coverage-tamper", "no-production-call"])
def test_real_packet_validation_prevents_provider_green_from_hiding_incomplete_units(unit_case, mutation):
    directory = unit_case["args"]["unit_packet_dir"]
    proof = unit_case["proof"]
    if mutation == "missing-phase":
        proof["reports"].pop()
    elif mutation == "skip":
        proof["reports"][1]["outcome"] = "skipped"
    elif mutation == "wrong-source":
        proof["source_sha"] = MAIN
    elif mutation == "coverage-tamper":
        proof["coverage_sha256"] = "0" * 64
    else:
        unit_case["coverage"]["files"]["tools/unit_feature.py"]["contexts"] = {"1": [""]}
        raw = _write_json(directory / "coverage.json", unit_case["coverage"])
        proof["coverage_sha256"] = hashlib.sha256(raw).hexdigest()
    _write_json(directory / "unit-proof.json", proof)
    unit_case["reseal"]()
    with pytest.raises(UnitEvidenceError):
        qualification.qualify_units(**unit_case["args"])
    assert unit_case["downloads"] == []


@pytest.mark.parametrize("mutation", ["changed-candidate", "moved-main", "moved-candidate", "wrong-dev-checkout"])
def test_candidate_or_source_freshness_changes_refuse_unit_reuse(unit_case, mutation):
    if mutation == "changed-candidate":
        path = unit_case["args"]["candidate_root"] / "tools/unit_feature.py"
        path.write_bytes(path.read_bytes() + b"# changed\n")
    elif mutation == "moved-main":
        unit_case["refs"]["main"] = "f" * 40
    elif mutation == "moved-candidate":
        unit_case["refs"]["release/promote-123"] = "f" * 40
    else:
        unit_case["source_head"] = MAIN
    with pytest.raises((receipt.UnitQualificationError, VerificationError)):
        qualification.qualify_units(**unit_case["args"])


@pytest.mark.parametrize("field", ["candidate_sha", "dev_sha", "main_before", "control_sha", "tree", "packet_digest"])
def test_stale_v2_receipt_fields_cannot_be_relabelled(unit_case, field):
    value = qualification.qualify_units(**unit_case["args"])
    value[field] = "f" * (64 if field == "packet_digest" else 40)
    with pytest.raises(VerificationError, match="absent, invalid or stale"):
        _verify_units(unit_case, value)


def test_v2_receipt_requires_v2_intent_even_for_a_legacy_api_caller(unit_case):
    value = qualification.qualify_units(**unit_case["args"])
    with pytest.raises(VerificationError, match="explicit legacy"):
        _verify_units(unit_case, value, expected_profile=qualification.LEGACY_PROFILE)


def _publish_arguments(case, directory, receipt_path):
    return [
        "publish-candidate", "--directory", str(directory), "--repository", REPOSITORY,
        "--signer-digest", CONTROL, "--source-digest", CONTROL, "--source-ref", "refs/heads/main",
        "--candidate-commit", CANDIDATE, "--dev-head", DEV, "--main-before", MAIN,
        "--promotion-run", "123-1", "--candidate-root", str(case["args"]["candidate_root"]),
        "--qualification-receipt", str(receipt_path),
    ]


def test_v2_publisher_consumes_the_real_packet_verifier_with_explicit_profile(unit_case, tmp_path, monkeypatch):
    import promotion_artifact_readiness as readiness

    value = qualification.qualify_units(**unit_case["args"])
    receipt_path = tmp_path / "qualification.json"
    receipt_path.write_bytes(qualification.receipt_bytes(value))
    directory = tmp_path / "artifacts"
    directory.mkdir()
    (directory / publisher.INDEX_NAME).write_text('{"synthetic":"artifact-operation-boundary"}')
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "synthetic-independent-read-authority")
    monkeypatch.setattr(publisher, "verify_attestations", lambda *a, **k: {"schema_version": 1})
    monkeypatch.setattr(readiness, "verify_ready_artifacts", lambda *a, **k: "source-tag")
    writes = []
    monkeypatch.setattr(publisher, "publish_artifacts", lambda *a, **k: writes.append(k["candidate_commit"]))
    monkeypatch.setattr(publisher, "verify_published_artifacts", lambda *a, **k: {"artifacts": []})
    arguments = _publish_arguments(unit_case, directory, receipt_path)
    assert publisher.main([
        *arguments, "--qualification-profile", qualification.UNIT_PROFILE,
        "--unit-packet-dir", str(unit_case["args"]["unit_packet_dir"]),
        "--unit-manifest", MANIFEST, "--dev-root", str(unit_case["args"]["dev_root"]),
    ]) == 0
    assert writes == [CANDIDATE] and unit_case["downloads"] == [789, 789]
    # The same v2 receipt without trusted v2 intent must not enter legacy CI
    # or reach any additional artifact publication.
    assert publisher.main(arguments) == 1
    assert writes == [CANDIDATE]


def test_v2_publisher_missing_packet_does_not_fall_back_to_legacy(unit_case, tmp_path):
    with pytest.raises(SystemExit) as error:
        publisher.main([
            *_publish_arguments(unit_case, tmp_path, tmp_path / "qualification.json"),
            "--qualification-profile", qualification.UNIT_PROFILE,
        ])
    assert error.value.code == 2


def test_unit_producer_can_succeed_before_vp_finishes_and_survive_job_only_retry():
    binding = _bind(_producer())
    assert binding["run_id"] == 123 and binding["run_attempt"] == 1
    assert binding["unit_job_id"] == 456 and binding["unit_artifact_id"] == 789
    assert binding["unit_artifact_digest"] == "sha256:" + "f" * 64


@pytest.mark.parametrize("mutation", [
    "wrong-workflow", "wrong-control", "wrong-source-as-controller", "wrong-owner",
    "future-attempt", "missing-job", "duplicate-job", "failed-job", "cancelled-job",
    "skipped-job", "wrong-job-attempt", "missing-upload", "failed-upload",
    "wrong-artifact-id", "wrong-artifact-name", "expired-artifact", "wrong-artifact-digest",
    "foreign-artifact-run", "foreign-artifact-control", "wrong-upload-window",
])
def test_wrong_or_incomplete_producer_cannot_authorize_unit_reuse(mutation):
    producer = _producer()
    if mutation == "wrong-workflow":
        producer["run"]["path"] = ".github/workflows/ci.yml"
    elif mutation == "wrong-control":
        producer["run"]["head_sha"] = MAIN
    elif mutation == "wrong-source-as-controller":
        producer["run"]["head_sha"] = DEV
    elif mutation == "wrong-owner":
        producer["run"]["head_repository"]["full_name"] = "outside/project"
    elif mutation == "future-attempt":
        producer["run"]["run_attempt"] = 0
    elif mutation == "missing-job":
        producer["jobs"] = []
    elif mutation == "duplicate-job":
        producer["jobs"].append(copy.deepcopy(producer["jobs"][0]))
    elif mutation in ("failed-job", "cancelled-job", "skipped-job"):
        producer["jobs"][0]["conclusion"] = {
            "failed-job": "failure", "cancelled-job": "cancelled", "skipped-job": "skipped",
        }[mutation]
    elif mutation == "wrong-job-attempt":
        producer["jobs"][0]["run_attempt"] = 2
    elif mutation == "missing-upload":
        producer["jobs"][0]["steps"] = []
    elif mutation == "failed-upload":
        producer["jobs"][0]["steps"][0]["conclusion"] = "failure"
    elif mutation == "wrong-artifact-id":
        producer["artifact"]["id"] = 790
    elif mutation == "wrong-artifact-name":
        producer["artifact"]["name"] = "other-packet"
    elif mutation == "expired-artifact":
        producer["artifact"]["expired"] = True
    elif mutation == "wrong-artifact-digest":
        producer["artifact"]["digest"] = "sha256:" + "0" * 64
    elif mutation == "foreign-artifact-run":
        producer["artifact"]["workflow_run"]["id"] = 124
    elif mutation == "foreign-artifact-control":
        producer["artifact"]["workflow_run"]["head_sha"] = CANDIDATE
    else:
        producer["artifact"]["created_at"] = "2026-10-19T20:00:19Z"
    with pytest.raises(receipt.UnitQualificationError):
        _bind(producer)


@pytest.mark.parametrize("name", [NODE.split("::")[0], "tools/unit_feature.py"])
@pytest.mark.parametrize("mutation", ["missing", "changed", "crlf"])
def test_every_admitted_source_byte_must_survive_candidate_composition(tmp_path, name, mutation):
    source, candidate, manifest = _source_inputs(tmp_path)
    assert receipt.input_equivalence(source, candidate, manifest) == receipt.canonical_digest(manifest["sources"])
    path = candidate / name
    if mutation == "missing":
        path.unlink()
    else:
        raw = path.read_bytes()
        path.write_bytes(raw + b"# changed\n" if mutation == "changed" else raw.replace(b"\n", b"\r\n"))
    with pytest.raises(receipt.UnitQualificationError):
        receipt.input_equivalence(source, candidate, manifest)


def test_source_manifest_is_admitted_from_the_source_checkout_without_duplicate_fields(tmp_path):
    source, _, manifest = _source_inputs(tmp_path)
    admitted, raw_digest = receipt.load_manifest(source, MANIFEST)
    assert admitted == manifest
    assert manifest_digest(admitted) == manifest_digest(manifest)
    assert raw_digest == hashlib.sha256((source / MANIFEST).read_bytes()).hexdigest()
    (source / MANIFEST).write_text('{"profile":"one","profile":"other"}')
    with pytest.raises(UnitEvidenceError):
        receipt.load_manifest(source, MANIFEST)


def _archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, raw in files.items():
            bundle.writestr(name, raw)
    return stream.getvalue()


def test_hosted_archive_and_canonical_packet_digests_are_distinct_and_both_checked():
    files = {name: json.dumps({"file": name}).encode() for name in receipt.PACKET_FILES}
    raw = _archive(files)
    digests = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    archive_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert archive_digest.removeprefix("sha256:") != receipt.canonical_digest(digests)
    receipt.verify_archive(raw, expected_digest=archive_digest, expected_size=len(raw), packet_files=digests)
    with pytest.raises(receipt.UnitQualificationError):
        receipt.verify_archive(
            raw, expected_digest="sha256:" + "0" * 64, expected_size=len(raw), packet_files=digests,
        )
    with pytest.raises(receipt.UnitQualificationError):
        receipt.verify_archive(
            raw, expected_digest=archive_digest, expected_size=len(raw),
            packet_files={**digests, "unit-proof.json": "0" * 64},
        )


@pytest.mark.parametrize("version", [1, True, 3])
def test_v2_verification_never_falls_back_to_general_ci(monkeypatch, version):
    def forbidden(*args, **kwargs):
        raise AssertionError("general CI must not participate in unit qualification")

    monkeypatch.setattr(qualification, "required_jobs", forbidden)
    monkeypatch.setattr(qualification, "_complete_run", forbidden)
    monkeypatch.setattr(qualification, "matrix", forbidden)
    with pytest.raises(VerificationError, match="explicit source-unit v2"):
        qualification.verify_receipt(
            {"schema": qualification.SCHEMA, "version": version},
            repository=REPOSITORY, candidate_sha=CANDIDATE, dev_sha=DEV, main_before=MAIN,
            control_sha=CONTROL, promotion_run="123-1", candidate_root=Path("."),
            expected_profile=qualification.UNIT_PROFILE,
        )
