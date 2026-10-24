"""Execute the workflow's exact merge waiter without network calls or sleeps.

Use --workflow PATH under the bounded supervisor to rehearse independent Main
control while the default Dev template still uses the legacy controller.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from _promotion_workflow_test_support import (
    api_response, assert_source_owned_boundary, source_owned, unit_profile,
)

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "validate-and-promote.yml"
SHA = "a" * 40


@pytest.fixture
def waiter(monkeypatch, tmp_path):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    merge_job = workflow["jobs"]["merge-promotion"]
    current = source_owned(workflow["jobs"])
    steps = merge_job["steps"]
    step = next(s for s in steps if s.get("id") == "merged")
    match = re.search(r"^python - <<'PY'\s*\n(.*?)^PY\s*$", step["run"], re.M | re.S)
    assert match is not None, "promotion waiter must contain a complete Python heredoc"
    code = match.group(1)
    output = tmp_path / "output"
    for key, value in {
        "PROMOTION_PR": "123",
        "GITHUB_REPOSITORY": "example/project",
        "CANDIDATE_BRANCH": "release/promote-1",
        "CANDIDATE_SHA": SHA,
        "MAIN_BEFORE": "b" * 40,
        "PROMOTION_MERGE_TOKEN": "synthetic-actions-token",
        "GH_TOKEN": "synthetic-read-token" if current else "synthetic-actions-token",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    assert workflow["concurrency"]["cancel-in-progress"] is False
    if current:
        assert_source_owned_boundary(workflow["jobs"])
        for key, value in {
            "DEV_SHA": "d" * 40, "CONTROL_SHA": "c" * 40,
            "QUALIFIED_CANDIDATE": SHA, "QUALIFIED_DEV": "d" * 40,
            "QUALIFIED_MAIN_BEFORE": "b" * 40, "QUALIFIED_CONTROL": "c" * 40,
            "QUALIFIED_TREE": "e" * 40, "QUALIFICATION_RUN": "789",
            "QUALIFICATION_ATTEMPT": "2", "QUALIFICATION_DIGEST": "1" * 64,
            "PUBLICATION_DIGEST": "2" * 64, "SOURCE_RELEASE": f"python-artifacts-{'d' * 40}",
            "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1",
            "QUALIFICATION_PROMOTION_RUN": "1-1",
        }.items():
            monkeypatch.setenv(key, value)
    else:
        assert set(merge_job["needs"]) == {
            "promote", "materialized-candidate-plan", "materialized-candidate-consumers",
            "materialized-candidate-windows",
        }
        assert "needs.materialized-candidate-plan.result == 'success'" in merge_job["if"]
        assert "needs.materialized-candidate-consumers.result == 'success'" in merge_job["if"]
        assert "needs.materialized-candidate-windows.result == 'success'" in merge_job["if"]
    if "source-units" in workflow["jobs"]:
        for key, value in {
            "QUALIFICATION_PROFILE": unit_profile(workflow["jobs"]),
            "QUALIFICATION_RUN": "1", "QUALIFICATION_ATTEMPT": "1",
            "QUALIFICATION_UNIT_JOB": "456", "QUALIFICATION_UNIT_ARTIFACT": "789",
            "QUALIFICATION_UNIT_ARTIFACT_DIGEST": "sha256:" + "a" * 64,
            "QUALIFICATION_PACKET_DIGEST": "b" * 64,
            "QUALIFICATION_UNIT_MANIFEST_DIGEST": "c" * 64,
            "QUALIFICATION_UNIT_INPUTS_DIGEST": "d" * 64,
        }.items():
            monkeypatch.setenv(key, value)
    assert merge_job["permissions"]["contents"] == "write"
    assert merge_job["permissions"]["pull-requests"] == "write"
    assert "secrets." not in json.dumps(merge_job)
    assert len(steps) == 1 and "uses" not in step
    assert step["env"]["PROMOTION_MERGE_TOKEN"] == "${{ github.token }}"
    publishing = workflow["jobs"]["publish-python-artifacts"]
    if current:
        record = workflow["jobs"]["record-promotion"]
        tag = next(s for s in record["steps"] if "tag_promotion.py" in s.get("run", ""))
        assert tag["env"]["MERGE_SHA"] == "${{ needs.merge-promotion.outputs.merge_sha }}"
        assert tag["env"]["SOURCE_RELEASE"] == "${{ needs.publish-python-artifacts.outputs.source_release }}"
        assert '--merge-sha "$MERGE_SHA" --dev-sha "$DEV_SHA"' in tag["run"]
        assert 'DEV_SHA="${SOURCE_RELEASE#python-artifacts-}"' in tag["run"]
    else:
        assert "merge-promotion" in publishing["needs"]
        tag = next(s for s in publishing["steps"] if s.get("id") == "tag")
        assert publishing["env"]["MERGE_SHA"] == "${{ needs.merge-promotion.outputs.merge_sha }}"
        assert publishing["env"]["DEV_SHA"] == "${{ needs.gate.outputs.sha }}"
        assert 'tools/tag_promotion.py --merge-sha "$MERGE_SHA" --dev-sha "$DEV_SHA"' in tag["run"]
    assert "date -u" not in tag["run"]
    assert "git rev-parse origin/main" not in tag["run"]
    return code, output


def response(state="MERGED", sha=SHA, **changes):
    return {
        "state": state, "mergeCommit": {"oid": sha},
        "headRefName": "release/promote-1", "headRefOid": SHA, "baseRefName": "main",
        "baseRefOid": "b" * 40, **changes,
    }


def strict_rules():
    return [{"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": True,
        "required_status_checks": [{"context": "main source gate"}],
    }}]


def test_pending_checks_wait_then_merge_only_the_exact_head_with_scoped_write_authority(
    waiter, monkeypatch,
):
    code, output = waiter
    replies = iter([response("OPEN"), response("OPEN"), response()])
    calls = []
    checks = iter(["pending", "pass"])

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["timeout"] <= 60
        assert not output.exists()
        assert "PROMOTION_MERGE_TOKEN" not in kwargs["env"]
        if argv[1] == "api":
            assert kwargs["env"]["GH_TOKEN"] == os.environ["GH_TOKEN"]
            return subprocess.CompletedProcess(argv, 0, api_response(argv, strict_rules()))
        if argv[2] == "merge":
            assert kwargs["env"]["GH_TOKEN"] == "synthetic-actions-token"
            assert kwargs["check"] is True
            assert "--auto" not in argv
            assert argv[argv.index("--match-head-commit") + 1] == SHA
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert kwargs["env"]["GH_TOKEN"] == os.environ["GH_TOKEN"]
        if argv[2] == "checks":
            assert kwargs["stderr"] == subprocess.PIPE
            assert "--required" in argv
            bucket = next(checks)
            return subprocess.CompletedProcess(
                argv, 8 if bucket == "pending" else 0,
                json.dumps([{"name": "gate", "bucket": bucket, "link": ""}]), "",
            )
        assert "stderr" not in kwargs
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(argv, 0, json.dumps(next(replies)))

    monkeypatch.setattr(subprocess, "run", run)
    exec(compile(code, str(WORKFLOW), "exec"), {})
    assert len(calls) == (
        10 if "QUALIFICATION_UNIT_JOB" in code else 8 if "QUALIFICATION_RUN" in code else 7
    )
    assert len([argv for argv in calls if argv[2] == "merge"]) == 1
    assert output.read_text() == f"merge_sha={SHA}\n"


@pytest.mark.parametrize("reply", [
    response("CLOSED"), response(sha=None),
    response(headRefName="other"), response(baseRefName="dev"),
    response(headRefOid="b" * 40),
    response("OPEN", baseRefOid="c" * 40),
])
def test_invalid_or_unmerged_candidate_cannot_authorize_tag(waiter, monkeypatch, reply):
    code, output = waiter
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(reply)),
    )
    with pytest.raises(RuntimeError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


def test_timeout_cannot_authorize_tag(waiter, monkeypatch):
    code, output = waiter
    clock = iter([0, 18001])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


def test_provider_error_is_not_a_successful_promotion(waiter, monkeypatch):
    code, output = waiter

    def run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


@pytest.mark.parametrize("bucket", ["fail", "cancel", "skipping"])
def test_required_check_failure_is_not_reported_as_merge_timeout(waiter, monkeypatch, bucket):
    code, output = waiter

    def run(argv, **kwargs):
        if argv[2] == "view":
            assert "stderr" not in kwargs
            return subprocess.CompletedProcess(argv, 0, json.dumps(response("OPEN")))
        return subprocess.CompletedProcess(argv, 1, json.dumps([
            {"name": "required gate", "bucket": bucket, "link": "https://example.com/check"},
        ]), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="required gate"):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


@pytest.mark.parametrize("message", [
    "no checks reported on the 'release/promote-1' branch",
    "no required checks reported on the 'release/promote-1' branch",
])
def test_delayed_required_check_registration_keeps_waiting(waiter, monkeypatch, message):
    code, output = waiter
    views = iter([response("OPEN"), response()])

    def run(argv, **kwargs):
        if argv[2] == "view":
            return subprocess.CompletedProcess(argv, 0, json.dumps(next(views)))
        return subprocess.CompletedProcess(argv, 1, "", message + "\n")

    monkeypatch.setattr(subprocess, "run", run)
    exec(compile(code, str(WORKFLOW), "exec"), {})
    assert output.read_text() == f"merge_sha={SHA}\n"


def test_check_provider_failure_keeps_stderr_visible(waiter, monkeypatch, capsys):
    code, output = waiter

    def run(argv, **kwargs):
        if argv[2] == "view":
            return subprocess.CompletedProcess(argv, 0, json.dumps(response("OPEN")))
        return subprocess.CompletedProcess(argv, 2, "", "provider refused request\n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert "provider refused request" in capsys.readouterr().err
    assert not output.exists()


def test_head_move_while_checks_are_pending_never_leaves_auto_merge_armed(waiter, monkeypatch):
    code, output = waiter
    views = iter([response("OPEN"), response("OPEN", headRefOid="b" * 40)])
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "api":
            return subprocess.CompletedProcess(argv, 0, api_response(argv, strict_rules()))
        if argv[2] == "view":
            return subprocess.CompletedProcess(argv, 0, json.dumps(next(views)))
        assert argv[2] == "checks"
        return subprocess.CompletedProcess(
            argv, 8, '[{"name":"gate","bucket":"pending","link":""}]', "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="identity changed"):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()
    assert not any(argv[2] == "merge" for argv in calls)


def test_atomic_head_or_policy_refusal_has_no_auto_merge_fallback(waiter, monkeypatch, capsys):
    code, output = waiter
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "api":
            return subprocess.CompletedProcess(argv, 0, api_response(argv, strict_rules()))
        if argv[2] == "view":
            return subprocess.CompletedProcess(argv, 0, json.dumps(response("OPEN")))
        if argv[2] == "checks":
            return subprocess.CompletedProcess(
                argv, 0, '[{"name":"gate","bucket":"pass","link":""}]', "",
            )
        assert argv[2] == "merge"
        assert "--auto" not in argv
        raise subprocess.CalledProcessError(1, argv, stderr="head or policy changed")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()
    assert len([argv for argv in calls if argv[2] == "merge"]) == 1
    assert "head or policy changed" in capsys.readouterr().err


def test_deadline_expiring_during_checks_cannot_start_a_merge(waiter, monkeypatch):
    code, output = waiter
    clock = iter([0, 1, 2, 18001])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[2] == "view":
            return subprocess.CompletedProcess(argv, 0, json.dumps(response("OPEN")))
        assert argv[2] == "checks"
        return subprocess.CompletedProcess(
            argv, 0, '[{"name":"gate","bucket":"pass","link":""}]', "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(TimeoutError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()
    assert not any(argv[2] == "merge" for argv in calls)


def test_missing_write_authority_cannot_fall_back_to_ambient_auth(waiter, monkeypatch):
    code, output = waiter
    monkeypatch.setenv("PROMOTION_MERGE_TOKEN", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("unexpected backend call"))
    with pytest.raises(RuntimeError, match="authority is unavailable"):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


@pytest.mark.parametrize("endpoint,field,value", [
    ("jobs", "id", True), ("jobs", "id", 457), ("jobs", "run_id", 2),
    ("jobs", "run_attempt", True), ("jobs", "run_attempt", 2),
    ("jobs", "head_sha", "f" * 40), ("jobs", "name", "full"),
    ("jobs", "status", "in_progress"), ("jobs", "conclusion", "skipped"),
    ("jobs", "steps", []),
    ("jobs", "steps", [{"name": "Upload promotion unit evidence", "status": "completed",
                       "conclusion": "failure"}]),
    ("artifacts", "id", True), ("artifacts", "id", 790),
    ("artifacts", "name", "unrelated"), ("artifacts", "expired", True),
    ("artifacts", "digest", "sha256:" + "f" * 64),
    ("artifacts", "workflow_run", {"id": 2, "head_branch": "main", "head_sha": "c" * 40}),
    ("artifacts", "workflow_run", {"id": 1, "head_branch": "dev", "head_sha": "c" * 40}),
    ("artifacts", "workflow_run", {"id": 1, "head_branch": "main", "head_sha": "f" * 40}),
])
def test_source_unit_provider_binding_cannot_drift(waiter, monkeypatch, endpoint, field, value):
    code, output = waiter
    selected = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if "source-units" not in selected["jobs"]:
        pytest.skip("source-unit provider bindings do not apply to the selected legacy controller")
    assert "QUALIFICATION_UNIT_JOB" in code

    def run(argv, **kwargs):
        if argv[1] == "api":
            result = json.loads(api_response(argv, strict_rules()))
            if f"/actions/{endpoint}/" in argv[-1]:
                result[field] = value
        elif argv[2] == "view":
            result = response("OPEN")
        elif argv[2] == "checks":
            result = [{"name": "main source gate", "bucket": "pass", "link": ""}]
        else:
            pytest.fail("invalid provider evidence must never reach a merge write")
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


def test_missing_original_qualification_owner_refuses_before_provider_calls(waiter, monkeypatch):
    code, output = waiter
    if "QUALIFICATION_PROMOTION_RUN" not in code:
        pytest.skip("Legacy controller profile predates original qualification ownership")
    monkeypatch.delenv("QUALIFICATION_PROMOTION_RUN", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("unexpected backend call"))
    with pytest.raises(KeyError, match="QUALIFICATION_PROMOTION_RUN"):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


@pytest.mark.parametrize("rules", [
    [],
    [{"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": False,
        "required_status_checks": [{"context": "main source gate"}],
    }}],
    [{"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": True,
        "required_status_checks": [{"context": "unrelated"}],
    }}],
])
def test_missing_strict_server_policy_cannot_authorize_merge(waiter, monkeypatch, rules):
    code, output = waiter
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "api":
            return subprocess.CompletedProcess(argv, 0, json.dumps(rules))
        if argv[2] == "view":
            return subprocess.CompletedProcess(argv, 0, json.dumps(response("OPEN")))
        assert argv[2] == "checks"
        return subprocess.CompletedProcess(
            argv, 0, '[{"name":"main source gate","bucket":"pass","link":""}]', "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="strict server-enforced base freshness"):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()
    assert not any(argv[1:3] == ["pr", "merge"] for argv in calls)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, required=True)
    options, pytest_args = parser.parse_known_args(argv)
    workflow = options.workflow.resolve(strict=True)
    target = Path(__file__).resolve()

    class WorkflowOverlay:
        def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
            for item in items:
                if isinstance(item, pytest.Function) and Path(item.path).resolve() == target:
                    item.module.WORKFLOW = workflow

    return pytest.main(["-q", str(target), *pytest_args], plugins=[WorkflowOverlay()])


if __name__ == "__main__":
    sys.exit(main())
