"""Admission proposals retain custody and consume only declared free pool budgets."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "managing-agent-topology"
sys.path.insert(0, str(SKILL / "scripts"))
from topology import Topology  # noqa: E402
from topology_admission import admission  # noqa: E402
from topology_contract import ContractError, read_json, timestamp, validate  # noqa: E402

AT = "2026-01-01T00:01:00Z"
pytestmark = [
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_roles.topology"),
]


@pytest.fixture
def document() -> dict[str, Any]:
    result: dict[str, Any] = read_json(SKILL / "assets" / "topology.example.json")
    result["observations"]["machines"][0]["usedSlots"] = 0
    result["observations"]["owners"][0]["headRevision"] = 7
    return result


def task(name: str = "work-a", **changes: Any) -> dict[str, Any]:
    return {
        "id": name, "requestId": "request-" + name, "observedAt": AT,
        "sourceRef": "synthetic:task-read", "role": "scratch", "kind": "temporary",
        "priority": 1, "operatorTask": False, "readiness": "ready", "holdReason": None, "status": "queued",
        "generation": 2, "owner": None, "ownerSessionId": None, **changes,
    }


@pytest.fixture
def evidence() -> dict[str, Any]:
    return {
        "version": 1, "observedAt": AT, "sourceRef": "synthetic:complete-queue-and-reserves",
        "complete": True, "reviewRoles": ["reviewer"],
        "machines": [
            {"machine": "alpha", "interactiveReserveSlots": 1, "reviewReserveSlots": 1},
            {"machine": "beta", "interactiveReserveSlots": 0, "reviewReserveSlots": 1},
            {"machine": "gamma", "interactiveReserveSlots": 0, "reviewReserveSlots": 0},
        ],
        "tasks": [task()],
    }


def report(document: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    return admission(Topology(validate(document), timestamp(AT)), evidence)


def dormant(document: dict[str, Any], **changes: Any) -> dict[str, Any]:
    observation = document["observations"]["owners"][0]
    return task(
        "dormant-review", requestId="request-review-a", role="reviewer", kind="standing",
        status="suspended", owner=copy.deepcopy(observation["owner"]),
        ownerSessionId=observation["head"], **changes,
    )


@pytest.mark.portfolio_tier("T1")
def test_ranked_batch_preserves_interactive_and_separate_review_capacity(
    document: dict[str, Any], evidence: dict[str, Any],
) -> None:
    evidence["tasks"] = [
        task("low", priority=0), task("z"), task("a"), task("b"), task("c"),
        task("review", role="reviewer", kind="standing", priority=0),
        task("operator", operatorTask=True, priority=100),
    ]
    before = copy.deepcopy((document, evidence))
    result = report(document, evidence)
    assert [item["task"] for item in result["proposals"]] == ["a", "b", "c", "z", "review"]
    assert {item["task"]: item["reasons"] for item in result["held"]} == {
        "operator": ["operator-task-preserved"], "low": ["no-eligible-reserved-capacity"],
    }
    assert [item["pool"] for item in result["proposals"]] == ["general"] * 4 + ["review"]
    alpha = next(item for item in result["capacity"] if item["machine"] == "alpha")
    assert alpha["interactiveReserveSlots"] == 1
    assert alpha["generalSlotsRemaining"] == 0
    assert alpha["reviewSlotsRemaining"] == 0
    assert (document, evidence) == before
    evidence["tasks"].reverse()
    assert report(document, evidence) == result


@pytest.mark.portfolio_tier("T1")
def test_dormant_work_retains_owner_generation_and_head(
    document: dict[str, Any], evidence: dict[str, Any],
) -> None:
    evidence["tasks"] = [dormant(document)]
    result = report(document, evidence)
    assert result["holds"] == []
    assert result["proposals"][0]["machine"] == "beta"
    assert result["proposals"][0]["nextStep"] == {
        "owner": "agent-dispatch", "operation": "resume-task", "expectedStatus": "suspended",
        "expectedGeneration": 2, "retainedWorktree": document["observations"]["owners"][0]["owner"],
        "expectedHead": "synthetic-head-a", "expectedHeadRevision": 7,
    }


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["no-dispatcher", "custody", "discovery", "stale", "future", "incomplete"])
def test_unqualified_global_evidence_holds_all_work(
    document: dict[str, Any], evidence: dict[str, Any], change: str,
) -> None:
    if change == "no-dispatcher":
        document["dispatcher"] = None
        document["observations"]["dispatcher"] = None
    elif change == "custody":
        document["observations"]["dispatcher"]["custody"] = "unknown"
    elif change == "discovery":
        document["observations"]["discovery"]["complete"] = False
    elif change == "incomplete":
        evidence["complete"] = False
    else:
        evidence["observedAt"] = "2025-01-01T00:00:00Z" if change == "stale" else "2027-01-01T00:00:00Z"
    result = report(document, evidence)
    assert result["holds"]
    assert result["proposals"] == []


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["stale", "head", "role", "running", "missing", "no-head-revision"])
def test_dormant_owner_is_not_replaced_or_retargeted(
    document: dict[str, Any], evidence: dict[str, Any], change: str,
) -> None:
    evidence["tasks"] = [dormant(document)]
    observation = document["observations"]["owners"][0]
    if change == "stale":
        observation["observedAt"] = "2025-01-01T00:00:00Z"
    elif change == "head":
        observation["head"] = "synthetic-successor"
    elif change == "role":
        observation["role"] = "scratch"
    elif change == "running":
        observation["state"] = "running"
    elif change == "missing":
        document["observations"]["owners"] = []
    else:
        observation.pop("headRevision")
    result = report(document, evidence)
    assert result["proposals"] == []
    assert result["held"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["reserve-missing", "reserve-overflow", "load-unknown", "load-stale", "constraint"])
def test_capacity_unknowns_do_not_become_free_slots(
    document: dict[str, Any], evidence: dict[str, Any], change: str,
) -> None:
    evidence["tasks"] = [dormant(document)]
    if change == "reserve-missing":
        evidence["machines"] = []
    elif change == "reserve-overflow":
        evidence["machines"][1]["interactiveReserveSlots"] = 1
    elif change == "load-unknown":
        document["observations"]["machines"][1]["usedSlots"] = None
    elif change == "load-stale":
        document["observations"]["machines"][1]["observedAt"] = "2025-01-01T00:00:00Z"
    else:
        document["machines"][1]["constraints"] = ["synthetic:requires-native-decision"]
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "no-eligible-reserved-capacity" in result["held"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("changes", [
    {"readiness": "waiting"}, {"readiness": "held"}, {"readiness": "unknown"},
    {"status": "started"}, {"operatorTask": True},
    {"holdReason": "synthetic:human-decision-pending"},
    {"observedAt": "2025-01-01T00:00:00Z"},
    {"ownerSessionId": "synthetic:retained-head"}, {"requestId": "request-review-a"},
])
def test_ineligible_work_never_consumes_budget(
    document: dict[str, Any], evidence: dict[str, Any], changes: dict[str, Any],
) -> None:
    evidence["tasks"] = [task("held", priority=10, **changes), task("eligible")]
    result = report(document, evidence)
    assert [item["task"] for item in result["proposals"]] == ["eligible"]
    assert result["held"][0]["task"] == "held"


@pytest.mark.portfolio_tier("T1")
def test_operator_owner_and_multiple_dormant_tasks_are_not_repurposed(
    document: dict[str, Any], evidence: dict[str, Any],
) -> None:
    evidence["tasks"] = [dormant(document, priority=2), task(
        "operator", operatorTask=True, status="started",
        owner=copy.deepcopy(document["observations"]["owners"][0]["owner"]),
        ownerSessionId="synthetic-head-a",
    )]
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "operator-owner-preserved" in result["held"][0]["reasons"]
    evidence["tasks"][1].update(
        id="dormant-other", requestId="request-other", operatorTask=False, status="suspended",
        role="reviewer", kind="standing",
    )
    document["machines"][1]["capacitySlots"] = 4
    evidence["machines"][1]["reviewReserveSlots"] = 3
    result = report(document, evidence)
    assert result["proposals"] == []
    assert any("retained-owner-has-other-assignment" in item["reasons"] for item in result["held"])
    assert all("retained-owner-has-unresolved-work" in item["reasons"] for item in result["held"])


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["task", "request", "machine", "review-role", "role", "boolean-slots", "generation"])
def test_bad_saved_contract_fails_explicitly(
    document: dict[str, Any], evidence: dict[str, Any], change: str,
) -> None:
    if change in {"task", "request"}:
        extra = copy.deepcopy(evidence["tasks"][0])
        extra["id" if change == "request" else "requestId"] = "other"
        evidence["tasks"].append(extra)
    elif change == "machine":
        evidence["machines"].append(copy.deepcopy(evidence["machines"][0]))
    elif change == "review-role":
        evidence["reviewRoles"] = ["undeclared"]
    elif change == "role":
        evidence["tasks"][0]["role"] = "undeclared"
    elif change == "boolean-slots":
        evidence["machines"][0]["reviewReserveSlots"] = True
    else:
        evidence["tasks"][0]["generation"] = -1
    with pytest.raises(ContractError):
        report(document, evidence)


@pytest.mark.portfolio_tier("T1")
def test_initial_queued_generation_zero_is_valid_but_dormant_zero_holds(
    document: dict[str, Any], evidence: dict[str, Any],
) -> None:
    evidence["tasks"][0]["generation"] = 0
    assert report(document, evidence)["proposals"][0]["nextStep"]["expectedGeneration"] == 0
    evidence["tasks"] = [dormant(document, generation=0)]
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "dormant-generation-unqualified" in result["held"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
def test_conflicting_native_snapshot_and_pending_effects_hold(
    document: dict[str, Any], evidence: dict[str, Any],
) -> None:
    from test_managing_agent_topology_dispatch import bind_snapshot

    document["assignments"][0]["nativeTask"] = "synthetic-standing-task"
    evidence["tasks"] = [dormant(document)]
    evidence["tasks"][0]["id"] = "synthetic-standing-task"
    document["observations"]["dispatch"] = [bind_snapshot(document)]
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "native-task-evidence-conflicting-or-stale" in result["held"][0]["reasons"]
    document["observations"].pop("dispatch")
    document["assignments"][0]["effects"][1]["state"] = "unknown"
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "retained-assignment-requires-reconciliation" in result["held"][0]["reasons"]


def native_dormant(document: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    from test_managing_agent_topology_dispatch import bind_snapshot

    assignment = document["assignments"][0]
    assignment["nativeTask"] = "synthetic-standing-task"
    snapshot = bind_snapshot(document)
    snapshot["task"].update(status="suspended", generation=2)
    document["observations"]["dispatch"] = [snapshot]
    evidence["tasks"] = [dormant(document)]
    evidence["tasks"][0]["id"] = assignment["nativeTask"]
    return snapshot


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["owner", "relationship-task", "unbound", "receipt", "retiring", "declaration"])
def test_native_dormant_requires_consistent_bound_active_custody(
    document: dict[str, Any], evidence: dict[str, Any], change: str,
) -> None:
    snapshot = native_dormant(document, evidence)
    assert len(report(document, evidence)["proposals"]) == 1
    if change == "owner":
        snapshot["task"]["owner"] = "beta/other-worktree"
    elif change == "relationship-task":
        snapshot["relationship"]["request"]["task_id"] = "other-task"
    elif change == "unbound":
        snapshot["relationship"]["state"] = "unknown"
    elif change == "receipt":
        snapshot["relationship"]["receipt"] = None
    elif change == "retiring":
        snapshot["task"]["standing_charter"]["lifecycle"] = "retiring"
    else:
        snapshot["task"]["standing_charter"]["declaration"]["source_revision"] = "synthetic:drifted"
    result = report(document, evidence)
    assert result["proposals"] == []
    assert any("native" in reason for reason in result["held"][0]["reasons"])


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("match", ["request", "task", "owner"])
def test_terminal_assignment_keeps_unsettled_custody_held(
    document: dict[str, Any], evidence: dict[str, Any], match: str,
) -> None:
    native_dormant(document, evidence)
    document["assignments"][0]["state"] = "completed"
    document["assignments"][0]["effects"][1]["state"] = "unknown"
    if match != "request":
        evidence["tasks"][0]["requestId"] = "different-request"
    if match != "task":
        evidence["tasks"][0]["id"] = "different-task"
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "terminal-assignment-retirement-unsettled" in result["held"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
def test_relationship_only_owner_still_blocks_other_work(
    document: dict[str, Any], evidence: dict[str, Any],
) -> None:
    native_dormant(document, evidence)
    assignment = document["assignments"][0]
    assignment.update(nativeWorktree=None, state="proposed", effects=[])
    document["observations"]["owners"][0]["assignmentId"] = None
    evidence["tasks"][0].update(id="other-task", requestId="other-request")
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "retained-owner-has-other-assignment" in result["held"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("field", ["id", "requestId"])
def test_both_request_and_native_task_identity_must_agree(
    document: dict[str, Any], evidence: dict[str, Any], field: str,
) -> None:
    native_dormant(document, evidence)
    evidence["tasks"][0][field] = "different"
    result = report(document, evidence)
    assert result["proposals"] == []
    assert "retained-assignment-identity-conflict" in result["held"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("changes", [
    {"status": "started"}, {"status": "claimed"}, {"status": "submitted"},
    {"status": "suspended", "readiness": "waiting"},
    {"status": "suspended", "holdReason": "synthetic:unresolved"},
    {"status": "completed"},
])
@pytest.mark.parametrize("reverse", [False, True])
def test_saved_owner_obligations_block_resume_regardless_of_priority_or_order(
    document: dict[str, Any], evidence: dict[str, Any], changes: dict[str, Any], reverse: bool,
) -> None:
    candidate = dormant(document, priority=100)
    retained = task(
        "unmapped-obligation", owner=copy.deepcopy(candidate["owner"]),
        ownerSessionId=candidate["ownerSessionId"], priority=0, **changes,
    )
    evidence["tasks"] = [candidate, retained] if not reverse else [retained, candidate]
    result = report(document, evidence)
    assert result["proposals"] == []
    candidate_result = next(item for item in result["held"] if item["task"] == candidate["id"])
    assert "retained-owner-has-unresolved-work" in candidate_result["reasons"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("changes", [
    {"ownerSessionId": "synthetic:conflicting-head"}, {"generation": 0}, {"role": "scratch"},
])
@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("priority", [0, 1000])
def test_unqualified_dormant_row_blocks_its_retained_workspace_before_ranking(
    document: dict[str, Any], evidence: dict[str, Any], changes: dict[str, Any], first: bool, priority: int,
) -> None:
    candidate = dormant(document, priority=10)
    conflict = copy.deepcopy(candidate)
    conflict.update(id="other-dormant", requestId="other-request", priority=priority, **changes)
    evidence["tasks"] = [conflict, candidate] if first else [candidate, conflict]
    result = report(document, evidence)
    assert result["proposals"] == []
    assert all("retained-owner-has-unresolved-work" in item["reasons"] for item in result["held"])


@pytest.mark.effect("process")
@pytest.mark.portfolio_tier("T2")
def test_real_cli_and_fresh_empty_work_are_read_only(
    document: dict[str, Any], evidence: dict[str, Any], tmp_path: Path,
) -> None:
    path = tmp_path / "topology.json"
    work = tmp_path / "work.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    work.write_text(json.dumps(evidence), encoding="utf-8")
    before = (path.read_bytes(), work.read_bytes())
    argv = [
        sys.executable, str(SKILL / "scripts" / "topology.py"), "admission", str(path),
        "--work", str(work), "--at", AT,
    ]
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == report(document, evidence)
    assert (path.read_bytes(), work.read_bytes()) == before
    evidence["tasks"] = []
    assert report(document, evidence)["proposals"] == []
    legacy = subprocess.run(
        argv[:2] + ["overview", str(path), "--at", AT],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert legacy.returncode == 0, legacy.stdout + legacy.stderr
    assert "coverage" in json.loads(legacy.stdout)


@pytest.mark.effect("process")
@pytest.mark.portfolio_tier("T2")
def test_cli_support_and_unsupported_work_version_are_explicit(
    document: dict[str, Any], evidence: dict[str, Any], tmp_path: Path,
) -> None:
    argv = [sys.executable, str(SKILL / "scripts" / "topology.py")]
    help_result = subprocess.run(
        argv + ["--help"], capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert help_result.returncode == 0
    assert "admission" in help_result.stdout and "--work" in help_result.stdout
    path = tmp_path / "topology.json"
    work = tmp_path / "work.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    evidence["version"] = 2
    work.write_text(json.dumps(evidence), encoding="utf-8")
    result = subprocess.run(
        argv + ["admission", str(path), "--work", str(work), "--at", AT],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 2
    assert "unsupported value/version" in json.loads(result.stdout)["error"]
