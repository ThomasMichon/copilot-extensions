"""Execute shipped guidance examples through the actual standalone source CLI."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills/driving-effort-agendas"
CLI = SKILL / "scripts/agenda_cli.py"
ASSETS = SKILL / "assets"
SNAPSHOT_FIELDS = {
    "worktree", "revision", "status", "root", "stack", "next_action", "exit_criteria",
    "declared_outcomes", "queued", "parked", "notes", "cursor_version", "deferred",
    "due_rechecks", "next_revisit_at",
}


def asset(name: str) -> dict[str, Any]:
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


class SourceLane:
    def __init__(self, root: Path) -> None:
        self.home = root / "lane"
        self.home.mkdir()
        self.requests = root / "requests"
        self.requests.mkdir()
        self.operations = 0

    def run(self, command: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CLI), command, "--state-home", str(self.home), *args],
            capture_output=True, text=True, timeout=10, check=False,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    def invoke(self, command: str, *args: str) -> dict[str, Any]:
        result = self.run(command, *args)
        assert result.returncode == 0, f"source CLI rejected guidance {command}: {result.stderr}"
        return json.loads(result.stdout)

    def submit(self, request: dict[str, Any], *, init: bool = False) -> dict[str, Any]:
        path = self.requests / f"{request['operation_id']}.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        flags = ["--mode", "context-qualified"] if init else []
        return self.invoke(
            "init" if init else "update", *flags, "--request", str(path), "--session", "example-head",
        )

    def initialize(self) -> dict[str, Any]:
        return self.submit(asset("init.context-qualified.example.json"), init=True)

    def read(self) -> dict[str, Any]:
        return self.invoke("read")

    def current(self) -> tuple[str, str, str]:
        cursor = self.read()["documents"]["cursor"]
        frame_id = cursor["frames"][-1]
        item_id = cursor["frame_state"][frame_id]["pending"][0]
        return frame_id, item_id, cursor["item_state"][item_id]["current_attempt_id"]

    def update(self, change: dict[str, Any]) -> dict[str, Any]:
        self.operations += 1
        request = {
            "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
            "expected_revision": self.read()["revision"],
            "operation_id": f"guidance-operation-{self.operations}", "change": change,
        }
        return self.submit(request)

    def proof(self, criterion: str) -> list[dict[str, str]]:
        return [{
            "criterion": criterion, "kind": "measured", "ref": "synthetic-guidance-sequence",
            "inputs": "CLI state transition only; not external work acceptance or permission",
        }]

    def outcome(self, outcome: str) -> dict[str, Any]:
        state = self.read()
        frame, item, attempt = self.current()
        criteria = state["snapshot"]["exit_criteria"]
        return self.update({
            "operation": "outcome", "frame_id": frame, "item_id": item,
            "attempt_id": attempt, "outcome": outcome,
            "evidence": [proof for criterion in criteria for proof in self.proof(criterion)],
        })

    def expand(self, definitions: list[str]) -> dict[str, Any]:
        frame, item, attempt = self.current()
        return self.update({
            "operation": "expand", "frame_id": frame, "item_id": item, "attempt_id": attempt,
            "items": [{"definition_ref": definition} for definition in definitions],
            "continuation": {"parent_item_id": item, "next_action": "Reassess the saved parent with the child results."},
            "evidence": self.proof("The subqueue is a bounded prerequisite within the original scope."),
        })

    def return_child(self, expansion: dict[str, Any]) -> dict[str, Any]:
        return self.update({
            "operation": "return", "frame_id": expansion["frame_id"],
            "expansion_id": expansion["expansion_id"],
            "acceptance_ref": "example:synthetic-child-queue-accepted",
            "evidence": self.proof("The synthetic child queue's declared outcomes were recorded."),
        })

    def revisit(self, proposal: dict[str, Any]) -> dict[str, Any]:
        frame, item, attempt = self.current()
        return self.update({
            "operation": "revisit", "frame_id": frame, "item_id": item,
            "predecessor_attempt_id": attempt, **deepcopy(proposal),
        })


def test_source_cli_guidance_tail_nested_subqueues_and_saved_parent(tmp_path: Path) -> None:
    lane = SourceLane(tmp_path)
    original_result = lane.initialize()
    first = lane.read()
    assert set(first["snapshot"]) == SNAPSHOT_FIELDS and first["snapshot"]["cursor_version"] == 4
    assert first["delivery"]["state"] == "not-configured"
    frame, parent, attempt = lane.current()
    original_records = deepcopy(first["records"])
    original_sibling = first["documents"]["cursor"]["frame_state"][frame]["pending"][1]
    appended = lane.update({
        "operation": "append", "frame_id": frame, "items": [{"definition_ref": "sibling-b"}],
        "assignment_ref": "example:already-assigned-later-package",
        "evidence": lane.proof("The later sibling is already authorized."),
    })["result"]["transition"]["item_ids"][0]
    assert lane.read()["documents"]["cursor"]["frame_state"][frame]["pending"] == [
        parent, original_sibling, appended,
    ]
    lane.outcome("needs-context")
    outer = lane.expand(["inspect-input", "compare-results"])["result"]["transition"]
    child_position = lane.current()
    nested = lane.expand(["probe-fixture"])["result"]["transition"]
    assert len(lane.read()["documents"]["cursor"]["frames"]) == 3
    lane.outcome("done")
    lane.return_child(nested)
    assert lane.current() == child_position
    lane.outcome("done")
    lane.outcome("done")
    returned = lane.return_child(outer)["result"]["transition"]
    assert (returned["frame_id"], returned["item_id"], returned["attempt_id"]) == (frame, parent, attempt)
    state = lane.read()
    assert state["snapshot"]["status"] == "replanning"
    assert state["documents"]["cursor"]["frame_state"][frame]["pending"] == [
        parent, original_sibling, appended,
    ]
    assert all(state["records"][identity] == record for identity, record in original_records.items())
    assert lane.submit(asset("init.context-qualified.example.json"), init=True)["result"] == original_result["result"]
    admission = lane.revisit(asset("revisit.context-qualified.example.json"))["result"]["transition"]
    assert admission["decision"] == "revisit-admitted"
    new_attempt = lane.read()["records"][admission["attempt_id"]]["body"]
    assert new_attempt["predecessor_attempt_id"] == attempt
    assert new_attempt["approach"]["action"] == first["records"][attempt]["body"]["approach"]["action"]


def test_source_cli_guidance_same_command_productive_revisits_keep_history(tmp_path: Path) -> None:
    lane = SourceLane(tmp_path)
    lane.initialize()
    original = lane.read()
    frame, item, previous = lane.current()
    proposal = asset("revisit.context-qualified.example.json")
    for number in range(1, 6):
        lane.outcome("needs-context")
        current_proposal = deepcopy(proposal)
        current_proposal["context"]["facts"][0]["value"] = {"content": f"fixture-corrected-input-{number}"}
        transition = lane.revisit(current_proposal)["result"]["transition"]
        assert transition["decision"] == "revisit-admitted"
        state = lane.read()
        record = state["records"][transition["attempt_id"]]["body"]
        assert record["predecessor_attempt_id"] == previous
        assert record["approach"]["action"] == "Run the same bounded validation command."
        previous = transition["attempt_id"]
    state = lane.read()
    current = state["documents"]["cursor"]["item_state"][item]
    assert current["visits"] == 6 and len(current["attempts"]) == 6
    assert state["documents"]["cursor"]["frames"] == [frame]
    assert all(state["records"][identity] == record for identity, record in original["records"].items())
    assert state["documents"]["cursor"]["resource_caps"] == original["documents"]["cursor"]["resource_caps"]
    history = lane.invoke("history")["history"]
    assert all(record["transition"].get("reason_code") is None for record in history)


@pytest.mark.parametrize("metadata_only", [False, True])
def test_source_cli_guidance_no_context_novelty_records_decision_not_attempt(
    tmp_path: Path, metadata_only: bool,
) -> None:
    lane = SourceLane(tmp_path)
    lane.initialize()
    lane.outcome("needs-context")
    before = lane.read()
    _frame, item, attempt = lane.current()
    proposal = asset("revisit.metadata-only.example.json")
    if not metadata_only:
        proposal["context"]["facts"][0]["value"] = {"content": "fixture-input-A"}
    reply = lane.revisit(proposal)["result"]
    assert reply["persisted"] is True
    assert reply["transition"]["decision"] == "not-admitted"
    assert reply["transition"]["reason_code"] == "duplicate-context"
    assert reply["transition"]["attempt_id"] is None
    assert not any(record["kind"] == "attempt" for record in reply["records"])
    after = lane.read()
    assert after["snapshot"]["status"] == "replanning"
    assert after["snapshot"]["next_action"] == reply["transition"]["next_action"]
    assert "investigation" in after["snapshot"]["next_action"].lower()
    assert "wait" in after["snapshot"]["next_action"].lower()
    current = after["documents"]["cursor"]["item_state"][item]
    assert current["current_attempt_id"] == attempt and current["visits"] == 1
    assert all(after["records"][identity] == record for identity, record in before["records"].items())


def test_source_cli_guidance_resource_bound_replans_without_child(tmp_path: Path) -> None:
    lane = SourceLane(tmp_path)
    initial = asset("init.context-qualified.example.json")
    initial["change"]["resource_caps"]["max_depth"] = 1
    lane.submit(initial, init=True)
    before = lane.read()
    transition = lane.expand(["inspect-input"])["result"]["transition"]
    assert transition["decision"] == "not-admitted" and transition["attempt_id"] is None
    assert transition["reason_code"] == "depth-resource-bound"
    after = lane.read()
    assert after["documents"]["cursor"]["frames"] == before["documents"]["cursor"]["frames"]
    assert after["snapshot"]["status"] == "replanning"
    assert after["documents"]["cursor"]["resource_caps"] == before["documents"]["cursor"]["resource_caps"]


def test_source_cli_guidance_wait_defer_and_resume_only_preserve_attempt(tmp_path: Path) -> None:
    lane = SourceLane(tmp_path)
    lane.initialize()
    original = lane.read()
    frame, item, attempt = lane.current()
    waited = lane.update({
        "operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "bounded-replan", "reason_ref": "example:unchanged-input-investigation",
        "next_action": "Inspect relevant conditions without replaying the original command.",
        "evidence": lane.proof("The original continuation remains blocked without new context."),
    })["result"]["transition"]
    assert waited["decision"] == "waiting" and lane.current() == (frame, item, attempt)
    deferred = lane.update({
        "operation": "defer", "frame_id": frame,
        "decision_ref": "example:pending-input-condition",
        "decision_packet_ref": "example:bounded-recheck-packet",
        "revisit_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "evidence": lane.proof("The saved stack is retained for a qualified recheck."),
    })["result"]["transition"]
    assert lane.read()["documents"]["cursor"]["frames"] == []
    recovered = lane.update({
        "operation": "recover", "deferred_id": deferred["deferred_id"],
        "recheck_ref": "example:synthetic-condition-recheck",
        "acceptance_ref": "example:synthetic-scope-requalification",
        "attempt_policy": "resume-only",
        "evidence": lane.proof("Recovery restores the same attempt, not effect permission."),
    })["result"]["transition"]
    assert recovered["frame_ids"] == [frame] and recovered["attempt_id"] == attempt
    assert lane.current() == (frame, item, attempt)
    state = lane.read()
    assert state["documents"]["cursor"]["item_state"][item]["visits"] == 1
    assert state["documents"]["cursor"]["resource_caps"] == original["documents"]["cursor"]["resource_caps"]
    assert all(state["records"][identity] == record for identity, record in original["records"].items())


def test_source_cli_guidance_assign_root_preserves_deferred_projection_and_lineage(tmp_path: Path) -> None:
    lane = SourceLane(tmp_path)
    original_result = lane.initialize()
    original = lane.read()
    frame, item, attempt = lane.current()
    next_action = "Inspect the pending input condition without replaying the saved command."
    lane.update({
        "operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "example:pending-input-condition",
        "next_action": next_action, "evidence": lane.proof("The condition remains pending."),
    })
    assert lane.read()["snapshot"]["next_action"] == next_action
    revisit_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    deferred = lane.update({
        "operation": "defer", "frame_id": frame,
        "decision_ref": "example:pending-input-condition",
        "decision_packet_ref": "example:bounded-recheck-packet",
        "revisit_at": revisit_at, "evidence": lane.proof("The original root is preserved."),
    })["result"]["transition"]
    saved = lane.read()
    assert set(saved["snapshot"]) == SNAPSHOT_FIELDS
    assert saved["snapshot"]["root"] == original["snapshot"]["root"]
    assert saved["snapshot"]["deferred"][0]["id"] == deferred["deferred_id"]
    assert saved["snapshot"]["deferred"][0]["resume_action"] == next_action
    assert saved["snapshot"]["deferred"][0]["decision_packet_ref"] == "example:bounded-recheck-packet"
    assert saved["snapshot"]["next_revisit_at"] == revisit_at
    assert saved["snapshot"]["due_rechecks"] == []
    assert any("Continue the next already-assigned work package." in value for value in saved["snapshot"]["queued"])

    rejected = {
        "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
        "expected_revision": saved["revision"], "operation_id": "reject-root-reuse",
        "change": {
            "operation": "assign-root", "items": [{"definition_ref": "parent"}],
            "assignment_ref": "example:cannot-recreate-the-saved-parent",
            "baseline_context": asset("init.context-qualified.example.json")["change"]["baseline_context"],
            "evidence": lane.proof("A new identity cannot reopen the original definition."),
        },
    }
    path = lane.requests / "reject-root-reuse.json"
    path.write_text(json.dumps(rejected), encoding="utf-8")
    result = lane.run("update", "--request", str(path), "--session", "example-head")
    assert result.returncode == 2 and "already has an item" in result.stderr
    assert lane.read() == saved

    assigned = lane.update({
        "operation": "assign-root", "items": [{"definition_ref": "sibling-b"}],
        "assignment_ref": "example:already-assigned-independent-package",
        "baseline_context": asset("revisit.context-qualified.example.json")["context"],
        "evidence": lane.proof("This different root is already authorized."),
    })["result"]["transition"]
    assert assigned["decision"] == "root-assigned" and assigned["frame_id"] != frame
    during = lane.read()
    assert during["documents"]["cursor"]["deferred"] == saved["documents"]["cursor"]["deferred"]
    assert during["documents"]["cursor"]["item_state"][item]["current_attempt_id"] == attempt
    assert all(during["records"][identity] == record for identity, record in saved["records"].items())
    lane.outcome("done")
    assert lane.read()["snapshot"]["status"] == "waiting", "other-root completion cannot close the deferred root"
    recovered = lane.update({
        "operation": "recover", "deferred_id": deferred["deferred_id"],
        "recheck_ref": "example:synthetic-qualified-recheck",
        "acceptance_ref": "example:synthetic-scope-requalification",
        "attempt_policy": "resume-only",
        "evidence": lane.proof("Restore only the original continuation and attempt."),
    })["result"]["transition"]
    assert recovered["frame_ids"] == [frame] and recovered["attempt_id"] == attempt
    assert lane.current() == (frame, item, attempt)
    restored = lane.read()
    assert restored["documents"]["cursor"]["item_state"][item]["visits"] == 1
    assert restored["snapshot"]["next_action"] == next_action
    assert restored["documents"]["cursor"]["resource_caps"] == original["documents"]["cursor"]["resource_caps"]
    assert lane.submit(asset("init.context-qualified.example.json"), init=True)["result"] == original_result["result"]


@pytest.mark.parametrize("deferred", [False, True])
def test_source_cli_legacy_guidance_assets_stay_explicitly_legacy(tmp_path: Path, deferred: bool) -> None:
    lane = SourceLane(tmp_path)
    suffix = ".deferred" if deferred else ""
    board = asset(f"agenda{suffix}.example.json")
    cursor = asset(f"cursor{suffix}.example.json")
    request = {
        "schema": "efforts.agenda-request", "version": 1, "expected_revision": 0,
        "operation_id": "init-legacy-guidance",
        "change": {"operation": "init", "documents": {"cursor": cursor, "boards": {"agenda.json": board}}},
    }
    path = lane.requests / "legacy-init.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    lane.invoke("init", "--request", str(path))
    state = lane.read()
    assert state["snapshot"]["cursor_version"] == cursor["version"] in (1, 2)
    assert state["delivery"]["state"] == "not-configured"


def test_source_cli_field_contract_preserves_literal_prose_and_declared_outcome(tmp_path: Path) -> None:
    lane = SourceLane(tmp_path)
    request = asset("init.context-qualified.example.json")
    literal = "**Inspect** `input`\n[reference](https://example.com) [bold]literal[/bold]"
    definition = request["change"]["board"]["items"]["parent"]
    definition.update(action=literal, label="**Parent**", exit=["Record the `literal` outcome."])
    definition["on"] = {"custom-result": "replan"}
    request["change"]["baseline_context"]["facts"][0]["value"] = "# Raw input\n**not formatting**"
    lane.submit(request, init=True)
    state = lane.read()
    assert state["snapshot"]["next_action"] == literal
    assert state["snapshot"]["exit_criteria"] == definition["exit"]
    assert state["snapshot"]["declared_outcomes"] == {"custom-result": "replan"}
    assert state["documents"]["boards"]["root"]["items"]["parent"] == definition
    assert lane.outcome("custom-result")["result"]["persisted"] is True
    assert lane.read()["snapshot"]["status"] == "replanning"


@pytest.mark.parametrize("field", ["mode", "source-kind", "transition", "fact-kind"])
def test_source_cli_field_contract_rejects_prose_or_markdown_in_enums(tmp_path: Path, field: str) -> None:
    lane = SourceLane(tmp_path)
    request = asset("init.context-qualified.example.json")
    if field == "mode":
        request["mode"] = "**context-qualified**"
    elif field == "source-kind":
        request["change"]["board"]["source"]["kind"] = "an effort"
    elif field == "transition":
        request["change"]["board"]["items"]["parent"]["on"]["done"] = "**advance**"
    else:
        request["change"]["baseline_context"]["facts"][0]["kind"] = "Input"
    path = lane.requests / "invalid-enum.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    result = lane.run("init", "--mode", "context-qualified", "--request", str(path))
    assert result.returncode == 2 and result.stderr
