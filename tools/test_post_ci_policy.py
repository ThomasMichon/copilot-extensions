"""Validate combined trusted CI follow-ups and execute the real ancestry filter."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from _promotion_workflow_test_support import source_workflow

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-trigger.yml"


def test_dependency_version_updates_use_the_contribution_branch():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    assert config["updates"]
    assert all(update["target-branch"] == "dev" for update in config["updates"])


def test_one_trusted_followup_keeps_jobs_and_credentials_independent():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {
        "workflow_run": {"workflows": ["CI", "Validate and promote dev to main"], "types": ["completed"]},
        "pull_request_target": {"branches": ["main"], "types": ["closed"]},
    }
    assert not (WORKFLOW.parent / "identifier-leak-guard.yml").exists()
    assert "concurrency" not in workflow
    assert workflow["permissions"] == {"contents": "read"}
    dispatch = workflow["jobs"]["filter-and-dispatch"]
    scan = workflow["jobs"]["scan-and-report"]
    assert "needs" not in dispatch and "needs" not in scan
    assert "github.event.workflow_run.conclusion == 'success'" in dispatch["if"]
    assert "github.event.workflow_run.event != 'pull_request'" in dispatch["if"]
    assert dispatch["permissions"] == {
        "contents": "write", "pull-requests": "read", "actions": "read",
    }
    assert scan["permissions"] == {
        "contents": "read", "pull-requests": "read", "issues": "write", "checks": "write",
    }
    assert "github.event.workflow_run.event == 'pull_request'" in scan["if"]
    assert "github.event.workflow_run.conclusion != 'cancelled'" in scan["if"]
    assert scan["steps"][0]["with"] == {
        "ref": "${{ github.workflow_sha }}", "fetch-depth": 1,
    }
    assert "git checkout" not in "\n".join(step.get("run", "") for step in scan["steps"])
    publish = scan["steps"][-1]
    assert publish["if"].startswith("always() &&")
    assert 'name: "identifier leak guard"' in publish["with"]["script"]
    assert "github.rest.checks.create" in publish["with"]["script"]
    assert "repository_dispatch" not in "\n".join(step.get("run", "") for step in scan["steps"])


def test_baseline_is_dev_owned_without_extra_workflow_or_verify_dispatch():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["refresh-module-baseline"]
    assert not (WORKFLOW.parent / "module-size-baseline-widen.yml").exists()
    assert not (WORKFLOW.parent / "module-size-baseline-widen-verify.yml").exists()
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "needs" not in job
    assert job["environment"] == "main-promotion"
    assert job["concurrency"] == {
        "group": "module-size-baseline-widen-dev", "cancel-in-progress": False,
    }
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    trusted = steps["Check out trusted workflow tooling without credentials"]
    candidate = steps["Check out committed dev input as data without credentials"]
    assert job["steps"].index(trusted) < job["steps"].index(candidate)
    assert trusted["with"]["ref"] == "${{ github.workflow_sha }}"
    assert candidate["with"]["ref"] == "${{ github.event.workflow_run.head_sha }}"
    assert trusted["with"]["persist-credentials"] is False
    assert candidate["with"]["persist-credentials"] is False
    refresh_step = steps["Refresh and validate using only trusted tooling"]
    refresh = refresh_step["run"]
    assert "python -I trusted/tools/check-module-size.py --repository candidate" in refresh
    assert "--refresh-baseline --widen-only" in refresh
    assert "--allow-widen" not in refresh
    publisher = steps["Open a baseline-only PR against dev"]
    assert publisher["with"]["base"] == "dev"
    assert publisher["with"]["add-paths"] == "tools/module-size-baseline.json"
    assert publisher["with"]["token"] == "${{ secrets.APERTURE_RELEASE_TOKEN }}"
    assert all(
        "APERTURE_RELEASE_TOKEN" not in json.dumps(step)
        for step in job["steps"] if step is not publisher
    )
    fence_step = steps["Fence stale dev inputs and restrict the publication diff"]
    assert job["steps"].index(candidate) < job["steps"].index(refresh_step)
    assert job["steps"].index(refresh_step) < job["steps"].index(fence_step)
    assert job["steps"].index(fence_step) < job["steps"].index(publisher)
    assert fence_step["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert fence_step["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    fence = fence_step["run"]
    assert "diff --no-ext-diff --no-textconv --name-only" in fence
    assert 'live_sha" != "$INPUT_SHA' in fence
    assert 'changed" = "tools/module-size-baseline.json' in fence
    assert "workflow run" not in "\n".join(step.get("run", "") for step in job["steps"])


def test_widen_only_keeps_ordinary_ceiling_checks_without_an_exact_baseline_gate():
    workflow = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    guards = workflow["jobs"]["checks"]
    commands = "\n".join(step.get("run", "") for step in guards["steps"])
    assert "check-module-size.py --changed-since" in commands
    assert "--check-automatic-baseline" not in commands
    assert "checks" in workflow["jobs"]["pr-gate"]["needs"]


def _boolean_expression(expression: str, context: dict[str, str | bool]) -> bool:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("invalid condition syntax") from exc
    allowed = (
        ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
        ast.Compare, ast.Eq, ast.NotEq, ast.Name, ast.Load, ast.Constant,
    )
    if any(type(value) not in (str, bool) for value in context.values()):
        raise ValueError("condition context must contain only strings and booleans")
    # Validate every branch before evaluation, including unreachable operands.
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError(f"unsupported condition node: {type(node).__name__}")
        if isinstance(node, ast.Constant) and type(node.value) not in (str, bool):
            raise ValueError("condition constants must be strings or booleans")
        if isinstance(node, ast.Name) and node.id not in context:
            raise ValueError(f"unknown condition name: {node.id}")

    def is_boolean_operand(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return type(context[node.id]) is bool
        if isinstance(node, ast.Constant):
            return type(node.value) is bool
        return isinstance(node, (ast.BoolOp, ast.UnaryOp, ast.Compare))

    for node in ast.walk(tree):
        operands = (
            node.values if isinstance(node, ast.BoolOp)
            else [node.operand] if isinstance(node, ast.UnaryOp) else []
        )
        if any(not is_boolean_operand(operand) for operand in operands):
            raise ValueError("condition operand must be boolean")

    def boolean(value: str | bool) -> bool:
        if type(value) is not bool:
            raise ValueError("condition operand must be boolean")
        return value

    def evaluate(node: ast.AST) -> str | bool:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value
        if isinstance(node, ast.Name):
            return context[node.id]
        if isinstance(node, ast.UnaryOp):
            return not boolean(evaluate(node.operand))
        if isinstance(node, ast.BoolOp):
            values = (boolean(evaluate(value)) for value in node.values)
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            values = [evaluate(node.left), *(evaluate(value) for value in node.comparators)]
            return all(
                left == right if isinstance(op, ast.Eq) else left != right
                for left, op, right in zip(values, node.ops, values[1:])
            )
        raise ValueError(f"unsupported condition node: {type(node).__name__}")

    return boolean(evaluate(tree))


@pytest.mark.parametrize("expression", [
    "__import__('os')", "event.__class__", "event[0]",
    "[value for value in event]", "(lambda: True)()", "{'event': event}",
    "event in 'pull_request'", "1 == 1", "None == None", "'true'",
    "True or missing", "False and event.__class__", "True or event[0]",
    "False and __import__('os')", "True or 1", "not event", "False and event",
])
def test_condition_evaluator_rejects_unsupported_expressions(expression: str) -> None:
    with pytest.raises(ValueError):
        _boolean_expression(expression, {"event": "pull_request"})


def test_condition_evaluator_rejects_nonprimitive_context() -> None:
    with pytest.raises(ValueError, match="context"):
        _boolean_expression("True", {"event": 1})  # type: ignore[dict-item]


@pytest.mark.parametrize("expression,expected,reads", [
    ("True or flag", True, 1),
    ("False and flag", False, 1),
    ("flag or True", True, 2),
])
def test_condition_evaluator_short_circuits_after_full_validation(
    expression: str, expected: bool, reads: int,
) -> None:
    class Context(dict[str, str | bool]):
        lookups = 0

        def __getitem__(self, key: str) -> str | bool:
            self.lookups += 1
            return super().__getitem__(key)

    context = Context(flag=False)
    assert _boolean_expression(expression, context) is expected
    assert context.lookups == reads


def _ci_condition(
    condition: str, *, event: str, cancelled: bool = False,
    failed: bool = False, standalone: str = "true", base: str = "dev",
    promotion_run: str = "123", action: str = "",
) -> bool:
    condition = condition.removeprefix("${{").removesuffix("}}").strip()
    for source, target in [
        ("github.event.pull_request.base.ref", "base"),
        ("needs.discover.outputs.standalone_service", "standalone"),
        ("github.event_name", "event"),
        ("github.event.action", "action"),
        ("inputs.promotion_run", "promotion_run"),
        ("!cancelled()", "not cancelled"),
        ("cancelled()", "cancelled"),
        ("always()", "True"),
        ("success()", "success"),
        ("failure()", "failed"),
        ("&&", "and"),
        ("||", "or"),
    ]:
        condition = condition.replace(source, target)
    return _boolean_expression(condition, {
        "event": event, "cancelled": cancelled, "failed": failed,
        "success": not failed, "standalone": standalone, "base": base,
        "promotion_run": promotion_run, "action": action,
    })


@pytest.mark.parametrize("event,action,expected", [
    ("pull_request", "synchronize", True),
    ("pull_request", "edited", False),
    ("pull_request", "opened", False),
    ("pull_request", "reopened", False),
    ("push", "", False),
    ("workflow_dispatch", "", False),
])
def test_only_superseded_pr_ci_is_cancelled(
    event: str, action: str, expected: bool,
) -> None:
    ci = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    concurrency = ci["concurrency"]
    assert concurrency["group"] == "ci-${{ github.workflow }}-${{ github.ref }}"
    condition = concurrency["cancel-in-progress"]
    assert isinstance(condition, str)
    assert _ci_condition(condition, event=event, action=action) is expected


def test_source_ci_does_not_allocate_retired_standalone_integration_jobs() -> None:
    ci = source_workflow(ROOT, "ci.yml")
    assert set(ci["jobs"]) == {"checks", "unit-tests", "pr-gate"}
    assert set(ci["jobs"]["pr-gate"]["needs"]) == {"checks", "unit-tests"}


@pytest.mark.parametrize("event,cancelled,failed,base,expected", [
    ("pull_request", False, False, "dev", True),
    ("pull_request", False, True, "dev", True),
    ("pull_request", True, False, "dev", False),
    ("pull_request", True, True, "dev", False),
    ("pull_request", False, False, "main", False),
    ("push", False, False, "dev", False),
    ("workflow_dispatch", False, False, "dev", False),
])
def test_pr_aggregate_reports_failures_but_never_waits_after_cancellation(
    event: str, cancelled: bool, failed: bool, base: str, expected: bool,
) -> None:
    ci = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    assert _ci_condition(
        ci["jobs"]["pr-gate"]["if"], event=event,
        cancelled=cancelled, failed=failed, base=base,
    ) is expected


def test_manual_source_ci_keeps_units_without_restoring_candidate_qualification() -> None:
    ci = source_workflow(ROOT, "ci.yml")
    assert ci.get("on", ci.get(True))["workflow_dispatch"] == {}
    assert "promotion-gate" not in ci["jobs"]
    assert _ci_condition(ci["jobs"]["unit-tests"]["if"], event="workflow_dispatch")
    execution = [
        step for step in ci["jobs"]["unit-tests"]["steps"]
        if step.get("id") == "units"
    ]
    assert len(execution) == 1
    assert execution[0]["if"] == "steps.impact.outputs.run == 'true'"
    assert execution[0]["run"] == (
        'python tools/run-promotion-unit-tests.py --source-sha "$GITHUB_SHA" '
        '--output-dir "$RUNNER_TEMP/promotion-unit-evidence"'
    )


def test_main_pr_cutover_keeps_a_read_only_trusted_producer():
    ci = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    host = yaml.safe_load((WORKFLOW.parent / "base-branch-reminder.yml").read_text(encoding="utf-8"))
    events = host.get("on", host.get(True))["pull_request_target"]
    assert events["branches"] == ["main"]
    assert set(events["types"]) == {"opened", "reopened", "edited", "synchronize"}
    ci_events = ci.get("on", ci.get(True))
    assert ci_events["pull_request"] == {
        "branches-ignore": ["main"],
        "types": ["opened", "reopened", "synchronize", "edited"],
    }
    assert ci_events["push"] == {"branches": ["main", "dev"]}
    assert "workflow_dispatch" in ci_events
    assert "main-gate" not in ci["jobs"]
    gate = host["jobs"]["main-gate"]
    assert gate["name"] == "main source gate"
    assert gate["permissions"] == {"contents": "read", "actions": "read"}
    assert gate["timeout-minutes"] == 2
    assert "needs" not in gate
    assert gate["steps"][-1]["env"] == {
        "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
        "PR_AUTHOR": "${{ github.event.pull_request.user.login }}",
        "REPO_OWNER": "${{ github.repository_owner }}",
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
        "PR_COMMITS": "${{ github.event.pull_request.commits }}",
        "HEAD_REPOSITORY": "${{ github.event.pull_request.head.repo.full_name }}",
        "TARGET_REPOSITORY": "${{ github.repository }}",
        "GH_TOKEN": "${{ github.token }}",
    }
    checkout, fetch = gate["steps"][:2]
    assert checkout["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0}
    assert checkout["if"] == "github.event.pull_request.user.login == github.repository_owner"
    assert "github.event.pull_request.user.login == github.repository_owner" in fetch["if"]
    assert "!startsWith(github.event.pull_request.head.ref, 'release/promote-')" in fetch["if"]
    assert fetch["env"] == {"HEAD_SHA": "${{ github.event.pull_request.head.sha }}"}
    assert 'fetch --no-tags --no-recurse-submodules origin "$HEAD_SHA"' in fetch["run"]
    assert "checkout" not in fetch["run"]
    assert "secrets." not in json.dumps(gate)
    assert gate["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert host["jobs"]["remind"]["if"] in (
        "github.event.action != 'synchronize'",
        "github.event.action != 'synchronize' && "
        "github.event.pull_request.user.login != github.repository_owner",
    )


@pytest.mark.parametrize("event,branch,same_repo,conclusion,expected", [
    ("push", "dev", True, "success", True),
    ("push", "dev", True, "failure", True),
    ("push", "dev", True, "cancelled", False),
    ("push", "dev", True, "skipped", False),
    ("push", "dev", True, "neutral", False),
    ("push", "dev", True, "timed_out", False),
    ("push", "main", True, "success", False),
    ("pull_request", "dev", True, "success", False),
    ("workflow_dispatch", "dev", True, "success", False),
    ("push", "dev", False, "success", False),
])
def test_only_committed_same_repo_dev_ci_can_refresh_baseline(
    event, branch, same_repo, conclusion, expected,
):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    condition = workflow["jobs"]["refresh-module-baseline"]["if"]
    for source, target in [
        ("github.event.workflow_run.head_repository.full_name", "head_repo"),
        ("github.event.workflow_run.head_branch", "branch"),
        ("github.event.workflow_run.event", "event"),
        ("github.event.workflow_run.conclusion", "conclusion"),
        ("github.repository", "repo"),
    ]:
        condition = condition.replace(source, target)
    assert _boolean_expression(condition.replace("&&", "and").replace("||", "or"), {
        "event": event, "branch": branch, "head_repo": "o/r" if same_repo else "fork/r",
        "repo": "o/r", "conclusion": conclusion,
    }) is expected


@pytest.mark.parametrize("event,conclusion,expected", [
    ("pull_request", "success", False),
    ("pull_request", "failure", False),
    ("push", "success", True),
    ("push", "failure", False),
    ("push", "cancelled", False),
    ("workflow_dispatch", "success", True),
])
def test_only_committed_or_manual_success_can_enter_the_filter(event, conclusion, expected):
    assert _filter_can_run(event=event, conclusion=conclusion) is expected


@pytest.mark.parametrize("name,event,conclusion,expected", [
    ("Validate and promote dev to main", "workflow_dispatch", "success", True),
    ("Validate and promote dev to main", "workflow_dispatch", "failure", True),
    ("Validate and promote dev to main", "repository_dispatch", "cancelled", True),
    ("Validate and promote dev to main", "pull_request", "success", False),
    ("CI", "push", "cancelled", False),
    ("unrelated", "push", "success", False),
])
def test_promoter_completion_can_reconcile_a_merge_after_failed_confirmation(
    name: str, event: str, conclusion: str, expected: bool,
) -> None:
    assert _filter_can_run(workflow_name=name, event=event, conclusion=conclusion) is expected


def _filter_can_run(*, condition: str | None = None, **changes: str) -> bool:
    if condition is None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        condition = workflow["jobs"]["filter-and-dispatch"]["if"]
    replacements = {
        "github.event.workflow_run.conclusion": "conclusion",
        "github.event.workflow_run.head_branch": "branch",
        "github.event.workflow_run.name": "workflow_name",
        "github.event.workflow_run.event": "event",
        "github.event.pull_request.head.repo.full_name": "head_repo",
        "github.event.pull_request.user.login": "author",
        "github.event.pull_request.base.ref": "base",
        "github.repository_owner": "owner",
        "github.event_name": "trigger",
        "github.event.action": "action",
        "github.repository": "repo",
    }
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        condition = condition.replace(source, target)
    executable = "(" + condition.replace("&&", "and").replace("||", "or") + ")"
    context: dict[str, str | bool] = {
        "trigger": "workflow_run", "event": "", "conclusion": "", "action": "",
        "workflow_name": "CI", "branch": "dev",
        "author": "", "owner": "owner", "base": "", "head_repo": "", "repo": "owner/project",
        **changes,
    }
    return _boolean_expression(executable, context)


@pytest.mark.parametrize("workflow_name,event,branch,conclusion,expected", [
    ("CI", "push", "main", "success", False),
    ("CI", "push", "dev", "success", True),
    ("CI", "push", "unknown", "success", True),
    ("CI", "push", "", "success", True),
    ("CI", "workflow_dispatch", "main", "success", True),
    ("CI", "workflow_dispatch", "promote-20261023062400-12345678", "success", True),
    ("CI", "pull_request", "dev", "success", False),
    ("CI", "push", "dev", "failure", False),
    ("Validate and promote dev to main", "workflow_dispatch", "main", "success", True),
    ("Validate and promote dev to main", "workflow_dispatch", "main", "failure", True),
    ("Validate and promote dev to main", "repository_dispatch", "main", "cancelled", True),
])
def test_source_filter_omits_only_main_push_ci_before_allocation(
    workflow_name, event, branch, conclusion, expected,
):
    condition = source_workflow(ROOT, "promote-trigger.yml")["jobs"]["filter-and-dispatch"]["if"]
    assert _filter_can_run(
        condition=condition, workflow_name=workflow_name, event=event,
        branch=branch, conclusion=conclusion,
    ) is expected


@pytest.mark.parametrize("changes,expected", [
    ({}, True), ({"author": "outside"}, False), ({"head_repo": "outside/fork"}, False),
    ({"base": "dev"}, False), ({"action": "edited"}, False), ({"trigger": "push"}, False),
])
def test_only_closed_same_repository_owner_main_prs_can_wake_deferred_source(
    changes: dict[str, str], expected: bool,
) -> None:
    assert _filter_can_run(
        **{
            "trigger": "pull_request_target", "action": "closed", "base": "main",
            "author": "owner", "head_repo": "owner/project", **changes,
        },
    ) is expected


@pytest.fixture
def filter_fixture(tmp_path):
    git = shutil.which("git")
    assert git, "Git is required for the real ancestry-filter contract"
    bash = shutil.which("bash")
    if os.name == "nt":
        directory = Path(git).resolve().parent
        bash = next((str(p) for p in [
            directory / "bash.exe", directory.parent / "bin" / "bash.exe",
            directory.parent / "usr" / "bin" / "bash.exe",
        ] if p.is_file()), None)
    assert bash, "Bash (Git Bash on Windows) is required for the workflow contract"
    env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    repo = tmp_path / "repo"
    repo.mkdir()

    def git_run(*args):
        return subprocess.run(
            [git, "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *args],
            cwd=repo, env=env, check=True, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    git_run("init", "-q", "-b", "dev")
    git_run("config", "user.name", "Test")
    git_run("config", "user.email", "test@example.com")
    source = repo / "source.txt"
    source.write_text("reviewed\n", encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "reviewed")
    dev_sha = git_run("rev-parse", "HEAD")
    git_run("checkout", "-qb", "feature")
    source.write_text("unreviewed\n", encoding="utf-8")
    git_run("commit", "-qam", "feature")
    feature_sha = git_run("rev-parse", "HEAD")
    git_run("checkout", "-q", "dev")
    remote = tmp_path / "origin.git"
    git_run("clone", "--bare", str(repo), str(remote))
    git_run("remote", "add", "origin", str(remote))
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    mock_gh = mock_bin / "gh"
    mock_gh.write_bytes(
        b'#!/bin/sh\n[ "$*" = "api repos/example/project/dispatches --input -" ] || exit 2\n'
        b'cat > "$DISPATCH_LOG"\n'
    )
    mock_gh.chmod(0o755)
    log = tmp_path / "dispatch.json"
    env.update({
        "PATH": str(mock_bin) + os.pathsep + env["PATH"],
        "DISPATCH_LOG": log.as_posix(),
    })
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    code = workflow["jobs"]["filter-and-dispatch"]["steps"][-1]["run"]
    script = tmp_path / "filter.sh"
    script.write_bytes(code.replace("${{ github.repository }}", "example/project").encode("utf-8"))

    def run(sha):
        return subprocess.run(
            [bash, "-e", script.as_posix()], cwd=repo, env={**env, "SHA": sha},
            check=False, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    return run, log, dev_sha, feature_sha, git_run, tmp_path


def test_reviewed_dev_ancestor_dispatches_exact_sha(filter_fixture):
    run, log, dev_sha, _, _, _ = filter_fixture
    result = run(dev_sha)
    assert result.returncode == 0, result.stderr
    assert json.loads(log.read_text()) == {
        "event_type": "dev-advanced", "client_payload": {"sha": dev_sha},
    }


def test_unreviewed_feature_never_dispatches(filter_fixture):
    run, log, _, feature_sha, _, _ = filter_fixture
    result = run(feature_sha)
    assert result.returncode == 0, result.stderr
    assert not log.exists()


def test_failed_dev_fetch_cannot_dispatch(filter_fixture):
    run, log, dev_sha, _, git_run, tmp_path = filter_fixture
    git_run("remote", "set-url", "origin", str(tmp_path / "missing.git"))
    result = run(dev_sha)
    assert result.returncode != 0
    assert not log.exists()


@pytest.fixture
def baseline_fixture(tmp_path):
    git = shutil.which("git")
    assert git
    bash = shutil.which("bash")
    if os.name == "nt":
        directory = Path(git).resolve().parent
        bash = next((str(p) for p in [
            directory / "bash.exe", directory.parent / "bin/bash.exe",
            directory.parent / "usr/bin/bash.exe",
        ] if p.is_file()), None)
    assert bash
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_"))}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    def git_run(*args):
        return subprocess.run(
            [git, "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *args],
            cwd=candidate, env=env, check=True, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    git_run("init", "-q")
    git_run("config", "user.name", "Fixture")
    git_run("config", "user.email", "fixture@example.test")
    baseline = candidate / "tools/module-size-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text("{}\n", encoding="utf-8")
    (baseline.parent / "promotion_control.py").write_bytes(
        (ROOT / "tools/promotion_control.py").read_bytes(),
    )
    other = candidate / "source.py"
    other.write_text("value = 1\n", encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "initial")
    base = git_run("rev-parse", "HEAD")
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_bytes(b'#!/bin/sh\nprintf "%s\\n" "$LIVE_SHA"\n')
    gh.chmod(0o755)
    env.update({
        "PATH": str(binary) + os.pathsep + env["PATH"], "REPO": "example/project",
        "INPUT_SHA": base, "GITHUB_OUTPUT": (tmp_path / "output").as_posix(),
    })

    def run(code, **values):
        script = tmp_path / "workflow-step.sh"
        script.write_bytes(code.encode("utf-8"))
        return subprocess.run(
            [bash, "-e", "-o", "pipefail", script.as_posix()], cwd=tmp_path,
            env={**env, "LIVE_SHA": base, **values},
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    return run, git_run, candidate, baseline, other, base, tmp_path


@pytest.mark.parametrize("change,live,expected", [
    ("baseline", None, "run"),
    ("none", None, "skip"),
    ("other", None, "fail"),
    ("baseline", "a" * 40, "skip"),
    ("baseline", "invalid", "fail"),
])
def test_actual_baseline_fence_and_diff_restriction(baseline_fixture, change, live, expected):
    run, _, _, baseline, other, base, tmp_path = baseline_fixture
    if change == "baseline":
        baseline.write_text('{"source.py": 1001}\n', encoding="utf-8")
    elif change == "other":
        other.write_text("value = 2\n", encoding="utf-8")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    code = next(
        step["run"] for step in workflow["jobs"]["refresh-module-baseline"]["steps"]
        if step.get("id") == "baseline"
    )
    result = run(code, LIVE_SHA=base if live is None else live)
    assert (result.returncode != 0) is (expected == "fail"), result.stderr
    output = tmp_path / "output"
    if expected != "fail":
        assert output.read_text().strip() == ("changed=true" if expected == "run" else "changed=false")


@pytest.mark.parametrize("branch", [
    "automation/module-size-baseline-widen", "automation/module-size-baseline-widen-dev",
])
def test_real_main_gate_rejects_baseline_automation(baseline_fixture, branch):
    run, git_run, candidate, baseline, _, base, _ = baseline_fixture
    baseline.write_text('{"source.py": 1001}\n', encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "baseline change")
    head = git_run("rev-parse", "HEAD")
    host = yaml.safe_load((WORKFLOW.parent / "base-branch-reminder.yml").read_text(encoding="utf-8"))
    code = host["jobs"]["main-gate"]["steps"][-1]["run"]
    result = run(
        f'cd "{candidate.as_posix()}"\n{code}',
        HEAD_REF=branch, PR_AUTHOR="owner", REPO_OWNER="owner",
        BASE_SHA=base, HEAD_SHA=head, PR_COMMITS="1",
    )
    assert result.returncode != 0
    assert "retarget" in result.stdout.lower()


@pytest.mark.parametrize("shape,expected", [
    ("unowned-promotion", False), ("foreign-promotion", False), ("workflow", True),
    ("mixed", False), ("state", True), ("state-plus-source", False),
])
def test_trusted_main_gate_preserves_admitted_shapes(baseline_fixture, shape, expected):
    run, git_run, candidate, _, other, base, _ = baseline_fixture
    branch = "release/promote-fixture" if "promotion" in shape else "pr/workflow"
    author = "outsider" if shape == "foreign-promotion" else "owner"
    if shape in ("state", "state-plus-source"):
        branch = "release-pipeline/state-fixture"
        path = candidate / ".github" / "release-pipeline-state.json"
    else:
        path = candidate / ".github" / "workflows" / "fixture.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture\n", encoding="utf-8")
    if shape in ("mixed", "state-plus-source"):
        other.write_text("value = 2\n", encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", shape)
    head = git_run("rev-parse", "HEAD")
    host = yaml.safe_load((WORKFLOW.parent / "base-branch-reminder.yml").read_text(encoding="utf-8"))
    result = run(
        f'cd "{candidate.as_posix()}"\n{host["jobs"]["main-gate"]["steps"][-1]["run"]}',
        HEAD_REF=branch, PR_AUTHOR=author, REPO_OWNER="owner",
        BASE_SHA=base, HEAD_SHA=head, PR_COMMITS="1",
        HEAD_REPOSITORY="example/project", TARGET_REPOSITORY="example/project",
    )
    assert (result.returncode == 0) is expected, result.stdout + result.stderr
    if shape == "unowned-promotion":
        # A prefix alone is no longer authority; authenticated positive owning
        # runs and actual control-byte comparisons are exercised by the boundary tests.
        assert "owning run/base" in result.stderr


@pytest.mark.parametrize("valid", [True, False])
def test_trusted_main_gate_validates_actual_rollback_tree(baseline_fixture, valid):
    run, git_run, candidate, _, other, _, _ = baseline_fixture
    other.write_text("value = 2\n", encoding="utf-8")
    git_run("commit", "-qam", "promotion")
    git_run("tag", "promote-fixture")
    state = candidate / ".github" / "release-pipeline-state.json"
    state.parent.mkdir()
    state.write_text('{"paused":true}\n', encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "pause")
    base = git_run("rev-parse", "HEAD")
    other.write_text("value = 1\n" if valid else "value = 3\n", encoding="utf-8")
    git_run("commit", "-qam", "revert")
    head = git_run("rev-parse", "HEAD")
    host = yaml.safe_load((WORKFLOW.parent / "base-branch-reminder.yml").read_text(encoding="utf-8"))
    result = run(
        f'cd "{candidate.as_posix()}"\n{host["jobs"]["main-gate"]["steps"][-1]["run"]}',
        HEAD_REF="release-pipeline/rollback-fixture", PR_AUTHOR="owner", REPO_OWNER="owner",
        BASE_SHA=base, HEAD_SHA=head, PR_COMMITS="1",
    )
    assert (result.returncode == 0) is valid, result.stdout + result.stderr


@pytest.mark.parametrize("available", [True, False])
def test_real_trusted_fetch_keeps_main_checkout_and_fails_loudly(baseline_fixture, available):
    run, git_run, candidate, _, other, base, tmp_path = baseline_fixture
    git_run("branch", "trusted", base)
    other.write_text("untrusted head data\n", encoding="utf-8")
    git_run("commit", "-qam", "PR data")
    head = git_run("rev-parse", "HEAD")
    remote, trusted = tmp_path / "origin.git", tmp_path / "trusted"
    git_run("clone", "--bare", str(candidate), str(remote))
    git_run("clone", "--no-local", "--single-branch", "--branch", "trusted", str(remote), str(trusted))
    host = yaml.safe_load((WORKFLOW.parent / "base-branch-reminder.yml").read_text(encoding="utf-8"))
    fetch = host["jobs"]["main-gate"]["steps"][1]["run"]
    source = f'cd "{trusted.as_posix()}"\n'
    if not available:
        source += f'git remote set-url origin "{(tmp_path / "missing.git").as_posix()}"\n'
    source += (
        'if git cat-file -e "$HEAD_SHA" 2>/dev/null; then exit 9; fi\n'
        f'{fetch}\n'
        'test "$(git rev-parse HEAD)" = "$BASE_SHA"\n'
        'test "$(cat source.py)" = "value = 1"\n'
        'git cat-file -e "$HEAD_SHA"\n'
    )
    result = run(source, HEAD_SHA=head, BASE_SHA=base)
    assert (result.returncode == 0) is available, result.stdout + result.stderr
    assert (trusted / "source.py").read_text(encoding="utf-8") == "value = 1\n"
