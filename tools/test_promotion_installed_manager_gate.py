"""Exercise the native release gate's actual receipt consumer without native actors.

Synthetic receipts test custody and refusal only, never installed/TTY qualification.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from _promotion_workflow_test_support import heredoc, source_workflow

ROOT = Path(__file__).resolve().parents[1]
JOB = "native-installed-manager-windows"


@pytest.fixture
def workflow():
    return source_workflow(ROOT, "validate-and-promote.yml")


def _step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def _assert_job_env_has_no_runner_context(workflow):
    for name, job in workflow["jobs"].items():
        for expression in re.findall(r"\$\{\{(.*?)\}\}", json.dumps(job.get("env", {}))):
            assert re.search(r"\brunner\s*[.\[]", expression, re.I) is None, (
                f"runner context is unavailable in jobs.{name}.env"
            )


def test_runner_context_is_only_used_after_runner_allocation(workflow):
    _assert_job_env_has_no_runner_context(workflow)
    native = workflow["jobs"][JOB]
    selected = _step(native, "Set the native acceptance directory")
    assert selected["shell"] == "pwsh"
    assert r'"ACCEPTANCE_ROOT=$env:RUNNER_TEMP\candidate\it"' in selected["run"]
    assert "Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append" in selected["run"]
    assert [
        step for step in native["steps"] if "ACCEPTANCE_ROOT=" in step.get("run", "")
    ] == [selected]


@pytest.mark.parametrize("expression", [
    "${{ runner.temp }}", "${{ runner['temp'] }}", "${{ Runner.Temp }}",
])
def test_job_env_runner_context_regression_is_rejected(workflow, expression):
    invalid = copy.deepcopy(workflow)
    invalid["jobs"][JOB]["env"]["ACCEPTANCE_ROOT"] = expression
    with pytest.raises(AssertionError, match="runner context is unavailable"):
        _assert_job_env_has_no_runner_context(invalid)


def test_native_gate_installs_and_validates_the_same_frozen_candidate_before_publication(workflow):
    jobs = workflow["jobs"]
    native = jobs[JOB]
    assert set(native["needs"]) == {"gate", "compose-candidate"}
    assert native["if"] == (
        "needs.gate.outputs.is_dev == 'true' && needs.compose-candidate.outputs.composed == 'true'"
    )
    assert native["runs-on"] == "windows-latest"
    assert native["permissions"] == {"contents": "read"}
    assert native["timeout-minutes"] == 15
    assert native.get("continue-on-error", False) is False
    assert all(step.get("continue-on-error", False) is False for step in native["steps"])
    assert "secrets." not in json.dumps(native)
    checkout = native["steps"][0]["with"]
    assert checkout["ref"] == "${{ needs.gate.outputs.sha }}"
    assert checkout["persist-credentials"] is False
    assert native["env"]["DEV_SHA"] == "${{ needs.gate.outputs.sha }}"
    assert native["env"]["MAIN_BEFORE"] == "${{ needs.gate.outputs.main_before }}"
    assert native["env"]["CANDIDATE_SHA"] == "${{ needs.compose-candidate.outputs.candidate_sha }}"
    assert native["env"]["CANDIDATE_TREE"] == "${{ needs.compose-candidate.outputs.tree }}"
    assert "ACCEPTANCE_ROOT" not in native["env"]
    assert "runner." not in json.dumps(native["env"])
    directory = _step(native, "Set the native acceptance directory")
    assert directory["shell"] == "pwsh"
    assert r'"ACCEPTANCE_ROOT=$env:RUNNER_TEMP\candidate\it"' in directory["run"]
    assert "Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append" in directory["run"]
    imported = _step(native, "Import the frozen candidate without recomposition")
    assert native["steps"].index(directory) < native["steps"].index(imported)
    assert "--directory \"$RUNNER_TEMP/frozen-candidate\"" in imported["run"]
    for argument in (
        '--dev-sha "$DEV_SHA"', '--main-before "$MAIN_BEFORE"',
        '--candidate-sha "$CANDIDATE_SHA"', '--checkout "$RUNNER_TEMP/candidate"',
    ):
        assert argument in imported["run"]
    install = _step(native, "Install and exercise the generated project binstub in real Windows terminals")
    validate = _step(native, "Require complete candidate-bound native semantics")
    receipt = _step(native, "Bind the validated receipt to its producing attempt")
    upload = _step(native, "Retain installed Manager qualification evidence")
    assert native["steps"].index(imported) < native["steps"].index(install)
    assert native["steps"].index(install) < native["steps"].index(validate)
    assert native["steps"].index(validate) < native["steps"].index(receipt)
    assert native["steps"].index(receipt) < native["steps"].index(upload)
    for step in (install, validate):
        assert step["working-directory"] == r"${{ runner.temp }}\candidate"
        assert "python tools/run-manager-runtime-acceptance.py --contract installed-tty" in step["run"]
        assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in step["run"]
        for flag, value in (
            ("candidate-commit", "CANDIDATE_SHA"), ("candidate-tree", "CANDIDATE_TREE"),
            ("source-commit", "DEV_SHA"), ("main-before", "MAIN_BEFORE"),
            ("run-id", "GITHUB_RUN_ID"), ("run-attempt", "GITHUB_RUN_ATTEMPT"),
        ):
            assert f'--{flag} "$env:{value}"' in step["run"]
    assert '--root "$env:ACCEPTANCE_ROOT"' in install["run"]
    assert '--output "$env:ACCEPTANCE_ROOT\\installed-tty-result.json"' in install["run"]
    assert '--validate-result "$env:ACCEPTANCE_ROOT\\installed-tty-result.json"' in validate["run"]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["name"] == "promotion-installed-manager-${{ github.run_id }}-${{ github.run_attempt }}"
    assert upload["with"]["path"].splitlines() == [
        "${{ env.ACCEPTANCE_ROOT }}/installed-tty-result.json",
        "${{ env.ACCEPTANCE_ROOT }}/diagnostics/**",
    ]
    assert native["outputs"]["complete"] == "${{ steps.receipt.outputs.complete }}"
    assert native["outputs"]["artifact_id"] == "${{ steps.upload.outputs.artifact-id }}"
    assert native["outputs"]["artifact_digest"] == "${{ steps.upload.outputs.artifact-digest }}"
    for name in ("python-artifacts", "promote", "report-failure"):
        assert JOB in jobs[name]["needs"]
    signer = jobs["python-artifacts"]["if"]
    assert f"needs.{JOB}.result == 'success'" in signer
    assert f"needs.{JOB}.outputs.complete == 'true'" in signer
    for output in ("attempt", "receipt_digest", "artifact_id", "artifact_digest"):
        assert f"needs.{JOB}.outputs.{output} != ''" in signer
    assert not any(function in signer for function in ("always()", "failure()", "cancelled()"))
    assert JOB not in jobs["source-units"]["needs"]
    assert "installed-tty" not in json.dumps(jobs["source-units"])
    decide = _step(jobs["promote"], "Decide whether this run should actually promote")["run"]
    guard = decide.split(f'if [ "${{{{ needs.{JOB}.result }}}}"', 1)[1].split("fi", 1)[0]
    assert '!= "success"' in guard
    assert f'needs.{JOB}.outputs.complete' in guard
    assert '!= "true"' in guard and "exit 1" in guard
    validation = decide.split('for r in "', 1)[1]
    assert 'needs.python-artifacts.result' in validation
    assert 'if [ "$r" != "success" ]' in validation
    assert "exit 1" in validation and "exit 0" not in validation
    assert JOB + ".result != 'success'" in jobs["report-failure"]["if"]
    consumer = _step(jobs["promote"], "Verify the native receipt and immutable artifact ownership before promotion")
    publish = next(step for step in jobs["promote"]["steps"] if step.get("id") == "promote")
    assert jobs["promote"]["steps"].index(consumer) < jobs["promote"]["steps"].index(publish)
    download = _step(jobs["promote"], "Download this native producing job's exact evidence artifact")
    assert download["with"]["artifact-ids"] == f"${{{{ needs.{JOB}.outputs.artifact_id }}}}"
    assert download["with"]["merge-multiple"] is True
    assert download["if"] == consumer["if"] == "steps.decide.outputs.proceed == 'true'"


@pytest.fixture
def consumer(workflow, tmp_path, monkeypatch):
    job = workflow["jobs"]["promote"]
    step = _step(job, "Verify the native receipt and immutable artifact ownership before promotion")
    report = {
        "schema": "worktree-manager.installed-tty", "version": 1,
        "identity": {
            "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
            "source_commit": "c" * 40, "main_before": "d" * 40,
            "run_id": 41, "run_attempt": 2,
        },
        "platform": "win32", "complete": True, "errors": [],
    }
    artifact = {
        "id": 71, "expired": False, "name": "promotion-installed-manager-41-2",
        "digest": "sha256:" + "e" * 64,
        "workflow_run": {"id": 41, "head_sha": "f" * 40},
    }
    path = tmp_path / "installed-tty-result.json"
    env = {
        "GITHUB_RUN_ID": "41", "GITHUB_RUN_ATTEMPT": "3",
        "GITHUB_REPOSITORY": "example/extensions",
        "NATIVE_ATTEMPT": "2", "NATIVE_ARTIFACT_ID": "71",
        "NATIVE_ARTIFACT_DIGEST": "e" * 64,
        "NATIVE_RECEIPT": str(path),
        "CANDIDATE_SHA": "a" * 40, "CANDIDATE_TREE": "b" * 40,
        "DEV_SHA": "c" * 40, "MAIN_BEFORE": "d" * 40, "CONTROL_SHA": "f" * 40,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []

    def api(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=json.dumps(artifact))

    monkeypatch.setattr(subprocess, "run", api)

    def execute(*, unchanged_digest=None):
        path.write_text(json.dumps(report), encoding="utf-8")
        monkeypatch.setenv(
            "NATIVE_RECEIPT_DIGEST", unchanged_digest or hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        exec(compile(heredoc(step["run"]), "<native-receipt-consumer>", "exec"), {})

    return SimpleNamespace(
        report=report, artifact=artifact, path=path, execute=execute, calls=calls, env=env,
    )


def test_exact_previously_validated_native_receipt_retains_its_original_producing_attempt(consumer):
    consumer.execute()
    assert consumer.calls == [(
        ["gh", "api", "repos/example/extensions/actions/artifacts/71"],
        {"check": True, "capture_output": True, "text": True, "timeout": 30},
    )]


@pytest.mark.parametrize("field,value", [
    ("candidate_commit", "9" * 40), ("candidate_tree", "9" * 40),
    ("source_commit", "9" * 40), ("main_before", "9" * 40),
    ("run_id", 40), ("run_attempt", 1), ("run_id", "41"), ("run_attempt", "2"),
])
def test_other_candidate_or_execution_receipts_refuse_before_provider_reads(consumer, field, value):
    consumer.report["identity"][field] = value
    with pytest.raises(RuntimeError, match="complete Windows qualification"):
        consumer.execute()
    assert not consumer.calls


@pytest.mark.parametrize("mutation", [
    {"schema": "unknown"}, {"version": True}, {"platform": "linux"},
    {"complete": False}, {"complete": "true"}, {"errors": ["timeout"]},
    {"identity": {}}, {"identity": None},
])
def test_failed_incomplete_and_unknown_native_results_refuse(consumer, mutation):
    consumer.report.update(copy.deepcopy(mutation))
    with pytest.raises(RuntimeError, match="complete Windows qualification"):
        consumer.execute()
    assert not consumer.calls


def test_changed_receipt_bytes_cannot_borrow_the_validators_digest(consumer):
    with pytest.raises(RuntimeError, match="digest changed"):
        consumer.execute(unchanged_digest="0" * 64)
    assert not consumer.calls


@pytest.mark.parametrize("field,value", [
    ("id", 72), ("id", "71"), ("expired", True), ("expired", None),
    ("name", "promotion-installed-manager-41-1"), ("digest", "sha256:" + "0" * 64),
    ("workflow_run", {"id": 40, "head_sha": "f" * 40}),
    ("workflow_run", {"id": 41, "head_sha": "0" * 40}),
    ("workflow_run", None),
])
def test_stale_missing_or_other_artifact_ownership_refuses(consumer, field, value):
    consumer.artifact[field] = value
    with pytest.raises(RuntimeError, match="another execution"):
        consumer.execute()


@pytest.mark.parametrize("key,value", [
    ("NATIVE_ATTEMPT", ""), ("NATIVE_ATTEMPT", "4"),
    ("NATIVE_ARTIFACT_ID", ""), ("NATIVE_ARTIFACT_ID", "0"),
    ("NATIVE_ARTIFACT_DIGEST", ""), ("NATIVE_ARTIFACT_DIGEST", "unknown"),
    ("CANDIDATE_SHA", "main"), ("CANDIDATE_TREE", "HEAD"),
])
def test_missing_or_unqualified_outputs_do_not_enable_promotion(consumer, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        consumer.execute()
    assert not consumer.calls


def test_unavailable_artifact_metadata_is_not_qualification(consumer, monkeypatch):
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 30)

    monkeypatch.setattr(subprocess, "run", unavailable)
    with pytest.raises(subprocess.TimeoutExpired):
        consumer.execute()
