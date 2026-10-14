"""Exercise the consolidated trusted workflow without network access."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "workflow-lockdown-guard.yml"


@pytest.fixture
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def execute_script(
    script, *, files=(), author="outsider", collaborator=True, roster="owner",
    request_error=False,
):
    payload = json.dumps({
        "script": script, "files": list(files), "author": author,
        "collaborator": collaborator, "roster": roster, "request_error": request_error,
    })
    harness = """
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const result = { failures: [], warnings: [], outputs: {} };
const core = {
  info() {},
  warning(message) { result.warnings.push(message); },
  setFailed(message) { result.failures.push(message); process.exitCode = 1; },
  setOutput(key, value) { result.outputs[key] = value; },
};
const github = {
  paginate: async method => method === github.rest.pulls.listFiles ? input.files : [],
  rest: {
    issues: { listEventsForTimeline() {} },
    pulls: {
      listFiles() {}, listReviews() {},
      async get() {
        return { data: { state: "open", draft: false, base: { ref: "dev" }, head: { sha: "a".repeat(40) } } };
      },
      async listRequestedReviewers() { return { data: { users: [] } }; },
      async requestReviewers() {
        if (input.request_error) {
          throw Object.assign(new Error("rate limited"), {
            response: { headers: { "retry-after": "60" } },
          });
        }
        result.requested = true;
      },
    },
    repos: {
      async checkCollaborator() {
        if (!input.collaborator) throw Object.assign(new Error("not found"), {status: 404});
      },
      async getContent() {
        return { data: { content: Buffer.from("* @" + input.roster).toString("base64") } };
      },
    },
  },
};
github.constructor = function() { return github; };
global.setTimeout = resolve => { resolve(); return 0; };
const context = {
  repo: { owner: "owner", repo: "project" },
  payload: { pull_request: {
    number: 1, user: { login: input.author }, base: { ref: "dev" }, head: { sha: "a".repeat(40) },
    updated_at: new Date(Date.now() - 300_000).toISOString(),
  } },
};
process.env.ALLOWED_LOGIN = "owner";
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction("github", "core", "context", input.script)(github, core, context)
  .then(() => process.stdout.write(JSON.stringify(result)))
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
    completed = subprocess.run(
        ["node", "-e", harness], input=payload, check=False, capture_output=True,
        text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == int(bool(result["failures"])), completed.stderr
    return result


def test_one_trusted_entry_point_preserves_both_checks(workflow):
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"pull_request_target", "push", "workflow_dispatch"}
    assert workflow["name"] == "Workflow lockdown guard"
    assert triggers["pull_request_target"]["branches"] == ["dev"]
    assert set(triggers["pull_request_target"]["types"]) == {
        "opened", "reopened", "synchronize", "edited", "ready_for_review", "labeled", "unlabeled",
    }
    assert not (WORKFLOW.parent / "copilot-review-gate.yml").exists()
    assert "concurrency" not in workflow
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    guard = workflow["jobs"]["workflow-lockdown-guard"]
    assert guard["name"] == "workflow-lockdown-guard"
    assert "needs" not in guard
    assert guard["if"] == "github.event_name == 'pull_request_target'"
    assert all("uses" not in step or "checkout" not in step["uses"] for step in guard["steps"])
    review = workflow["jobs"]["request-review"]
    assert review["name"] == "request-review-if-maintainer"
    assert review["concurrency"]["cancel-in-progress"] is False
    assert "github.event.pull_request.number" in review["concurrency"]["group"]
    assert "github.event.changes.base" in review["if"]
    assert review["permissions"] == {
        "contents": "read", "issues": "read", "pull-requests": "read",
    }
    assert all("uses" not in step or "checkout" not in step["uses"] for step in review["steps"])
    assert review["steps"][1]["if"] == "steps.status.outputs.is_maintainer == 'true'"
    assert review["steps"][1]["with"]["github-token"] == "${{ secrets.MAINTAINER_REVIEW_PAT }}"


@pytest.mark.parametrize("file", [
    {"filename": ".github/workflows/ci.yml"},
    {"filename": ".github/actions/check/action.yml"},
    {"filename": ".github/CODEOWNERS"},
    {"filename": "moved.yml", "previous_filename": ".github/workflows/ci.yml"},
])
def test_outsider_cannot_modify_or_rename_protected_content(workflow, file):
    script = workflow["jobs"]["workflow-lockdown-guard"]["steps"][0]["with"]["script"]
    assert execute_script(script, files=[file])["failures"]
    assert not execute_script(script, files=[file], author="owner")["failures"]


def test_ordinary_contribution_and_file_cap_preserve_admission(workflow):
    script = workflow["jobs"]["workflow-lockdown-guard"]["steps"][0]["with"]["script"]
    assert not execute_script(script, files=[{"filename": "src/feature.py"}])["failures"]
    files = [{"filename": f"src/{i}.py"} for i in range(3000)]
    assert execute_script(script, files=files, author="owner")["failures"]


@pytest.mark.parametrize("author,collaborator,expected", [
    ("owner", True, "true"), ("outsider", False, "false"),
    ("contributor", True, "false"),
])
def test_review_billing_stays_limited_to_invited_maintainers(
    workflow, author, collaborator, expected,
):
    script = workflow["jobs"]["request-review"]["steps"][0]["with"]["script"]
    result = execute_script(script, author=author, collaborator=collaborator)
    assert result["outputs"]["is_maintainer"] == expected


def test_review_request_failure_reports_backoff_and_fails_the_step(workflow):
    script = workflow["jobs"]["request-review"]["steps"][1]["with"]["script"]
    result = execute_script(script, request_error=True)
    assert result["failures"] == ["Could not request Copilot review: rate limited"]
    assert len(result["warnings"]) == 1
    assert "retry-after=60" in result["warnings"][0]
    assert "requested" not in result


def test_review_request_success_uses_the_same_embedded_script(workflow):
    script = workflow["jobs"]["request-review"]["steps"][1]["with"]["script"]
    result = execute_script(script)
    assert not result["failures"]
    assert result["requested"] is True
