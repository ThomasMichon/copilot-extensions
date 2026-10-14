"""Execute the shared trusted identifier reporter and its caller failure paths."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "identifier-leak-guard"
HEAD = "a" * 40


def load(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.fixture
def reporter(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP"}}
    env.update({
        "PR_NUMBER": "7", "PR_HEAD_SHA": HEAD, "ACTION_PATH": str(ACTION),
        "FINDINGS_PATH": str(tmp_path / "findings.json"),
        "DETAILS_PATH": str(tmp_path / "details.json"),
        "PATHS_PATH": str(tmp_path / "paths.txt"),
        "STALE": "false", "DENYLIST_CONFIGURED": "true", "SCAN_OUTCOME": "success",
        **{f"{name}_OUTCOME": "success" for name in (
            "SETUP_PYTHON", "PR", "INIT_FILES", "FETCH_PR_DATA", "DENYLIST",
        )},
    })
    script = yaml.safe_load((ACTION / "action.yml").read_text())["runs"]["steps"][-1]["with"]["script"]
    harness = """
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const result = {checks: [], comments: [], warnings: [], notices: [], outputs: {}, order: []};
const core = {
  notice(m) {result.notices.push(m);}, warning(m) {result.warnings.push(m);},
  setOutput(k, v) {result.outputs[k] = v;},
};
const github = {rest: {
  pulls: {async get() {
    if (input.api_error) throw new Error("metadata unavailable");
    return {data: {head: {sha: input.current_head}}};
  }},
  checks: {async create(check) {
    result.order.push("check");
    if (input.check_error) throw new Error("check delivery unavailable");
    result.checks.push(check);
  }},
  issues: {
    listComments() {},
    async createComment(c) {result.order.push("comment"); result.comments.push(c);},
    async updateComment(c) {result.order.push("update"); result.comments.push(c);},
  },
}, async paginate() {
  if (input.comment_error) throw new Error("comments forbidden");
  return input.comments;
}};
const context = {repo: {owner: "owner", repo: "project"}};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction("github", "context", "core", "require", input.script)(github, context, core, require)
  .then(() => process.stdout.write(JSON.stringify(result)))
  .catch(error => {result.error = error.message; process.stdout.write(JSON.stringify(result));});
