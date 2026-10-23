"""Validate the repository's self-hosted pull-request trust boundary."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "trusted-ci.yml"
REVIEW_WORKFLOW = "copilot-code-review.yml"
REVIEW_JOB = "copilot-setup-steps"
REVIEW_RUNNERS = ("ubuntu-latest", "copilot-extensions-review")
ACTIVATION_CONDITION = "vars.TRUSTED_SELF_HOSTED_CI == 'enabled'"
TRUSTED_JOB_CONDITION = (
    "needs.authorize.outputs.trusted == 'true' && "
    "vars.TRUSTED_SELF_HOSTED_CI == 'enabled'"
)
UNIT_CONDITION = "steps.impact.outputs.run == 'true'"
UNIT_JOB_CONDITION = "github.event_name != 'push'"
SOURCE_AST_CONDITION = "steps.source-ast-impact.outputs.run == 'true'"
SOURCE_AST_COMMANDS = ("python tools/run-source-ast-guards.py",)
SOURCE_AST_SCRIPT = """python - <<'PY'
import json
import os
import subprocess
from pathlib import Path
import sys
sys.path.insert(0, str(Path("tools").resolve()))
from promotion_unit_contract import ci_requires_source_ast_guards
needed = ci_requires_source_ast_guards(
    os.environ["EVENT"], os.environ["BASE"], os.environ["HEAD"],
    subprocess.check_output,
)
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write("run=" + str(needed).lower() + "\\n")
print(json.dumps({"run_source_ast_guards": needed}))
PY"""
UNIT_GATE_SCRIPT = """python - <<'PY'
import json
import os
rows = json.loads(os.environ["RESULTS"])
if set(rows) != {"checks", "unit-tests"} or any(row["result"] != "success" for row in rows.values()):
    raise RuntimeError("source guard or unit-impact execution failed")
outputs = rows["unit-tests"]["outputs"]
selected = outputs.get("run")
if selected not in ("true", "false"):
    raise RuntimeError("unit selection is missing or invalid")
expected = "success" if selected == "true" else "skipped"
if outputs.get("result") != expected:
    raise RuntimeError("selected units did not pass, or unselected work was executed")
