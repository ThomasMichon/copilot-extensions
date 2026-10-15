"""Trusted metadata wiring, event repair, and automation origin contracts."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    if name.endswith(".md"):
        text = text.removeprefix("---\n").split("\n---\n", 1)[0]
    return yaml.load(text, Loader=yaml.BaseLoader)


def test_metadata_events_and_trusted_checkout_do_not_execute_pr_content():
    workflow = load("workflow-lockdown-guard.yml")
    trigger = workflow["on"]["pull_request_target"]
    assert trigger["branches"] == ["dev"]
    assert {"opened", "reopened", "synchronize", "edited", "labeled", "unlabeled"} <= set(trigger["types"])
    source = workflow["jobs"]["source-attribution"]
    assert source["name"] == "source-attribution"
    assert source["if"] == "github.event_name == 'pull_request_target'"
    checkout = source["steps"][0]
    assert checkout["uses"] == "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    assert checkout["with"] == {"ref": "${{ github.workflow_sha }}", "persist-credentials": "false"}
    install = source["steps"][1]
    assert install.get("working-directory", ".") == "."
    assert install["run"] == (
        "python worktree-manager/scripts/picker-snapshot/install.py --directory .github/actions "
        "--health-module commonmark --health-module parse5"
    )
    assert "required-pr-attribution.cjs" in source["steps"][2]["with"]["script"]
    assert source["steps"][2]["with"]["retries"] == "0"
    protected = workflow["jobs"]["workflow-lockdown-guard"]
    assert protected["if"] == "github.event_name == 'pull_request_target'"
    assert "protectedPrefixes" in protected["steps"][0]["with"]["script"]


def test_existing_heads_refresh_from_default_branch_or_verified_promotion():
    workflow = load("workflow-lockdown-guard.yml")
    refresh = workflow["jobs"]["refresh-source-attribution"]
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert "github.event.repository.default_branch" in refresh["if"]
    assert "refs/tags/" not in refresh["if"]
    assert refresh["permissions"] == {
        "contents": "read", "pull-requests": "read", "checks": "write",
    }
    guard, checkout, install, backfill = refresh["steps"]
    assert guard["if"] == "github.event_name == 'workflow_dispatch' && (inputs.promotion_sha != '' || inputs.promotion_run != '')"
    assert '"$RUN_REF" = "$DEFAULT_REF"' in guard["run"]
    assert '"$RUN_SHA" = "$WORKFLOW_SHA"' in guard["run"]
    assert "/compare/$EXPECTED_SHA...$WORKFLOW_SHA" in guard["run"]
    assert checkout["with"]["ref"] == "${{ inputs.promotion_sha || github.workflow_sha }}"
    assert checkout["uses"] == "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    assert checkout["with"]["persist-credentials"] == "false"
    assert install.get("working-directory", ".") == "."
    assert install["run"] == (
        "python worktree-manager/scripts/picker-snapshot/install.py --directory .github/actions "
        "--health-module commonmark --health-module parse5"
    )
    assert ".backfill(" in backfill["with"]["script"]
    assert backfill["with"]["retries"] == "0"
    assert "labeled" in workflow["jobs"]["request-review"]["if"]
    assert "unlabeled" in workflow["jobs"]["request-review"]["if"]


def test_departures_observe_retargets_without_skipped_required_contexts():
    workflow = load("source-attribution-departures.yml")
    trigger = workflow["on"]["pull_request_target"]
    assert "branches" not in trigger
    assert set(trigger["types"]) == {"synchronize", "closed", "edited"}
    assert set(workflow["jobs"]) == {"refresh-departed-source-attribution"}
    job = workflow["jobs"]["refresh-departed-source-attribution"]
    assert "github.event.changes.base.ref.from == 'dev'" in job["if"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read", "checks": "write"}
    assert job["concurrency"]["cancel-in-progress"] == "false"
    assert job["steps"][0]["uses"] == "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    assert job["steps"][0]["with"] == {"ref": "${{ github.workflow_sha }}", "persist-credentials": "false"}
    assert job["steps"][1].get("working-directory", ".") == "."
    assert job["steps"][1]["run"] == (
        "python worktree-manager/scripts/picker-snapshot/install.py --directory .github/actions "
        "--health-module commonmark --health-module parse5"
    )
    assert job["steps"][2]["with"]["retries"] == "0"
    assert ".departed(" in job["steps"][2]["with"]["script"]



def test_contract_tests_are_wired_and_dev_cleanup_has_a_workflow_origin():
    workflow = load("ci.yml")
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["main", "dev"]
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["checks"]["steps"]
    assert steps[0]["name"] == "Verify the dispatched promotion snapshot before checkout"
    assert '[ "$RUN_SHA" = "$EXPECTED_SHA" ]' in steps[0]["run"]
    assert '[ "$WORKFLOW_SHA" = "$EXPECTED_SHA" ]' in steps[0]["run"]
    assert steps[1]["uses"] == "actions/checkout@v4"
    assert steps[1]["with"] == {"fetch-depth": "0"}
    assert any("required-pr-attribution.test.cjs" in step.get("run", "") for step in steps)
    install = next(step for step in steps if step.get("name") == "Required source metadata contract tests")
    assert install.get("working-directory", ".") == "."
    assert install["run"].splitlines()[0] == (
        "python worktree-manager/scripts/picker-snapshot/install.py --directory .github/actions "
        "--health-module commonmark --health-module parse5"
    )
    assert any("test_required_source_attribution_workflow.py" in step.get("run", "") for step in steps)
    purge = (ROOT / ".github" / "workflows" / "purge-consumed-changefiles.yml").read_text(encoding="utf-8")
    assert "agent-worktrees:source workflow=purge-consumed-changefiles run=$GITHUB_RUN_ID" in purge
    assert '"$origin"' in purge
    agentic = load("ci-failure-fix-attempt.md")
    assert agentic["safe-outputs"]["create-pull-request"]["base-branch"] == "dev"


def test_metadata_parser_is_exactly_pinned_with_integrity():
    directory = ROOT / ".github" / "actions"
    manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((directory / "package-lock.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["commonmark"] == "0.31.2"
    parser = lock["packages"]["node_modules/commonmark"]
    assert parser["version"] == manifest["dependencies"]["commonmark"]
    assert parser["integrity"].startswith("sha512-")
    assert manifest["dependencies"]["parse5"] == "7.2.1"
    assert lock["packages"]["node_modules/parse5"]["version"] == "7.2.1"
    for name, package in lock["packages"].items():
        if name:
            assert package["resolved"].startswith("https://registry.npmjs.org/")
            assert package["integrity"].startswith("sha512-")
