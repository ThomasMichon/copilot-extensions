"""Production metadata partition and selected Main admission contracts."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from test_post_ci_policy import (
    _boolean_expression,
    baseline_fixture as _baseline_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
LOCKDOWN = "workflow-lockdown-guard.yml"
DEPARTURES = "source-attribution-departures.yml"
REFRESH = "refresh-departed-source-attribution"
baseline_fixture = _baseline_fixture


def workflow(text):
    value = yaml.safe_load(text)
    if True in value:
        value["on"] = value.pop(True)
    return value


@pytest.fixture(scope="module")
def metadata_workflows():
    return {
        name: workflow((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        for name in (LOCKDOWN, DEPARTURES)
    }


@pytest.fixture(scope="module")
def metadata_snapshot():
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GH_"))}

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(ROOT), "-c", "core.fsmonitor=false", *args],
            env=env, check=True, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout

    revision = os.environ.get("METADATA_PREFLIGHT_SHA") or git("rev-parse", "HEAD").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        pytest.fail("METADATA_PREFLIGHT_SHA must name one available immutable commit")
    workflows = {}
    for filename in (LOCKDOWN, DEPARTURES, "base-branch-reminder.yml"):
        workflows[filename] = workflow(git("show", f"{revision}:.github/workflows/{filename}"))
    return revision, workflows


def admitted(workflow, action, base):
    event = workflow["on"]["pull_request_target"]
    assert set(event) <= {"branches", "types"}
    assert set(event.get("branches", [])) <= {"dev", "main"}
    return action in event["types"] and ("branches" not in event or base in event["branches"])


def selected(condition, action, base, old_base):
    for source, target in (
        ("github.event.changes.base.ref.from", "old_base"),
        ("github.event.pull_request.base.ref", "base"),
        ("github.event.action", "action"),
        ("github.event_name", "event"),
        ("&&", "and"),
        ("||", "or"),
    ):
        condition = condition.replace(source, target)
    return _boolean_expression(condition, {
        "event": "pull_request_target", "action": action,
        "base": base, "old_base": old_base,
    })


def handlers(workflows, action, base, old_base):
    result = []
    for filename in (LOCKDOWN, DEPARTURES):
        workflow = workflows[filename]
        if not admitted(workflow, action, base):
            continue
        for job in workflow["jobs"].values():
            scripts = "\n".join(step.get("with", {}).get("script", "") for step in job["steps"])
            calls = [name for name in ("enforce", "departed") if f".{name}(" in scripts]
            if calls and selected(job["if"], action, base, old_base):
                result.extend(calls)
    return sorted(result)


@pytest.mark.parametrize("action", [
    "opened", "reopened", "synchronize", "edited", "ready_for_review",
    "labeled", "unlabeled", "closed",
])
@pytest.mark.parametrize("base", ["dev", "main", "feature/other"])
@pytest.mark.parametrize("old_base", ["", "dev", "main"])
def test_source_partition_keeps_current_and_departed_head_obligations(
    metadata_workflows, action, base, old_base,
):
    expected = []
    if base == "dev" and action != "closed":
        expected.append("enforce")
    if (
        action in ("synchronize", "closed") and base == "dev"
        or action == "edited" and old_base == "dev" and base != "dev"
    ):
        expected.append("departed")
    assert handlers(metadata_workflows, action, base, old_base) == sorted(expected)


def test_source_keeps_job_permissions_and_promotion_consumer(metadata_snapshot, metadata_workflows):
    _, snapshot = metadata_snapshot
    original = snapshot[LOCKDOWN]
    current = metadata_workflows[LOCKDOWN]
    assert {key: value for key, value in current.items() if key != "jobs"} == {
        key: value for key, value in original.items() if key != "jobs"
    }
    for name in ("workflow-lockdown-guard", "source-attribution", "refresh-source-attribution"):
        assert current["jobs"][name] == original["jobs"][name]
    assert {key: value for key, value in current["jobs"][REFRESH].items() if key != "if"} == {
        key: value for key, value in snapshot[DEPARTURES]["jobs"][REFRESH].items() if key != "if"
    }
    assert metadata_workflows[DEPARTURES]["jobs"] == snapshot[DEPARTURES]["jobs"]


@pytest.mark.parametrize("action,base,expected", [
    ("synchronize", "dev", 1),
    ("synchronize", "main", 0),
    ("closed", "dev", 1),
    ("edited", "dev", 2),
    ("edited", "feature/other", 1),
])
def test_source_workflow_admission_counts(metadata_workflows, action, base, expected):
    assert sum(
        admitted(metadata_workflows[name], action, base) for name in (LOCKDOWN, DEPARTURES)
    ) == expected


@pytest.mark.parametrize("shape,expected", [
    ("workflows", True), ("helper-mixed", False),
    ("outsider", False), ("unchanged", False),
])
def test_actual_selected_main_gate_on_real_git_candidate(
    metadata_snapshot, metadata_workflows, baseline_fixture, shape, expected,
):
    revision, workflows = metadata_snapshot
    run, git, repository, _, _, _, temporary = baseline_fixture
    directory = repository / ".github" / "workflows"
    directory.mkdir(parents=True)
    for name in (LOCKDOWN, DEPARTURES):
        (directory / name).write_text("name: Prior metadata fixture\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "original metadata workflows")
    base = git("rev-parse", "HEAD")
    if shape != "unchanged":
        for name, declaration in metadata_workflows.items():
            (directory / name).write_text(
                yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8",
            )
        if shape == "helper-mixed":
            helper = repository / ".github" / "actions" / "unapproved.cjs"
            helper.parent.mkdir()
            helper.write_text("throw new Error('candidate code must not execute');\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-qm", shape)
    head = git("rev-parse", "HEAD")
    code = workflows["base-branch-reminder.yml"]["jobs"]["main-gate"]["steps"][-1]["run"]
    # Match Actions' step files, avoiding multiline Bash transport through Windows argv.
    script = temporary / "metadata-main-gate.sh"
    script.write_bytes(code.encode("utf-8"))
    result = run(
        f'cd "{repository.as_posix()}"\nsource "{script.as_posix()}"',
        HEAD_REF="pr/metadata-preflight", PR_AUTHOR="outsider" if shape == "outsider" else "owner",
        REPO_OWNER="owner", BASE_SHA=base, HEAD_SHA=head,
        PR_COMMITS="0" if shape == "unchanged" else "1",
        HEAD_REPOSITORY="example/project", TARGET_REPOSITORY="example/project",
    )
    assert (result.returncode == 0) is expected, revision + result.stdout + result.stderr
    diagnostic = {
        "workflows": "Workflow-file-only bootstrap PR",
        "helper-mixed": "file(s) outside .github/workflows/",
        "outsider": "not the promotion pipeline's own PR",
        "unchanged": "No changed files detected",
    }
    assert diagnostic[shape] in result.stdout, result.stdout + result.stderr
