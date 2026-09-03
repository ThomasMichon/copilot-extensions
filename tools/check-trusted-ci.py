"""Validate the repository's self-hosted pull-request trust boundary."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "trusted-ci.yml"
ACTIVATION_CONDITION = "vars.TRUSTED_SELF_HOSTED_CI == 'enabled'"
TRUSTED_JOB_CONDITION = (
    "needs.authorize.outputs.trusted == 'true' && "
    "vars.TRUSTED_SELF_HOSTED_CI == 'enabled'"
)
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
GITHUB_HOSTED_LABELS = {
    "ubuntu-latest",
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


def _validate_no_other_runner_routes(trusted_path: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(trusted_path.parent.glob("*.y*ml")):
        workflow, load_errors = _load(path)
        errors.extend(load_errors)
        if workflow is None:
            continue
        jobs = _mapping(workflow.get("jobs"))
        if jobs is None:
            errors.append(f"{path.name}: jobs must be a mapping")
            continue
        for name, raw_job in jobs.items():
            job = _mapping(raw_job)
            if job is None:
                errors.append(f"{path.name}:{name}: job must be a mapping")
                continue
            allowed = path == trusted_path and name == "agent-bridge"
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
    ci_path = path.parent / "ci.yml"
    ci_workflow, ci_errors = _load(ci_path)
    errors.extend(ci_errors)
    if ci_workflow is not None:
        ci_events = _mapping(ci_workflow.get("on"))
        if (
            ci_events is None
            or "pull_request" not in ci_events
            or ci_events["pull_request"] not in ("", None)
        ):
            errors.append("ci.yml must run on every pull_request without filters")
        ci_jobs = _mapping(ci_workflow.get("jobs"))
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
                        == (
                            "python tools/check-trusted-ci.py && "
                            "python -m pytest -q tools/test_check_trusted_ci.py"
                        )
                        and "if" not in step
                        and "continue-on-error" not in step
                    ):
                        guard_found = True
        if not guard_found:
            errors.append("ci.yml must invoke the ungated trusted CI guard and tests")
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
