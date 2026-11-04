"""Selected-runtime support and preservation admission precede owner writes."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from agent_dispatch import assignment_relationship_owner as module
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue_records import TaskError
from agent_dispatch.queue_task_relationships import WRITER


def support() -> dict:
    return {
        "schema": "copilot-extensions.resource-relationship",
        "version": 1,
        "operations": ["reserve", "bind", "inspect", "close"],
        "inspect_absence": True,
        "expected_creation_nonce": True,
        "writer_scope": "executing-process",
        "writer_contract": WRITER,
        "metadata_version": 2,
    }


def test_selected_prefix_and_authoritative_record_keep_original_identity(monkeypatch, tmp_path):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    calls = []

    def capture(argv, **kwargs):
        calls.append((argv, kwargs))
        if "capabilities" in argv:
            value = support()
        elif "record" in argv:
            value = {
                "ok": True,
                "worktree": {
                    "id": workspace.worktree,
                    "repo": workspace.project,
                    "path": workspace.path,
                    "creation_nonce": workspace.creation_nonce,
                },
            }
        else:
            assert json.loads(kwargs["input_text"])["expected_creation_nonce"] == "original"
            value = {"ok": True}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    admitted = []
    writer = module.WorktreeRelationshipWriter(
        ["/selected/python", "-m", "agent_worktrees"],
        admit_workspace=admitted.append,
    )
    assert writer(workspace, "reserve", {"expected_creation_nonce": "original"}) == {"ok": True}
    assert admitted == [workspace] and len(calls) == 3
    assert all(call[0][:3] == ["/selected/python", "-m", "agent_worktrees"] for call in calls)
    assert calls[-1][0][-3:] == ["--owner-ref", workspace.owner_ref, "--json"]


@pytest.mark.parametrize("change", ["old", "new_payload", "missing_nonce", "unknown"])
def test_missing_actual_runtime_support_refuses_before_record_or_effect(monkeypatch, change):
    calls = []
    declaration = copy.deepcopy(support())
    if change == "old":
        declaration.pop("writer_contract")
    elif change == "new_payload":
        declaration["writer_scope"] = "installed-payload"
    elif change == "missing_nonce":
        declaration["expected_creation_nonce"] = False
    else:
        declaration["version"] = True

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(declaration), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    with pytest.raises(TaskError, match="cannot protect"):
        module.WorktreeRelationshipWriter(
            ["/selected/owner"], admit_workspace=lambda workspace: None
        )
    assert len(calls) == 1 and "capabilities" in calls[0]


@pytest.mark.parametrize("reason", ["preservation", "incarnation"])
def test_unqualified_cohort_or_reused_workspace_cannot_reach_relationship_write(
    monkeypatch,
    tmp_path,
    reason,
):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        value = (
            support()
            if "capabilities" in argv
            else {
                "ok": True,
                "worktree": {
                    "id": workspace.worktree,
                    "repo": workspace.project,
                    "path": workspace.path,
                    "creation_nonce": "replacement",
                },
            }
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    def admit(workspace):
        if reason == "preservation":
            raise TaskError("participating writers are unqualified")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(["/selected/owner"], admit_workspace=admit)
    with pytest.raises(TaskError, match=r"unqualified|unconfirmed"):
        writer(workspace, "reserve", {"expected_creation_nonce": "original"})
    assert not any("reserve" in argv for argv in calls)
