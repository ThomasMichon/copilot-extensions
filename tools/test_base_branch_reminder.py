"""Keep owner no-op admission separate from preserved Main reminder behavior."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from _promotion_workflow_test_support import source_workflow
from test_post_ci_policy import _boolean_expression

ROOT = Path(__file__).resolve().parents[1]


def reminder():
    return source_workflow(ROOT, "base-branch-reminder.yml")["jobs"]["remind"]


@pytest.mark.parametrize("action,author,owner,expected", [
    ("opened", "owner", "owner", False),
    ("reopened", "owner", "owner", False),
    ("edited", "owner", "owner", False),
    ("synchronize", "owner", "owner", False),
    ("opened", "contributor", "owner", True),
    ("reopened", "contributor", "owner", True),
    ("edited", "contributor", "owner", True),
    ("synchronize", "contributor", "owner", False),
    ("opened", "OwNeR", "owner", False),
    ("opened", "owner-bot", "owner", True),
    ("opened", "", "owner", True),
    ("opened", "contributor", "", True),
    ("opened", "", "", False),
])
def test_source_omits_only_synchronize_and_owner_reminders(action, author, owner, expected):
    job = reminder()
    assert job["if"] == (
        "github.event.action != 'synchronize' && "
        "github.event.pull_request.user.login != github.repository_owner"
    )
    expression = job["if"]
    for source, target in (
        ("github.event.pull_request.user.login", "author"),
        ("github.repository_owner", "owner"),
        ("github.event.action", "action"),
        ("&&", "and"),
    ):
        expression = expression.replace(source, target)
    # Actions compares strings without case; absent properties resolve to "".
    assert _boolean_expression(expression, {
        "action": action.lower(), "author": author.lower(), "owner": owner.lower(),
    }) is expected
    assert job["timeout-minutes"] == 5
    assert "needs" not in job
    assert len(job["steps"]) == 1
    assert job["steps"][0]["uses"].startswith("actions/github-script@")


def test_actual_reminder_script_preserves_noops_freshness_deduplication_and_errors():
    node = shutil.which("node")
    assert node, "Node is required for the actual reminder JavaScript consumer"
    script = reminder()["steps"][0]["with"]["script"]
    driver = r"""
import assert from "node:assert/strict";
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction("github", "context", "core", "process", SCRIPT);
const marker = "<!-- base-branch-reminder -->";
const cases = [
  {name: "owner promotion", author: "owner", branch: "release/promote-123", calls: []},
  {name: "owner workflow", author: "owner", files: [".github/workflows/ci.yml"], calls: ["files"]},
  {name: "owner ordinary", author: "owner", calls: ["files"]},
  {name: "owner state", author: "owner", branch: "release-pipeline/state-123",
   files: [".github/release-pipeline-state.json"], calls: ["files"]},
  {name: "owner rollback", author: "owner", branch: "release-pipeline/rollback-123", calls: ["files"]},
  {name: "outsider promotion prefix", branch: "release/promote-123", calls: ["current", "comments", "post"]},
  {name: "ordinary", calls: ["current", "comments", "post"]},
  {name: "closed", state: "closed", calls: ["current"]},
  {name: "retargeted", base: "dev", calls: ["current"]},
  {name: "trusted existing", comments: [{user: {login: "github-actions[bot]"}, body: marker}],
   calls: ["current", "comments"]},
  {name: "spoofed marker", comments: [{user: {login: "outsider"}, body: marker}],
   calls: ["current", "comments", "post"]},
  {name: "unrelated bot comment", comments: [{user: {login: "github-actions[bot]"}, body: "other"}],
   calls: ["current", "comments", "post"]},
  {name: "provider failure", error: true, calls: ["current"]},
];
for (const c of cases) {
  const calls = [];
  const rest = {pulls: {
    listFiles: "files",
    get: async () => {
      calls.push("current");
      if (c.error) throw new Error("provider unavailable");
      return {data: {state: c.state ?? "open", base: {ref: c.base ?? "main"}}};
    },
  }, issues: {
    listComments: "comments",
    createComment: async (args) => {
      calls.push("post");
      assert.equal(args.pull_number ?? args.issue_number, 7);
      assert.equal(args.owner, "owner");
      assert.equal(args.repo, "project");
      assert.ok(args.body.startsWith(marker));
      assert.ok(args.body.includes("retarget this PR's base branch to `dev`"));
    },
  }};
  const github = {rest, paginate: async (operation) => {
    calls.push(operation);
    return operation === "files" ? (c.files ?? ["src/example.py"]).map(filename => ({filename}))
                                : c.comments ?? [];
  }};
  const context = {repo: {owner: "owner", repo: "project"},
                   payload: {pull_request: {number: 7, commits: 1}}};
  const run = () => execute(github, context, {info() {}}, {
    env: {PR_AUTHOR: c.author ?? "contributor", REPO_OWNER: "owner", HEAD_REF: c.branch ?? "pr/example"},
  });
  if (c.error) await assert.rejects(run, /provider unavailable/);
  else await run();
  assert.deepEqual(calls, c.calls, c.name);
}
console.log(`${cases.length} actual reminder consumer cases passed`);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-"],
        input="const SCRIPT = " + json.dumps(script) + ";\n" + driver,
        check=True, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.stdout.strip() == "13 actual reminder consumer cases passed"