"""

    def run(*, details=(), findings=(), paths='"changed.txt"\n', overrides=None, **options):
        Path(env["FINDINGS_PATH"]).write_text(json.dumps(findings), encoding="utf-8")
        Path(env["DETAILS_PATH"]).write_text(json.dumps(details), encoding="utf-8")
        Path(env["PATHS_PATH"]).write_text(paths, encoding="utf-8")
        inputs = {"script": script, "current_head": HEAD, "comments": [], **options}
        completed = subprocess.run(
            ["node", "-e", harness], input=json.dumps(inputs), cwd=tmp_path,
            env={**env, **(overrides or {})}, capture_output=True, text=True,
            check=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(completed.stdout)

    return run


def test_both_callers_keep_trusted_checkout_and_shared_action_with_failure_reporting():
    jobs = [
        load("promote-trigger.yml")["jobs"]["scan-and-report"],
        load("base-branch-reminder.yml")["jobs"]["scan-and-report"],
    ]
    assert jobs[0]["if"].startswith("github.event.workflow_run.event == 'pull_request'")
    assert jobs[1]["needs"] == "main-gate"
    assert jobs[1]["if"] == (
        "needs.main-gate.result == 'success' && "
        "github.event.pull_request.user.login == github.repository_owner"
    )
    for job, ref in zip(jobs, ("${{ github.workflow_sha }}", "${{ github.sha }}")):
        assert job["permissions"] == {
            "contents": "read", "pull-requests": "read", "issues": "write", "checks": "write",
        }
        assert job["timeout-minutes"] == 10
        checkout, guard, fallback = job["steps"]
        assert checkout["with"] == {"ref": ref, "fetch-depth": 1}
        assert checkout["continue-on-error"] is True
        assert guard["uses"] == "./.github/actions/identifier-leak-guard"
        assert guard["if"] == "steps.checkout.outcome == 'success'"
        assert guard["continue-on-error"] is True
        assert guard["with"]["facility-identifiers"] == "${{ secrets.FORBIDDEN_IDS_FACILITY }}"
        assert guard["with"]["work-identifiers"] == "${{ secrets.FORBIDDEN_IDS_WORK }}"
        assert fallback["if"] == (
            "always() && (steps.checkout.outcome != 'success' || steps.guard.outcome != 'success')"
        )
        assert "secrets." not in json.dumps(fallback)
    assert jobs[0]["steps"][-1]["with"]["script"] == jobs[1]["steps"][-1]["with"]["script"]
    action = yaml.safe_load((ACTION / "action.yml").read_text())
    steps = {step.get("id"): step for step in action["runs"]["steps"]}
    fetch = steps["fetch-pr-data"]["run"]
    assert "--paginate" in fetch and 'select(.status != "removed")' in fetch
    assert ".filename | @json" in fetch
    assert "--no-recurse-submodules" in fetch and "checkout" not in fetch
    assert "--git-ref" in steps["scan"]["run"] and "--trusted-details-json-out" in steps["scan"]["run"]
    assert "--paths-jsonl identifier-guard-paths.jsonl" in steps["scan"]["run"]
    for name in ("fetch-pr-data", "init-files", "denylist", "scan"):
        assert steps[name]["if"] == "steps.pr.outcome == 'success' && steps.pr.outputs.stale != 'true'"
    assert action["runs"]["steps"][-1]["if"] == "always()"
    assert "/.github/actions/" in (ROOT / ".github" / "CODEOWNERS").read_text()


@pytest.mark.parametrize("configured,conclusion", [("true", "success"), ("false", "neutral")])
def test_clean_or_genuinely_unconfigured_scan_preserves_check_contract(reporter, configured, conclusion):
    result = reporter(overrides={"DENYLIST_CONFIGURED": configured})
    check, = result["checks"]
    assert check["name"] == "identifier leak guard"
    assert check["head_sha"] == HEAD and check["conclusion"] == conclusion
    assert not result["comments"]


@pytest.mark.parametrize("step", [
    "SETUP_PYTHON", "PR", "INIT_FILES", "FETCH_PR_DATA", "DENYLIST", "SCAN",
])
@pytest.mark.parametrize("configured", ["true", "false"])
def test_infrastructure_failure_is_never_a_neutral_or_green_scan(reporter, step, configured):
    result = reporter(overrides={f"{step}_OUTCOME": "failure", "DENYLIST_CONFIGURED": configured})
    assert result["checks"][0]["conclusion"] == "failure"


@pytest.mark.parametrize("options", [
    {"overrides": {"STALE": "true"}}, {"current_head": "b" * 40},
])
def test_stale_event_and_mid_scan_head_advance_publish_nothing(reporter, options):
    result = reporter(**options)
    assert not result["checks"] and not result["comments"] and result["notices"]


def test_full_findings_are_in_the_check_before_best_effort_comment(reporter):
    detail = {"file": "generated.txt", "line": 3, "col": 2,
              "identifier": "generic-fixture-id", "reason": "replace this value"}
    result = reporter(details=[detail], comment_error=True, overrides={"SCAN_OUTCOME": "failure"})
    check, = result["checks"]
    assert check["conclusion"] == "failure"
    assert "generic-fixture-id" in check["output"]["text"]
    assert "generated.txt:3:2" in check["output"]["text"]
    assert "replace this value" in check["output"]["text"]
    assert result["order"] == ["check"] and result["warnings"]


def test_comment_update_requires_authenticated_marker_and_delivery(reporter):
    detail = {"file": "a", "line": 1, "col": 1, "identifier": "fixture-id"}
    body = f"<!-- identifier-leak-guard:{HEAD} -->"
    result = reporter(details=[detail], comments=[{"id": 1, "user": {"login": "outsider"}, "body": body}])
    assert result["order"] == ["check", "comment"]
    result = reporter(details=[detail], comments=[{
        "id": 2, "user": {"login": "github-actions[bot]"}, "body": body,
    }])
    assert result["order"] == ["check", "update"] and result["comments"][0]["comment_id"] == 2
    result = reporter(details=[detail], check_error=True)
    assert result["error"] and not result["comments"]


def test_large_findings_stay_within_check_text_cap_and_include_remainder(reporter):
    details = [{"file": "generated.txt", "line": i, "col": 1, "identifier": "x" * 100}
               for i in range(1000)]
    check = reporter(details=details)["checks"][0]
    assert len(check["output"]["text"]) < 65535 and "more." in check["output"]["text"]


@pytest.mark.parametrize("checkout,guard", [("failure", "skipped"), ("success", "failure")])
def test_fallback_runs_without_local_files_or_secrets_and_fences_stale_heads(reporter, checkout, guard):
    script = load("promote-trigger.yml")["jobs"]["scan-and-report"]["steps"][-1]["with"]["script"]
    overrides = {"CHECKOUT_OUTCOME": checkout, "GUARD_OUTCOME": guard,
                 "FINDINGS_PATH": "nonexistent", "DETAILS_PATH": "nonexistent"}
    result = reporter(script=script, overrides=overrides)
    assert result["checks"][0]["conclusion"] == "failure"
    assert result["checks"][0]["head_sha"] == HEAD
    result = reporter(script=script, overrides=overrides, current_head="b" * 40)
    assert not result["checks"]


def test_missing_report_is_an_explicit_action_error_for_caller_fallback(reporter):
    result = reporter(overrides={"DETAILS_PATH": "nonexistent"})
    assert result["error"] and not result["checks"]


@pytest.mark.parametrize("invalid", ["details", "findings"])
def test_wrong_report_shape_cannot_publish_success(reporter, invalid):
    result = reporter(**{invalid: {"invalid": "report"}})
    assert result["error"] and not result["checks"]


def test_reporter_counts_lossless_paths_and_rejects_invalid_records(reporter):
    paths = json.dumps("line\nbreak\u2028name.txt", ensure_ascii=False) + "\n"
    check = reporter(paths=paths)["checks"][0]
    assert check["output"]["summary"].startswith("Scanned 1 changed file")
    for invalid in ('123\n', '""\n', '"nul\\u0000name"\n', "not-json\n"):
        result = reporter(paths=invalid)
        assert result["error"] and not result["checks"]


@pytest.mark.parametrize("filename", [
    "generated.txt", "line\nbreak.txt", "line\rbreak.txt", "line\u2028break.txt",
    " leading space.txt ", "slash\\name.txt",
])
def test_composite_scans_generated_pr_blob_not_checked_out_tooling(tmp_path, filename):
    git = shutil.which("git")
    bash = shutil.which("bash")
    assert git
    if os.name == "nt":
        directory = Path(git).resolve().parent
        bash = next((str(p) for p in (
            directory / "bash.exe", directory.parent / "bin" / "bash.exe",
            directory.parent / "usr" / "bin" / "bash.exe",
        ) if p.is_file()), None)
    assert bash
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP",
    }}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                "COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP": "1", "PR_NUMBER": "7",
                "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": "fixture-identifier | replace it"})
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]

    def run(args, *, binary=False, **kwargs):
        return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=not binary,
                              encoding=None if binary else "utf-8",
                              timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                              **kwargs)

    for args in (["init", "-q"], ["config", "user.name", "Test"],
                 ["config", "user.email", "test@example.com"]):
        assert run([git, *args]).returncode == 0
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "check-no-internal-identifiers.py", tmp_path / "tools")
    blob = run([git, "hash-object", "-w", "--stdin"], input="fixture-identifier\n")
    assert blob.returncode == 0, blob.stderr
    # Git data can contain filenames that Windows cannot check out.
    tree = run([git, "mktree", "-z"], binary=True,
               input=f"100644 blob {blob.stdout.strip()}\t{filename}\0".encode("utf-8"))
    assert tree.returncode == 0, tree.stderr
    commit = run([git, "commit-tree", tree.stdout.decode("ascii").strip()], input="generated\n")
    assert commit.returncode == 0, commit.stderr
    listed = run([git, "ls-tree", "-rz", commit.stdout.strip()], binary=True)
    assert b"\t" + filename.encode("utf-8") + b"\0" in listed.stdout
    result = run([git, "update-ref", "refs/remotes/origin/pr-head/7", commit.stdout.strip()])
    assert result.returncode == 0, result.stderr
    # A different working tree proves the action reads the commit, not current files.
    (tmp_path / "generated.txt").write_text("clean worktree\n")
    (tmp_path / "identifier-guard-paths.jsonl").write_text(
        json.dumps(filename, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    action = yaml.safe_load((ACTION / "action.yml").read_text())
    command = next(s["run"] for s in action["runs"]["steps"] if s.get("id") == "scan")
    result = run([bash, "-e", "-c", command])
    assert result.returncode == 1, result.stdout + result.stderr
    assert (tmp_path / "identifier-guard-details.json").exists(), result.stdout + result.stderr
    details = json.loads((tmp_path / "identifier-guard-details.json").read_text())
    assert details[0]["identifier"] == "fixture-identifier"
    assert details[0]["file"] == filename and details[0]["reason"] == "replace it"
