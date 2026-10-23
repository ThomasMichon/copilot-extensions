"""Synthetic provider/packet tests; no Git, network or child actor is executed."""

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import ci_pr_unit_reuse as reuse
from candidate_qualification_receipt import UnitQualificationError
from promotion_unit_contract import UnitEvidenceError
from promotion_unit_tests.test_packet import SOURCE, packet_case as packet_case


def archive(directory, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name in reuse.PACKET_FILES:
            zipped.writestr(name, (directory / name).read_bytes())
        if extra:
            zipped.writestr(extra, b"unexpected")
    return stream.getvalue()


@pytest.fixture
def case(packet_case):
    directory, manifest, _, _, root = packet_case
    environment = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_SHA": SOURCE,
        "GITHUB_WORKFLOW_SHA": "d" * 40,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_WORKSPACE": str(root),
        "ImageOS": "ubuntu24",
        "ImageVersion": "test-image",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
        "HOME": "/synthetic/home",
        "PATH": "/synthetic/bin",
    }
    event = {
        "number": 7,
        "repository": {"id": 10, "full_name": "example/project"},
        "pull_request": {
            "number": 7,
            "base": {"ref": "dev", "sha": "c" * 40, "repo": {"id": 10}},
            "head": {"sha": "b" * 40, "repo": {"id": 10}},
        },
    }
    runtime = {
        "python": "test-python",
        "executable_sha256": "3" * 64,
        "implementation": "cpython",
        "cache_tag": "test-tag",
        "machine": "test-architecture",
        "kernel": "test-kernel",
        "distributions": {
            "example": {
                "version": "1",
                "record_sha256": "1" * 64,
                "files_sha256": "2" * 64,
            }
        },
    }
    identity = reuse.context(environment, event, manifest, runtime)
    run = {
        "id": 20,
        "run_attempt": 2,
        "workflow_id": 30,
        "path": reuse.WORKFLOW,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "b" * 40,
        "repository": {"id": 10},
        "head_repository": {"id": 10},
        "created_at": "2026-01-01T00:00:00Z",
        "pull_requests": [copy.deepcopy(event["pull_request"])],
    }
    job = {
        "id": 40,
        "run_id": 20,
        "run_attempt": 2,
        "head_sha": "b" * 40,
        "name": reuse.UNIT_JOB,
        "status": "completed",
        "conclusion": "success",
        "runner_id": 50,
        "started_at": "2026-01-01T00:00:01Z",
        "completed_at": "2026-01-01T00:00:08Z",
        "steps": [
            {
                "name": reuse.EXECUTE_STEP,
                "number": 1,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-01-01T00:00:02Z",
                "completed_at": "2026-01-01T00:00:04Z",
            },
            {
                "name": reuse.UPLOAD_STEP,
                "number": 2,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-01-01T00:00:05Z",
                "completed_at": "2026-01-01T00:00:07Z",
            },
        ],
    }
    result = SimpleNamespace(
        directory=directory,
        root=root,
        manifest=manifest,
        environment=environment,
        event=event,
        runtime=runtime,
        identity=identity,
        run=run,
        job=job,
        now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        calls=[],
    )
    result.raw = archive(directory)
    result.artifact = {
        "id": 60,
        "name": reuse.artifact_name(
            {**identity, "qualification_epoch": [["attempt", 20, 2]]},
            20,
            2,
        ),
        "expired": False,
        "digest": "sha256:" + hashlib.sha256(result.raw).hexdigest(),
        "size_in_bytes": len(result.raw),
        "created_at": "2026-01-01T00:00:06Z",
        "workflow_run": {
            "id": 20,
            "head_sha": "b" * 40,
            "repository_id": 10,
            "head_repository_id": 10,
        },
    }
    return result


def read_provider(case, path, binary=False):
    case.calls.append((path, binary))
    if "workflows/ci.yml/runs?" in path:
        return {"total_count": 1, "workflow_runs": [case.run]}
    if path.endswith("runs/20/artifacts?per_page=100"):
        return {"total_count": 1, "artifacts": [case.artifact]}
    if path.endswith("runs/20/attempts/2/jobs?per_page=100"):
        return {"total_count": 1, "jobs": [case.job]}
    assert path.endswith("artifacts/60/zip") and binary
    return case.raw