print("Required unit-only PR gate satisfied.")
PY"""
PERMISSION_SCRIPT = """
const sameRepository =
context.payload.pull_request.head.repo.full_name ===
context.payload.repository.full_name;
if (!sameRepository) {
core.setOutput("trusted", false);
return;
}
async function hasWriteAccess(username) {
let data;
try {
({ data } = await github.rest.repos.getCollaboratorPermissionLevel({
owner: context.repo.owner,
repo: context.repo.repo,
username,
}));
} catch (error) {
if (error.status === 404) {
return false;
}
throw error;
}
const permissions = data.user?.permissions ?? {};
return permissions.admin === true ||
permissions.maintain === true ||
permissions.push === true;
}
try {
const authorTrusted = await hasWriteAccess(
context.payload.pull_request.user.login,
);
const senderTrusted = await hasWriteAccess(
context.payload.sender.login,
);
core.setOutput("trusted", authorTrusted && senderTrusted);
} catch (error) {
core.setOutput("trusted", false);
core.setFailed(
`Unable to resolve current repository permission: ${error.status ?? "unknown"}`,
);
}
"""
GITHUB_SCRIPT_SHA = "f28e40c7f34bde8b3046d885e986cb6290c5673b"
SETUP_PYTHON_SHA = "a26af69be951a213d495a4c3e4e4022e16d87065"
SETUP_UV_SHA = "c771a70e6277c0a99b617c7a806ffedaca235ff9"
# `ubuntu-slim`: a real, standard GitHub-hosted single-CPU runner label,
# documented in GitHub's own "GitHub-hosted runners reference" table
# (docs.github.com/en/actions/reference/runners/github-hosted-runners) right
# alongside `ubuntu-latest` -- NOT a self-hosted or custom-runner-group name.
# Added when `gh-aw`-compiled workflows' generated infra jobs
# (`activation`/`conclusion`/`pre_activation`/`safe_outputs`) defaulted to it
# and this allowlist (which predates this repo's `gh-aw` adoption) rejected
# them as an "unauthorized self-hosted runner route" -- a false positive this
# guard's own allowlist just hadn't caught up to yet, not a real
# self-hosted-routing risk.
GITHUB_HOSTED_LABELS = {
    "ubuntu-latest",
    "ubuntu-slim",
    "ubuntu-24.04",
    "ubuntu-22.04",
    "windows-latest",
    "windows-2025",
    "windows-2022",
    "macos-latest",
    "macos-15",
    "macos-14",
    "macos-13",
}
CHECKOUT_RUN = r"""
find "$GITHUB_WORKSPACE" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
git init "$GITHUB_WORKSPACE"
git -C "$GITHUB_WORKSPACE" remote add origin \
"https://github.com/${{ github.repository }}.git"
git -C "$GITHUB_WORKSPACE" fetch --depth=1 origin "$PR_SHA"
git -C "$GITHUB_WORKSPACE" checkout --detach FETCH_HEAD
"""


def _normalized(value: object) -> str:
    return " ".join(str(value).split())


def _mapping(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _canonical(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, str):
        return _normalized(value)
    return value


def _load(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    except (OSError, yaml.YAMLError) as exc:
        return None, [f"cannot load {path}: {exc}"]
    if not isinstance(value, dict):
        return None, [f"{path} must contain a workflow mapping"]
    return value, []


def _runs_on_labels(value: object) -> set[str] | None:
    if isinstance(value, str):
        if "${{" in value:
            return None
        return {value.casefold()}
    if isinstance(value, list):
        labels = {str(item).casefold() for item in value}
        return None if any("${{" in label for label in labels) else labels
    if isinstance(value, dict):
        return None
    return None


def _is_github_hosted(labels: set[str]) -> bool:
    return len(labels) == 1 and next(iter(labels)) in GITHUB_HOSTED_LABELS


def _validate_review_setup(workflow: dict[str, Any]) -> list[str]:
    jobs = _mapping(workflow.get("jobs")) or {}
    job = _mapping(jobs.get(REVIEW_JOB)) or {}
    runner = job.get("runs-on")
    expected = {
        "name": "Copilot Code Review setup",
        "on": {"workflow_dispatch": ""},
        "permissions": {},
        "jobs": {
            REVIEW_JOB: {
                "runs-on": runner,
                "permissions": {},
                "steps": [{
                    "name": "Complete minimal review setup",
                    "timeout-minutes": "5",
                    "run": 'echo "Copilot Code Review setup complete."',
                }],
            },
        },
    }
    if runner not in REVIEW_RUNNERS or workflow != expected:
        return [f"{REVIEW_WORKFLOW}: setup does not match the reviewed contract"]
    return []


def _validate_no_other_runner_routes(trusted_path: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(trusted_path.parent.glob("*.y*ml")):
        workflow, load_errors = _load(path)
        errors.extend(load_errors)
        if workflow is None:
            continue
        review_setup = path.name == REVIEW_WORKFLOW
        review_errors = _validate_review_setup(workflow) if review_setup else []
        errors.extend(review_errors)
        jobs = _mapping(workflow.get("jobs"))
        if jobs is None:
            errors.append(f"{path.name}: jobs must be a mapping")
            continue
        for name, raw_job in jobs.items():
            job = _mapping(raw_job)
            if job is None:
                errors.append(f"{path.name}:{name}: job must be a mapping")
                continue
            allowed = (
                path == trusted_path and name == "agent-bridge"
            ) or (review_setup and not review_errors and name == REVIEW_JOB)
            if "uses" in job and not allowed:
                errors.append(f"{path.name}:{name}: reusable workflow route is forbidden")
            labels = _runs_on_labels(job.get("runs-on"))
            if labels is None:
                errors.append(f"{path.name}:{name}: dynamic runner route is forbidden")
                continue
            if not allowed and not _is_github_hosted(labels):
                errors.append(
                    f"{path.name}:{name}: unauthorized self-hosted runner route"
                )
    return errors


def validate_workflow(path: Path = WORKFLOW) -> list[str]:
    workflow, errors = _load(path)
    if workflow is None:
        return errors
    ci_workflow, ci_errors = _load(path.parent / "ci.yml")
    errors.extend(ci_errors)
    ci_env = _mapping(ci_workflow.get("env")) if ci_workflow else None
    if ci_env and ci_env.get("PROMOTION_UNIT_PROFILE") == "copilot-extensions.promotion-units/v1":
        retired = {
            "name": "Trusted CI periodic routing",
            "on": {"workflow_dispatch": ""},
            "permissions": {},
            "jobs": {
                "periodic-route": {
                    "runs-on": "ubuntu-latest",
                    "permissions": {},
                    "steps": [{
                        "name": "Identify the containerized integration destination",
                        "run": 'echo "Integration runs in windows-coverage-rotation.yml; PR CI admits units only."',
                    }],
                },
            },
        }
        if workflow != retired:
            errors.append("unit-only CI must retire the self-hosted PR integration route")
        errors.extend(_validate_no_other_runner_routes(path))
        errors.extend(_validate_ci(path))
        return errors
    if set(workflow) != {"name", "on", "concurrency", "permissions", "jobs"}:
        errors.append("workflow top-level keys do not match the reviewed contract")
    if workflow.get("name") != "Trusted self-hosted CI":
        errors.append("workflow name does not match the reviewed contract")
    expected_concurrency = {
        "group": "trusted-ci-${{ github.event.pull_request.number }}",
        "cancel-in-progress": "true",
    }
    if _canonical(workflow.get("concurrency")) != _canonical(expected_concurrency):
        errors.append("workflow concurrency does not match the reviewed contract")

    events = _mapping(workflow.get("on"))
    expected_events = {
        "pull_request_target": {
            "types": ["opened", "reopened", "synchronize", "ready_for_review"],
            "paths": [
                ".github/workflows/trusted-ci.yml",
                "libs/**",
                "plugins/agent-bridge/**",
                "tools/**",
            ],
        }
    }
    if _canonical(events) != _canonical(expected_events):
        errors.append("workflow trigger does not match the reviewed pull_request_target contract")

    permissions = _mapping(workflow.get("permissions"))
    if permissions != {"contents": "read", "pull-requests": "read"}:
        errors.append("top-level permissions must be contents/read + pull-requests/read")

    jobs = _mapping(workflow.get("jobs"))
    if jobs is None:
        return errors + ["jobs must be a mapping"]
    if set(jobs) != {"authorize", "agent-bridge"}:
        errors.append("workflow must contain exactly authorize and agent-bridge jobs")

    authorize = _mapping(jobs.get("authorize"))
    if authorize is None:
        errors.append("authorization job is missing")
    else:
        expected_authorize = {
            "name": "authorize current collaborator",
            "if": ACTIVATION_CONDITION,
            "runs-on": "ubuntu-latest",
            "outputs": {"trusted": "${{ steps.permission.outputs.trusted }}"},
            "steps": [
                {
                    "id": "permission",
                    "name": "Check current repository permission",
                    "uses": f"actions/github-script@{GITHUB_SCRIPT_SHA}",
                    "with": {"script": PERMISSION_SCRIPT},
                }
            ],
        }
        if _canonical(authorize) != _canonical(expected_authorize):
            errors.append("authorization job does not match the reviewed contract")

    trusted_job = _mapping(jobs.get("agent-bridge"))
    if trusted_job is None:
        errors.append("self-hosted job is missing")
    else:
        expected_trusted_job = {
            "name": "agent-bridge trusted suite",
            "needs": "authorize",
            "if": TRUSTED_JOB_CONDITION,
            "runs-on": "copilot-extensions-ci",
            "timeout-minutes": "15",
            "permissions": {},
            "steps": [
                {
                    "name": "Check out immutable pull-request revision without credentials",
                    "env": {"PR_SHA": "${{ github.event.pull_request.head.sha }}"},
                    "run": CHECKOUT_RUN,
                },
                {
                    "uses": f"actions/setup-python@{SETUP_PYTHON_SHA}",
                    "with": {"python-version": "3.12"},
                },
                {"uses": f"astral-sh/setup-uv@{SETUP_UV_SHA}"},
                {
                    "name": "Run agent-bridge suite",
                    "run": "python tools/run-plugin-tests.py agent-bridge",
                },
            ],
        }
        if _canonical(trusted_job) != _canonical(expected_trusted_job):
            errors.append("self-hosted job does not match the reviewed contract")

    text = path.read_text(encoding="utf-8")
    compact_text = re.sub(r"\s+", "", text).casefold()
    if (
        "secrets" in compact_text
        or "github.token" in compact_text
        or "github['token']" in compact_text
        or 'github["token"]' in compact_text
    ):
        errors.append("self-hosted workflow must not expose repository secrets or token")
    if "github.event.pull_request.head.ref" in compact_text or "github.head_ref" in compact_text:
        errors.append("mutable pull-request head refs are forbidden")
    for match in re.finditer(r"(?m)^\s*-?\s*uses:\s*[^@\s]+@([^\s#]+)", text):
        if not re.fullmatch(r"[0-9a-f]{40}", match.group(1)):
            errors.append("all actions must be pinned to full commit SHAs")
    errors.extend(_validate_no_other_runner_routes(path))
    errors.extend(_validate_ci(path))
    return errors


def _validate_ci(path: Path) -> list[str]:
    errors: list[str] = []
    ci_path = path.parent / "ci.yml"
    ci_workflow, ci_errors = _load(ci_path)
    errors.extend(ci_errors)
    if ci_workflow is not None:
        ci_events = _mapping(ci_workflow.get("on"))
        if (
            ci_events is None
            or ci_events.get("pull_request") != {
                "branches-ignore": ["main"],
                "types": ["opened", "reopened", "synchronize", "edited"],
            }
        ):
            errors.append("ci.yml must exclude only main-base pull requests and include retarget edits")
        ci_jobs = _mapping(ci_workflow.get("jobs"))
        unit_profile = _mapping(ci_workflow.get("env"))
        unit_profile = (
            unit_profile is not None
            and unit_profile.get("PROMOTION_UNIT_PROFILE") == "copilot-extensions.promotion-units/v1"
        )
        expected_guard = "python tools/check-trusted-ci.py"
        if not unit_profile:
            expected_guard += " && python -m pytest -q tools/test_check_trusted_ci.py"
        guard_found = False
        if ci_jobs is not None:
            checks = _mapping(ci_jobs.get("checks"))
            if checks is None:
                errors.append("ci.yml checks job is missing")
            elif any(
                key in checks for key in ("if", "needs", "continue-on-error")
            ):
                errors.append("ci.yml checks job must be ungated and dependency-free")
            elif isinstance(checks.get("steps"), list):
                for raw_step in checks["steps"]:
                    step = _mapping(raw_step)
                    if step is None:
                        continue
                    if (
                        step.get("name") == "Trusted self-hosted CI contract"
                        and _normalized(step.get("run"))
                        == expected_guard
                        and "if" not in step
                        and "continue-on-error" not in step
                    ):
                        guard_found = True
        if not guard_found:
            errors.append(
                "ci.yml must invoke the ungated trusted CI guard"
                + ("" if unit_profile else " and tests")
            )
        if unit_profile and ci_jobs is not None:
            if set(ci_jobs) != {"checks", "unit-tests", "pr-gate"}:
                errors.append("unit-only CI must contain only guards, combined units and aggregate")
            check_steps = (_mapping(ci_jobs.get("checks")) or {}).get("steps", [])
            ast_impact = [
                step for step in check_steps if isinstance(step, dict)
                and step.get("id") == "source-ast-impact"
            ]
            if len(ast_impact) != 1 or (
                str(ast_impact[0].get("run", "")).strip() != SOURCE_AST_SCRIPT
                or ast_impact[0].get("timeout-minutes") != "2"
                or ast_impact[0].get("env") != {
                    "EVENT": "${{ github.event_name }}",
                    "BASE": "${{ github.event.pull_request.base.sha }}",
                    "HEAD": "${{ github.event.pull_request.head.sha }}",
                }
                or any(key in ast_impact[0] for key in ("if", "continue-on-error"))
            ):
                errors.append("source AST guards require conservative pinned-input admission")
            for command in SOURCE_AST_COMMANDS:
                scans = [
                    step for step in check_steps if isinstance(step, dict)
                    and step.get("run") == command
                ]
                if len(scans) != 1 or len(ast_impact) != 1 or (
                    scans[0].get("if") != SOURCE_AST_CONDITION
                    or scans[0].get("timeout-minutes") != "6"
                    or "continue-on-error" in scans[0]
                    or check_steps.index(ast_impact[0]) >= check_steps.index(scans[0])
                ):
                    errors.append("source AST guards must follow validated admission without weakening scans")
            inventory = [
                step for step in (_mapping(ci_jobs.get("checks")) or {}).get("steps", [])
                if isinstance(step, dict)
                and step.get("name") == "Marketplace isolation inventory (report-only)"
            ]
            if len(inventory) != 1 or (
                inventory[0].get("run") != "python tools/check-marketplace-isolation.py"
                or inventory[0].get("if") != "github.event_name != 'pull_request'"
                or "continue-on-error" in inventory[0]
            ):
                errors.append("report-only inventory must remain on non-PR CI without becoming a strict PR gate")
            units = _mapping(ci_jobs.get("unit-tests"))
            if (
                units is None or "needs" in units
                or units.get("if") != UNIT_JOB_CONDITION
                or units.get("runs-on") != "ubuntu-latest"
                or units.get("timeout-minutes") != "5"
                or "continue-on-error" in units
                or units.get("outputs") != {
                    "run": "${{ steps.impact.outputs.run }}",
                    "result": "${{ steps.units.outcome }}",
                }
            ):
                errors.append("unit packet must have the reviewed independent hosted five-minute job")
            unit_steps = units.get("steps", []) if units else []
            impact = [
                step for step in unit_steps if isinstance(step, dict)
                and step.get("id") == "impact"
                and not any(key in step for key in ("if", "continue-on-error"))
                and isinstance(step.get("run"), str)
                and "ci_requires_units(" in step.get("run", "")
            ]
            if len(impact) != 1:
                errors.append("combined units must resolve impact before conditional execution")
            runner = [
                step for step in unit_steps if isinstance(step, dict)
                and step.get("id") == "units"
                and step.get("run") == (
                    'python tools/run-promotion-unit-tests.py --source-sha "$GITHUB_SHA" '
                    '--output-dir "$RUNNER_TEMP/promotion-unit-evidence"'
                )
                and step.get("if") == UNIT_CONDITION
                and "continue-on-error" not in step
            ]
            if (
                len(runner) != 1 or len(impact) != 1
                or unit_steps.index(impact[0]) >= unit_steps.index(runner[0])
            ):
                errors.append("unit-only CI must execute the exact admitted packet once")
            gate = _mapping(ci_jobs.get("pr-gate")) or {}
            gate_steps = gate.get("steps")
            gate_step = (
                _mapping(gate_steps[0])
                if isinstance(gate_steps, list) and len(gate_steps) == 1 else None
            )
            if (
                set(gate.get("needs", [])) != {"checks", "unit-tests"}
                or gate_step is None or set(gate_step) != {"name", "env", "run"}
                or gate_step.get("name") != "Require actual admitted unit evidence when selected"
                or gate_step.get("env") != {"RESULTS": "${{ toJson(needs) }}"}
                or str(gate_step.get("run", "")).strip() != UNIT_GATE_SCRIPT
                or "continue-on-error" in gate
            ):
                errors.append("unit aggregate must require successful guards and the actual selected outcome")
            for job in ci_jobs.values():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps", []):
                    command = step.get("run", "") if isinstance(step, dict) else ""
                    if re.search(r"(?:-m\s+pytest|node\s+--test|run-plugin-tests|run-standalone-tests)", command):
                        errors.append("unit-only CI must not invoke a generic or integration test runner")
    return errors


def main() -> int:
    errors = validate_workflow()
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("Trusted CI workflow contract is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
