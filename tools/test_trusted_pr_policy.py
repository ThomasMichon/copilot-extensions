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


def execute_script(script, *, files=(), author="outsider", collaborator=True, roster="owner"):
    payload = json.dumps({
        "script": script, "files": list(files), "author": author,
        "collaborator": collaborator, "roster": roster,
    })
    harness = """
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const result = { failures: [], outputs: {} };
const core = {
  info() {},
  setFailed(message) { result.failures.push(message); },
  setOutput(key, value) { result.outputs[key] = value; },
};
const github = {
  paginate: async () => input.files,
  rest: {
    pulls: { listFiles() {} },
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
const context = {
  repo: { owner: "owner", repo: "project" },
  payload: { pull_request: { number: 1, user: { login: input.author }, base: { ref: "dev" } } },
};
process.env.ALLOWED_LOGIN = "owner";
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction("github", "core", "context", input.script)(github, core, context)
  .then(() => process.stdout.write(JSON.stringify(result)))
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
    completed = subprocess.run(
        ["node", "-e", harness], input=payload, check=True, capture_output=True,
        text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(completed.stdout)


def test_one_trusted_entry_point_preserves_both_checks(workflow):
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"pull_request_target"}
    assert triggers["pull_request_target"]["branches"] == ["dev"]
    assert set(triggers["pull_request_target"]["types"]) == {
        "opened", "reopened", "synchronize", "edited", "ready_for_review",
    }
    assert not (WORKFLOW.parent / "copilot-review-gate.yml").exists()
    assert "concurrency" not in workflow
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    guard = workflow["jobs"]["workflow-lockdown-guard"]
    assert guard["name"] == "workflow-lockdown-guard"
    assert "needs" not in guard and "if" not in guard
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