def resolve(case):
    return reuse.resolve(
        case.identity,
        case.manifest,
        case.root,
        100,
        lambda path, binary=False: read_provider(case, path, binary),
        case.now,
    )


def test_reuse_verifies_actual_archive_packet_and_exact_provider_attempt(case):
    result = resolve(case)
    assert result["mode"] == "reused"
    assert result["origin_run"] == "20" and result["origin_attempt"] == "2"
    assert result["origin_artifact"] == "60" and len(result["packet_sha256"]) == 64
    assert len(case.calls) == 4 and case.calls[-1][1]


def test_artifact_names_bind_each_producing_run_and_attempt(case):
    name = reuse.artifact_name(case.identity, 20, 2)
    assert name != reuse.artifact_name(case.identity, 20, 1)
    assert name != reuse.artifact_name(case.identity, 21, 2)


def test_context_fingerprints_identify_changes_without_disclosing_values(case):
    identity = copy.deepcopy(case.identity)
    identity["environment"]["PATH"] = "/private/synthetic/path"
    identity["source_root"] = "/private/synthetic/workspace"
    before = reuse.context_fingerprint(identity)
    rendered = json.dumps(before)
    assert "/private/" not in rendered
    assert identity["runtime"]["python"] not in rendered
    identity["environment"]["HOME"] = "/different/synthetic/home"
    after = reuse.context_fingerprint(identity)
    assert before["environment"]["HOME"] != after["environment"]["HOME"]
    assert before["runtime"] == after["runtime"]
    assert before["distributions"] == after["distributions"]


@pytest.mark.parametrize("run_id,attempt", [(0, 1), (1, 0), (True, 1), (1, True)])
def test_artifact_names_refuse_unqualified_origins(case, run_id, attempt):
    with pytest.raises(reuse.ReuseUnavailable, match="invalid-artifact-origin"):
        reuse.artifact_name(case.identity, run_id, attempt)


@pytest.mark.parametrize(
    "field",
    [
        "source_sha",
        "workflow_sha",
        "base_sha",
        "head_sha",
        "manifest_sha256",
        "pr",
        "base_ref",
        "repository_id",
        "head_repository_id",
        "runtime",
        "image",
        "environment",
        "source_root",
    ],
)
def test_any_unit_input_difference_retains_fresh_execution(case, field):
    identity = copy.deepcopy(case.identity)
    identity[field] = 999 if isinstance(identity[field], int) else "different"
    case.identity = identity
    assert resolve(case)["mode"] == "executed"
    assert not any(binary for _, binary in case.calls)


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("run", "event", "workflow_dispatch"),
        ("run", "status", "in_progress"),
        ("run", "conclusion", "failure"),
        ("run", "path", ".github/workflows/other.yml"),
        ("run", "run_attempt", True),
        ("run", "head_sha", "f" * 40),
        ("run", "repository", {"id": 999}),
        ("run", "head_repository", {"id": 999}),
        ("run", "created_at", "2025-12-30T00:00:00Z"),
        ("job", "run_attempt", 1),
        ("job", "run_attempt", True),
        ("job", "conclusion", "cancelled"),
        ("job", "head_sha", "e" * 40),
        ("job", "runner_id", None),
        ("job", "steps", []),
        ("artifact", "expired", True),
        ("artifact", "digest", "missing"),
        ("artifact", "size_in_bytes", 0),
        ("artifact", "size_in_bytes", True),
        ("artifact", "created_at", "2026-01-01T00:00:03Z"),
        ("artifact", "workflow_run", {"id": 999}),
        ("run", "pull_requests", []),
        ("run", "pull_requests", [{"number": 999}]),
    ],
)
def test_incomplete_failed_stale_or_wrong_attempt_evidence_cannot_authorize_reuse(
    case,
    target,
    key,
    value,
):
    getattr(case, target)[key] = value
    with pytest.raises(reuse.ReuseUnavailable):
        reuse.producer(case.identity, case.run, [case.job], case.artifact, case.now)


def test_reused_job_without_fresh_upload_cannot_become_another_producer(case):
    case.job["steps"][1]["conclusion"] = "skipped"
    with pytest.raises(reuse.ReuseUnavailable, match="fresh-execution-or-upload"):
        reuse.producer(case.identity, case.run, [case.job], case.artifact, case.now)


