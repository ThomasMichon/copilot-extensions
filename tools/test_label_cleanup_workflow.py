"""Structural regression for the maintenance workflow's mutation scopes."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_maintenance_workflow_preserves_branch_scope_and_trusted_label_job():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/stale-branch-sweep.yml").read_text(encoding="utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "label", "workflow_dispatch"}
    assert [row["cron"] for row in triggers["schedule"]] == ["30 9 * * 1", "17 * * * *"]
    assert triggers["label"]["types"] == ["created", "edited"]
    assert triggers["workflow_dispatch"]["inputs"]["labels_only"]["default"] is False
    branch = workflow["jobs"]["sweep"]
    assert "github.ref == 'refs/heads/main'" in branch["if"]
    assert "github.event_name == 'workflow_dispatch' && !inputs.labels_only" in branch["if"]
    assert "github.event.schedule == '30 9 * * 1'" in branch["if"]
    assert branch["concurrency"]["group"] == "stale-branch-sweep"
    assert branch["steps"][-1]["run"] == "python tools/sweep_stale_branches.py --execute"
    cleanup = workflow["jobs"]["labels"]
    assert cleanup["if"] == "github.ref == 'refs/heads/main'"
    assert cleanup["permissions"] == {"contents": "read", "issues": "write"}
    assert cleanup["steps"][0]["with"]["ref"] == "${{ github.workflow_sha }}"
    assert cleanup["steps"][0]["with"]["persist-credentials"] is False
    assert cleanup["steps"][-1]["env"]["COPILOT_EXTENSIONS_FORBIDDEN_LABELS"] == (
        "${{ secrets.FORBIDDEN_LABELS }}"
    )
