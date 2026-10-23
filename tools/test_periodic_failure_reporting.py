"""Preserve periodic Dev failure ownership even before selection has outputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from _promotion_workflow_test_support import source_workflow
from test_post_ci_policy import _boolean_expression

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return source_workflow(ROOT, "windows-coverage-rotation.yml")


def condition(expression, event, ref, select, windows, linux, launch=False):
    context = {
        "always": True, "event": event, "ref": ref,
        "select": select, "windows": windows, "linux": linux,
        "launch": launch,
    }
    for old, new in (
        ("always()", "always"),
        ("github.event_name", "event"),
        ("inputs.ref", "ref"),
        ("!inputs.launch_maintenance", "not launch"),
        ("needs.select.result", "select"),
        ("needs.test.result", "windows"),
        ("needs.linux-full.result", "linux"),
        ("&&", "and"),
        ("||", "or"),
    ):
        expression = expression.replace(old, new)
    return _boolean_expression(expression, context)


@pytest.mark.parametrize("event,ref,select,windows,linux,expected", [
    ("schedule", "", "failure", "skipped", "skipped", True),
    ("schedule", "feature", "failure", "skipped", "skipped", True),
    ("workflow_dispatch", "", "failure", "skipped", "skipped", True),
    ("workflow_dispatch", "dev", "failure", "skipped", "skipped", True),
    ("workflow_dispatch", "feature", "failure", "skipped", "skipped", False),
    ("workflow_dispatch", "main", "failure", "skipped", "skipped", False),
    ("workflow_dispatch", "dev\nother=value", "failure", "skipped", "skipped", False),
    ("schedule", "", "success", "skipped", "failure", True),
    ("workflow_dispatch", "dev", "success", "failure", "success", True),
    ("workflow_dispatch", "feature", "success", "skipped", "failure", False),
    ("schedule", "", "success", "skipped", "success", False),
    ("schedule", "", "skipped", "skipped", "skipped", False),
    ("schedule", "", "cancelled", "skipped", "skipped", False),
    ("schedule", "", "success", "skipped", "cancelled", False),
    ("pull_request", "dev", "failure", "failure", "failure", False),
])
def test_actual_report_admission_does_not_depend_on_missing_outputs(
    event, ref, select, windows, linux, expected,
):
    report = workflow()["jobs"]["report"]
    assert "outputs" not in report["if"]
    assert condition(report["if"], event, ref, select, windows, linux) is expected


def test_existing_reporter_and_execution_boundaries_are_preserved():
    source = workflow()
    jobs = source["jobs"]
    assert set(jobs) == {"select", "test", "linux-full", "report"}
    assert jobs["test"]["if"] == "${{ false }}"
    assert jobs["linux-full"]["needs"] == "select"
    assert jobs["linux-full"]["if"] == "needs.select.outputs.sha != ''"
    report = jobs["report"]
    assert report["needs"] == ["select", "test", "linux-full"]
    assert report["permissions"] == {"contents": "read", "issues": "write"}
    assert all("checkout" not in step.get("uses", "") for step in report["steps"])
    steps = {step["name"]: step for step in report["steps"]}
    for name in ("Ensure the Linux periodic failure label exists",
                 "File or update the Linux portfolio tracking issue"):
        assert steps[name]["if"] == (
            "needs.select.result == 'failure' || needs.linux-full.result == 'failure'"
        )
    for name in ("Ensure the tracking label exists", "File or update the tracking issue"):
        assert steps[name]["if"] == "needs.test.result == 'failure'"
    assert source["concurrency"] == {
        "group": "windows-coverage-rotation", "cancel-in-progress": False,
    }


@pytest.mark.parametrize("ref", ["", "dev", "feature"])
def test_targeted_launch_qualification_never_files_the_shared_portfolio_issue(ref):
    report = workflow()["jobs"]["report"]
    assert not condition(
        report["if"], "workflow_dispatch", ref, "success", "skipped", "failure", launch=True,
    )


def test_targeted_launch_selection_preserves_container_and_exact_scope_evidence():
    source = workflow()
    triggers = source.get("on", source.get(True))
    option = triggers["workflow_dispatch"]["inputs"]["launch_maintenance"]
    assert option["type"] == "boolean" and option["default"] is False
    linux = source["jobs"]["linux-full"]
    matrix = linux["strategy"]["matrix"]["plugin"]
    assert """'["agent-worktrees","worktree-manager","update-driver"]'""" in matrix
    selector = linux["env"]["TEST_SELECTOR"]
    for name in (
        "test_update_launch_cli", "test_update_driver_json", "test_update_stage", "test_reconcile_status",
        "test_launch_update_requests",
        "test_launch_preparation_progress", "test_launch_session_unwrap",
        "test_core", "test_participant_transport",
        "test_real_product_exit_cannot_hide_abandoned_execution_custody",
    ):
        assert name in selector
    execute = next(step for step in linux["steps"] if step.get("id") == "full")
    assert 'arguments+=(-k "$TEST_SELECTOR")' in execute["run"]
    assert 'tools/run_tests_in_devcontainer.py "${arguments[@]}"' in execute["run"]
    evidence = next(
        step for step in linux["steps"] if step.get("name") == "Record exact execution scope and outcome"
    )
    assert '"Linux launch-maintenance selection only; no Windows or broker qualification"' in evidence["run"]
    assert 'command += ["-k", selector]' in evidence["run"]


@pytest.mark.skipif(os.name == "nt", reason="The production report is a Linux Bash consumer")
@pytest.mark.parametrize("selection,sha,existing,provider_error", [
    ("failure", "", True, False),
    ("failure", "a" * 40, False, False),
    ("success", "b" * 40, True, False),
    ("failure", "", False, True),
])
def test_actual_shell_preserves_one_tracker_and_truthful_scope(
    tmp_path, selection, sha, existing, provider_error,
):
    bash = shutil.which("bash")
    assert bash, "Bash is required for the actual periodic reporter"
    step = next(
        step for step in workflow()["jobs"]["report"]["steps"]
        if step["name"] == "File or update the Linux portfolio tracking issue"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['CALLS']).open('a') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['issue', 'list']:\n"
        "    if os.environ['PROVIDER_ERROR'] == '1': sys.exit(1)\n"
        "    if os.environ['EXISTING'] == '1': print('7')\n"
        "else:\n"
        "    assert args[:2] in (['issue', 'comment'], ['issue', 'create'])\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    script = tmp_path / "report.sh"
    script.write_text(
        step["run"].replace("${{ github.repository }}", "owner/project"), encoding="utf-8",
    )
    calls = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"])
    env.update(
        SELECTION_RESULT=selection, TESTED_SHA=sha,
        RUN_URL="https://github.com/owner/project/actions/runs/123",
        CALLS=str(calls), EXISTING=str(int(existing)), PROVIDER_ERROR=str(int(provider_error)),
    )
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10,
    )
    events = [json.loads(line) for line in calls.read_text().splitlines()]
    assert events[0][:2] == ["issue", "list"]
    assert events[0][events[0].index("--label") + 1] == "periodic-linux-integration-failure"
    if provider_error:
        assert result.returncode != 0 and len(events) == 1
        return
    assert result.returncode == 0, result.stderr
    assert len(events) == 2
    assert events[1][:2] == ["issue", "comment" if existing else "create"]
    body = events[1][events[1].index("--body") + 1]
    assert "Failure owner: Test Portfolio Rationalization #1303" in body
    assert "https://github.com/owner/project/actions/runs/123" in body
    assert "not a promotion or required-PR gate" in body
    if selection == "failure":
        assert "before portfolio execution" in body
        assert "No Linux or Windows validation evidence is claimed" in body
        assert (sha or "unavailable") in body
        assert "portfolio failed at" not in body
    else:
        assert f"portfolio failed at {sha}" in body
        assert "selection/preparation failed" not in body