@pytest.mark.parametrize(
    "extra", ["../outside", "unit-reuse-context.json", "C:\\outside"]
)
def test_archive_inventory_cannot_expand_the_original_three_file_packet(case, extra):
    case.raw = archive(case.directory, extra)
    case.artifact["size_in_bytes"] = len(case.raw)
    case.artifact["digest"] = "sha256:" + hashlib.sha256(case.raw).hexdigest()
    with pytest.raises(UnitQualificationError):
        resolve(case)


@pytest.mark.parametrize("mutation", ["source", "effects", "skip", "coverage"])
def test_provider_digest_does_not_replace_canonical_coverage_and_phase_validation(
    case, mutation
):
    path = case.directory / "unit-proof.json"
    proof = json.loads(path.read_bytes())
    if mutation == "source":
        proof["source_sha"] = "f" * 40
    elif mutation == "effects":
        proof["effects"] = ["socket"]
    elif mutation == "skip":
        proof["reports"][0]["outcome"] = "skipped"
    else:
        proof["coverage_sha256"] = "0" * 64
    path.write_text(json.dumps(proof))
    case.raw = archive(case.directory)
    case.artifact["size_in_bytes"] = len(case.raw)
    case.artifact["digest"] = "sha256:" + hashlib.sha256(case.raw).hexdigest()
    with pytest.raises((UnitQualificationError, UnitEvidenceError)):
        resolve(case)


def test_archive_bytes_must_match_the_immutable_provider_digest(case):
    case.artifact["digest"] = "sha256:" + "0" * 64
    with pytest.raises(UnitQualificationError, match="conflicting bytes"):
        resolve(case)


def test_current_run_or_future_run_is_never_reused(case):
    case.run["run_attempt"] = 1
    for run_id in (100, 101):
        case.run["id"] = run_id
        before = len(case.calls)
        assert resolve(case)["mode"] == "executed"
        assert len(case.calls) == before + 1


@pytest.mark.parametrize(
    "status,conclusion",
    [
        ("completed", "failure"),
        ("completed", "cancelled"),
        ("in_progress", None),
        ("queued", None),
    ],
)
def test_newer_failed_or_pending_head_qualification_cannot_be_hidden_by_old_success(
    case,
    status,
    conclusion,
):
    newer = {**case.run, "id": 50, "status": status, "conclusion": conclusion}

    def read(path, binary=False):
        if "workflows/ci.yml/runs?" in path:
            return {"total_count": 2, "workflow_runs": [case.run, newer]}
        assert not binary, "old proof must not be downloaded after the epoch changes"
        return read_provider(case, path, binary)

    result = reuse.resolve(case.identity, case.manifest, case.root, 100, read, case.now)
    assert result["mode"] == "executed"
    assert result["reason"] == "no-qualified-completed-packet"


def test_duplicate_provider_runs_do_not_authorize_reuse(case):
    with pytest.raises(reuse.ReuseUnavailable, match="invalid-provider-run-inventory"):
        reuse.resolve(
            case.identity,
            case.manifest,
            case.root,
            100,
            lambda *_: {"total_count": 2, "workflow_runs": [case.run, case.run]},
            case.now,
        )


def test_lower_id_failed_retry_invalidates_a_higher_id_success(case):
    retry = {
        **case.run,
        "id": 10,
        "run_attempt": 3,
        "status": "completed",
        "conclusion": "failure",
        "run_started_at": "2026-01-01T00:00:50Z",
        "updated_at": "2026-01-01T00:00:55Z",
    }

    def read(path, binary=False):
        if "workflows/ci.yml/runs?" in path:
            return {"total_count": 2, "workflow_runs": [case.run, retry]}
        assert not binary, "retry epoch must invalidate the old higher-ID artifact"
        return read_provider(case, path, binary)

    result = reuse.resolve(case.identity, case.manifest, case.root, 100, read, case.now)
    assert result["mode"] == "executed"


def test_truncated_head_history_cannot_authorize_an_unknown_failure_epoch(case):
    with pytest.raises(reuse.ReuseUnavailable, match="invalid-provider-run-inventory"):
        reuse.resolve(
            case.identity,
            case.manifest,
            case.root,
            100,
            lambda *_: {"total_count": 11, "workflow_runs": [case.run]},
            case.now,
        )


def test_incomplete_artifact_or_job_pagination_is_not_assumed_complete(case):
    def read(path, binary=False):
        result = read_provider(case, path, binary)
        if "artifacts?" in path:
            result["total_count"] = 101
        return result

    with pytest.raises(reuse.ReuseUnavailable, match="incomplete-artifact"):
        reuse.resolve(case.identity, case.manifest, case.root, 100, read, case.now)


def test_child_environment_excludes_provider_credentials_and_event_epochs():
    environment = reuse.unit_environment(
        {
            "HOME": "/synthetic/home",
            "PATH": "/synthetic/bin",
            "GH_TOKEN": "synthetic-credential",
            "GITHUB_RUN_ID": "42",
            "GITHUB_OUTPUT": "/synthetic/output",
            "PRIVATE_VARIABLE": "not-a-unit-input",
        }
    )
    assert set(environment) == {
        "HOME",
        "PATH",
        "PYTHONHASHSEED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
    }
    assert environment["PYTHONHASHSEED"] == "0"


def test_installed_dependency_byte_change_changes_binding_without_a_version_change(
    tmp_path, monkeypatch
):
    library = tmp_path / "library.py"
    library.write_bytes(b"VALUE = 1\n")
    executable = tmp_path / "python"
    executable.write_bytes(b"synthetic-interpreter")
    distribution = SimpleNamespace(
        metadata={"Name": "example"},
        version="1",
        files=[Path("library.py")],
        read_text=lambda _: "unchanged RECORD",
        locate_file=lambda name: tmp_path / name,
    )
    monkeypatch.setattr(reuse.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(reuse.sys, "executable", str(executable))
    monkeypatch.setattr(
        reuse.importlib.metadata, "distributions", lambda: [distribution]
    )
    before = reuse.runtime_identity()
    library.write_bytes(b"VALUE = 2\n")
    after = reuse.runtime_identity()
    assert (
        before["distributions"]["example"]["version"]
        == after["distributions"]["example"]["version"]
    )
    assert (
        before["distributions"]["example"]["files_sha256"]
        != after["distributions"]["example"]["files_sha256"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"),
        ("ImageVersion", ""),
        ("RUNNER_OS", "Windows"),
        ("GITHUB_SERVER_URL", "https://example.invalid"),
    ],
)
def test_unknown_toolchains_and_non_pr_events_cannot_reuse(case, field, value):
    case.environment[field] = value
    with pytest.raises(reuse.ReuseUnavailable):
        reuse.context(case.environment, case.event, case.manifest, case.runtime)


@pytest.mark.parametrize(
    "event,mode,child_code",
    [
        ("pull_request", "reused", 0),
        ("pull_request", "executed", 0),
        ("pull_request", "executed", 7),
        ("workflow_dispatch", "executed", 0),
        ("future-event", "executed", 0),
        ("pull_request", "late", 0),
        ("pull_request", "retry", 0),
    ],
    ids=[
        "reused-0",
        "executed-0",
        "executed-7",
        "manual-executed-0",
        "unknown-executed-0",
        "late-proof-executed-0",
        "rerun-fresh-0",
    ],
)
def test_actual_cli_requires_zero_children_on_hit_and_one_real_runner_on_miss(
    case,
    tmp_path,
    monkeypatch,
    event,
    mode,
    child_code,
):
    (case.root / "tools/promotion-unit-manifest.json").write_text(
        json.dumps(case.manifest)
    )
    event_path, output, destination = (
        tmp_path / "event",
        tmp_path / "outputs",
        tmp_path / "result",
    )
    event_path.write_text(json.dumps(case.event))
    environment = {
        **case.environment,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "2" if mode == "retry" else "1",
        "GH_TOKEN": "synthetic-credential",
    }
    calls = []

    def process(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[0] == "git":
            return SimpleNamespace(
                stdout=(SOURCE + "\n").encode() if "rev-parse" in arguments else b""
            )
        assert arguments[1] == str(case.root / "tools/run-promotion-unit-tests.py")
        expected_environment = (
            reuse.unit_environment(environment)
            if event == "pull_request"
            else {key: value for key, value in environment.items() if key != "GH_TOKEN"}
        )
        assert kwargs["env"] == expected_environment
        if child_code == 0:
            destination.mkdir()
            for name in reuse.PACKET_FILES:
                (destination / name).write_bytes((case.directory / name).read_bytes())
        return SimpleNamespace(returncode=child_code)

    def lookup(*_):
        assert event == "pull_request"
        assert mode != "retry", "reruns must not try to consume old success"
        if mode == "executed":
            raise reuse.ReuseUnavailable("provider-unavailable")
        return {"mode": "reused", "origin_run": "20", "packet_sha256": "1" * 64}

    monkeypatch.setattr(reuse, "ROOT", case.root)
    monkeypatch.setattr(reuse.os, "environ", environment)
    monkeypatch.setattr(reuse, "_process", process)
    monkeypatch.setattr(reuse, "runtime_identity", lambda: case.runtime)
    monkeypatch.setattr(reuse, "resolve", lookup)
    if mode == "late":
        monkeypatch.setattr(reuse, "MAX_LOOKUP_SECONDS", -1)
    monkeypatch.setattr(
        reuse.sys,
        "argv",
        [
            "ci_pr_unit_reuse.py",
            "--source-sha",
            SOURCE,
            "--output-dir",
            str(destination),
        ],
    )
    assert reuse.main() == child_code
    expected_mode = "executed" if mode in ("late", "retry") else mode
    assert len([argv for argv, _ in calls if argv[0] != "git"]) == (
        expected_mode == "executed"
    )
    if child_code:
        assert not output.exists()
    else:
        assert "mode=" + expected_mode + "\n" in output.read_text()
        if mode == "late":
            assert "origin_run=100\n" in output.read_text()


@pytest.mark.parametrize("dirty", [False, True])
def test_current_checkout_contradictions_fail_instead_of_relabelling_fresh_proof(
    case,
    tmp_path,
    monkeypatch,
    dirty,
):
    (case.root / "tools/promotion-unit-manifest.json").write_text(
        json.dumps(case.manifest)
    )
    event, output = tmp_path / "event", tmp_path / "outputs"
    event.write_text(json.dumps(case.event))
    environment = {
        **case.environment,
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_OUTPUT": str(output),
    }

    def process(arguments, **_):
        assert arguments[0] == "git"
        if "rev-parse" in arguments:
            return SimpleNamespace(
                stdout=((SOURCE if dirty else "e" * 40) + "\n").encode()
            )
        return SimpleNamespace(stdout=b" M tools/example.py\n" if dirty else b"")

    monkeypatch.setattr(reuse, "ROOT", case.root)
    monkeypatch.setattr(reuse.os, "environ", environment)
    monkeypatch.setattr(reuse, "runtime_identity", lambda: case.runtime)
    monkeypatch.setattr(reuse, "_process", process)
    monkeypatch.setattr(
        reuse.sys,
        "argv",
        [
            "ci_pr_unit_reuse.py",
            "--source-sha",
            SOURCE,
            "--output-dir",
            str(tmp_path / "proof"),
        ],
    )
    with pytest.raises(RuntimeError, match="current checkout"):
        reuse.main()
    assert not output.exists()


@pytest.mark.parametrize("dirty", [False, True])
def test_checkout_validation_is_mandatory_when_toolchain_binding_is_unavailable(
    case,
    tmp_path,
    monkeypatch,
    dirty,
):
    (case.root / "tools/promotion-unit-manifest.json").write_text(
        json.dumps(case.manifest)
    )
    output = tmp_path / "outputs"
    environment = {
        **case.environment,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "1",
    }

    def process(arguments, **_):
        assert arguments[0] == "git", "wrong checkout must never execute units"
        if "rev-parse" in arguments:
            return SimpleNamespace(
                stdout=((SOURCE if dirty else "e" * 40) + "\n").encode()
            )
        return SimpleNamespace(stdout=b" M tools/example.py\n" if dirty else b"")

    def unavailable():
        raise reuse.ReuseUnavailable("unavailable-toolchain")

    monkeypatch.setattr(reuse, "ROOT", case.root)
    monkeypatch.setattr(reuse.os, "environ", environment)
    monkeypatch.setattr(reuse, "runtime_identity", unavailable)
    monkeypatch.setattr(reuse, "_process", process)
    monkeypatch.setattr(
        reuse.sys,
        "argv",
        [
            "ci_pr_unit_reuse.py",
            "--source-sha",
            SOURCE,
            "--output-dir",
            str(tmp_path / "proof"),
        ],
    )
    with pytest.raises(RuntimeError, match="current checkout"):
        reuse.main()
    assert not output.exists()
