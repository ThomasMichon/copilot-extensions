"""Saved native Dispatch evidence qualifies coverage without allocating workers."""

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
from topology import BOOKKEEPING_REVISION_WINDOW, Topology  # noqa: E402
from topology_contract import ContractError, read_json, timestamp, validate  # noqa: E402

AT = "2026-01-01T00:01:00Z"
pytestmark = [
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_roles.topology"),
]


def report(document: dict[str, Any]) -> Topology:
    return Topology(validate(document), timestamp(AT))


def bind_snapshot(document: dict[str, Any], index: int = 0) -> dict[str, Any]:
    assignment = document["assignments"][index]
    materialized = report(document).materialize(assignment["requestId"])
    owner = document["observations"]["owners"][0]
    declaration = {
        "materialization_ref": "synthetic:materialization-" + assignment["id"],
        "source_revision": materialized["sourceRevision"],
        "expected_materialization_revision": materialized["materializationRevision"],
    }
    request = {
        "request_id": "synthetic:attachment-" + assignment["id"],
        "event_id": index + 10, "task_id": assignment["nativeTask"], "generation": 1,
        "workspace": copy.deepcopy(owner["owner"]),
    }
    receipt = {"ref": "synthetic:relationship-" + assignment["id"]}
    return {
        "observedAt": AT, "sourceRef": "synthetic:dispatch-read",
        "task": {
            "id": assignment["nativeTask"], "kind": "standing-charter", "status": "started",
            "owner": "beta/review-a", "owner_session_id": owner["head"],
            "generation": 1, "hold_reason": None, "standing_revision": 3,
            "standing_charter": {
                "version": 1, "declaration": declaration, "declaration_generation": 1,
                "lifecycle": "active",
                "accepted_materialization": {
                    "version": 1, "head": {"session_id": owner["head"], "revision": 7},
                    "request_id": request["request_id"], "assignment_event_id": request["event_id"],
                    "relationship_ref": receipt["ref"],
                    "materialization_ref": declaration["materialization_ref"],
                    "source_revision": declaration["source_revision"],
                    "materialization_revision": declaration["expected_materialization_revision"],
                    "expected_revision": 2, "expected_generation": 1,
                    "assignment_generation": request["generation"], "generation": 1,
                },
            },
        },
        "relationship": {"request": request, "state": "bound", "receipt": receipt},
    }


@pytest.fixture
def document() -> dict[str, Any]:
    result = read_json(SKILL / "assets" / "topology.example.json")
    assert isinstance(result, dict)
    result["assignments"][0].update(
        nativeTask="synthetic-standing-task", nativeWorktree=None, state="proposed", effects=[],
    )
    result["observations"]["owners"][0].update(
        headRevision=7, assignmentId=None, role=None, scope=None,
        acceptedCharterRevision=None, acceptanceRef=None,
    )
    result["observations"]["dispatch"] = []
    result["observations"]["dispatch"].append(bind_snapshot(result))
    return result


def set_value(document: dict[str, Any], path: str, value: Any) -> None:
    target: Any = document
    parts = path.split(".")
    for key in parts[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    target[parts[-1]] = value


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.parametrize("command,args", [
    ("validate", []), ("overview", []), ("intent", []),
    ("placement", ["--role", "reviewer"]), ("route", ["--request", "request-review-a"]),
    ("seed", ["--request", "request-review-a"]),
    ("flow", ["--request", "request-review-a"]),
    ("relay", ["--request", "request-review-a", "--followup", "question-a"]),
    ("materialize", ["--request", "request-review-a"]), ("inspect-materialization", []),
])
def test_null_dispatcher_cli_is_read_only(
    document: dict[str, Any], tmp_path: Path, command: str, args: list[str],
) -> None:
    document["dispatcher"] = None
    document["observations"]["dispatcher"] = None
    document["observations"]["dispatch"] = [bind_snapshot(document)]
    data = report(document).materialize("request-review-a") if command == "inspect-materialization" else document
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "topology.py"), command, str(path), "--at", AT, *args],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert path.read_bytes() == before
    output = json.loads(result.stdout)
    if command in {"placement", "route", "seed", "overview", "flow", "relay"}:
        assert "dispatcher-not-appointed" in output["holds"]
    if command == "overview":
        assert output["coverage"][0]["qualifiedCount"] == 1
        assert output["shortfallIsLaunchAuthority"] is False
    if command == "route":
        assert output["target"] is None
        assert output["retainedWorktree"] == document["observations"]["owners"][0]["owner"]
    if command in {"materialize", "inspect-materialization"}:
        assert output["effective"]["dispatcher"] is None
    if command == "intent":
        assert output["lanes"][0]["state"] == "aligned"
        assert output["effectAuthorityGranted"] is False
        assert output["effectAuthorityGranted"] is False


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("state", ["running", "idle", "waiting"])
def test_only_joined_native_acceptance_counts_and_routes(
    document: dict[str, Any], state: str,
) -> None:
    document["observations"]["owners"][0]["state"] = state
    before = copy.deepcopy(document)
    topology = report(document)
    overview = topology.overview()
    lane = overview["coverage"][0]
    assert lane["qualifiedCount"] == 1
    assert lane["nativeStatusCounts"] == {"accepted": 1}
    assert overview["unmappedOwners"] == []
    assert overview["holds"] == []
    route = topology.route("request-review-a")
    assert route["status"] == "resolved"
    assert route["target"]["head"] == "synthetic-head-a"
    assert route["target"]["headRevision"] == 7
    assert route["advisoryOnly"] and route["nativeHeadRecheckRequired"]
    assert document == before


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("advance_head", [False, True])
def test_adjustment_reconciles_then_relays_to_same_head_and_requires_reacceptance(
    document: dict[str, Any], advance_head: bool,
) -> None:
    assignment = document["assignments"][0]
    observation = document["observations"]["owners"][0]
    task = document["observations"]["dispatch"][0]["task"]
    assignment["charter"].update(revision=2, objective="Review the revised synthetic scope.")
    followup = {"id": "adjust-a", "kind": "adjustment", "content": "Accept the revised charter.",
                "charterRevision": 2, "effectId": None}
    assignment["followups"].append(followup)
    request = assignment["requestId"]
    result = report(document).relay(request, "adjust-a")
    assert result["phase"] == "reconcile"
    assert result["retainedWorktree"] == observation["owner"]
    assert result["nextStep"]["expectedRevision"] == 3
    materialized = result["nextStep"]["materialization"]
    declaration = task["standing_charter"]["declaration"]
    declaration.update(source_revision=materialized["sourceRevision"],
                       expected_materialization_revision=materialized["materializationRevision"])
    task["standing_revision"] = 4
    result = report(document).relay(request, "adjust-a")
    assert result["phase"] == "persist-relay"
    assert result["nextStep"]["expectedHead"] == observation["head"]
    assert result["nextStep"]["expectedHeadRevision"] == 7
    assignment.update(nativeWorktree=observation["owner"], state="active")
    effect = {"id": "relay-a", "kind": "relay", "state": "prepared", "charterRevision": 2,
              "target": observation["owner"], "nativeRef": None,
              "nativeRequest": {"mode": "bridge-native", "envelopeRef": "synthetic:adjust-envelope"}}
    assignment["effects"].append(effect)
    followup["effectId"] = effect["id"]
    document["revision"] += 1
    result = report(document).relay(request, "adjust-a")
    assert result["phase"] == "relay"
    assert result["nextStep"]["operation"] == "send-exact-head"
    assert result["nextStep"]["replayAllowed"] is False
    assert result["nextStep"]["effect"] == effect
    assert result["nextStep"]["expectedHead"] == observation["head"]
    assert result["nextStep"]["expectedHeadRevision"] == 7
    if advance_head:
        observation["headRevision"] += 1
        held = report(document).relay(request, "adjust-a")
        assert held["phase"] == "held"
        assert "native-accepted-head-conflict" in held["holds"]
    effect.update(state="admitted", nativeRef="synthetic:relay-admission")
    result = report(document).relay(request, "adjust-a")
    assert result["phase"] == "await-acceptance"
    assert result["nextStep"]["operation"] == "accept-standing-charter"
    assert result["nextStep"]["expectedRevision"] == 4
    assert result["nextStep"]["sameHeadReacceptance"] is True
    assert result["nextStep"]["ownerSessionId"] == observation["head"]
    assert result["nextStep"]["expectedHeadRevision"] == observation["headRevision"]
    relationship = document["observations"]["dispatch"][0]["relationship"]
    assert result["nextStep"]["workspace"] == relationship["request"]["workspace"]
    accepted = task["standing_charter"]["accepted_materialization"]
    accepted.update(source_revision=declaration["source_revision"],
                    materialization_revision=declaration["expected_materialization_revision"],
                    expected_revision=4)
    accepted["head"]["revision"] = observation["headRevision"]
    task["standing_revision"] = 5
    result = report(document).relay(request, "adjust-a")
    assert result["phase"] == "settle-relay"
    assert result["nextStep"]["acceptance"] == accepted
    assert report(document).overview()["coverage"][0]["qualifiedCount"] == 0
    effect.update(state="accepted", nativeRef="synthetic:relay-acceptance")
    document["revision"] += 1
    topology = report(document)
    assert topology.relay(request, "adjust-a")["phase"] == "settled"
    assert topology.overview()["coverage"][0]["qualifiedCount"] == 1
    assert topology.route(request)["target"]["headRevision"] == observation["headRevision"]
    assert topology.flow(request)["phase"] == "accepted"
    assert topology.intent()["lanes"][0]["state"] == "proposal-pending"


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change,reason", [
    ("missing-acceptance", "native-materialization-not-accepted"),
    ("successor", "native-accepted-head-conflict"),
    ("revision-regressed", "native-accepted-head-conflict"),
    ("revision-missing", "native-accepted-head-conflict"),
    ("already-accepted-revision-advanced", "native-accepted-head-conflict"),
])
def test_relay_bookkeeping_drift_does_not_hide_unqualified_acceptance(
    document: dict[str, Any], change: str, reason: str,
) -> None:
    assignment = document["assignments"][0]
    observation = document["observations"]["owners"][0]
    task = document["observations"]["dispatch"][0]["task"]
    assignment["nativeWorktree"] = observation["owner"]
    assignment["followups"].append({
        "id": "adjust-a", "kind": "adjustment", "content": "Confirm the saved charter.",
        "charterRevision": 1, "effectId": "relay-a",
    })
    assignment["effects"].append({
        "id": "relay-a", "kind": "relay", "state": "consumed", "charterRevision": 1,
        "target": observation["owner"], "nativeRef": "synthetic:consumption",
        "nativeRequest": {"mode": "bridge-native", "envelopeRef": "synthetic:adjust-envelope"},
    })
    document["revision"] += 1
    if change == "missing-acceptance":
        task["standing_charter"]["accepted_materialization"] = None
    elif change == "successor":
        observation["head"] = "unaccepted-successor"
    else:
        observation["headRevision"] = {
            "revision-regressed": 6, "revision-missing": None, "already-accepted-revision-advanced": 8,
        }[change]
    result = report(document).relay(assignment["requestId"], "adjust-a")
    assert result["phase"] == "held"
    assert result["nextStep"] is None
    assert reason in result["holds"]
    assert report(document).overview()["coverage"][0]["qualifiedCount"] == 0
    assert report(document).route(assignment["requestId"])["target"] is None


@pytest.mark.portfolio_tier("T1")
def test_status_can_reach_retained_owner_while_acceptance_lags(document: dict[str, Any]) -> None:
    assignment = document["assignments"][0]
    task = document["observations"]["dispatch"][0]["task"]
    task["standing_charter"]["accepted_materialization"]["source_revision"] = "synthetic:prior-revision"
    assignment["followups"].append({
        "id": "status-a", "kind": "status", "content": "Report your accepted charter revision.",
        "charterRevision": 1, "effectId": None,
    })
    topology = report(document)
    result = topology.relay(assignment["requestId"], "status-a")
    assert result["phase"] == "persist-relay"
    assert result["nextStep"]["target"] == document["observations"]["owners"][0]["owner"]
    assert topology.overview()["coverage"][0]["qualifiedCount"] == 0
    document["observations"]["owners"][0]["head"] = "unaccepted-successor"
    held = report(document).relay(assignment["requestId"], "status-a")
    assert held["phase"] == "held"
    assert "native-accepted-head-conflict" in held["holds"]


@pytest.mark.portfolio_tier("T1")
def test_dispatcher_cannot_own_native_assignment(document: dict[str, Any]) -> None:
    document["dispatcher"]["owner"] = document["observations"]["owners"][0]["owner"]
    document["observations"]["dispatcher"]["owner"] = document["dispatcher"]["owner"]
    document["observations"]["dispatch"] = [bind_snapshot(document)]
    topology = report(document)
    reason = "dispatcher-cannot-own-worker-assignment"
    assert topology.overview()["coverage"][0]["qualifiedCount"] == 0
    assert reason in topology.route("request-review-a")["holds"]
    assert reason in topology.flow("request-review-a")["holds"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("generation", [2, 4])
def test_succession_keeps_original_attachment_generation(
    document: dict[str, Any], generation: int,
) -> None:
    snapshot = document["observations"]["dispatch"][0]
    task = snapshot["task"]
    owner = document["observations"]["owners"][0]
    owner.update(head="successor", headRevision=8)
    task.update(owner_session_id=owner["head"], generation=generation, standing_revision=9)
    accepted = task["standing_charter"]["accepted_materialization"]
    accepted.update(
        head={"session_id": owner["head"], "revision": 8, "predecessor_session_id": "predecessor"},
        expected_generation=generation - 1, generation=generation, expected_revision=7,
    )
    assert accepted["assignment_generation"] == snapshot["relationship"]["request"]["generation"] == 1
    topology = report(document)
    assert topology.overview()["coverage"][0]["qualifiedCount"] == 1
    assert topology.route("request-review-a")["target"]["head"] == "successor"

    accepted["head"]["predecessor_session_id"] = owner["head"]
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert "native-acceptance-fence-conflict" in lane["unqualified"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
def test_missing_dispatcher_custody_holds_routes_not_coverage(document: dict[str, Any]) -> None:
    document["observations"]["dispatcher"] = None
    topology = report(document)
    assert topology.overview()["coverage"][0]["qualifiedCount"] == 1
    assert topology.route("request-review-a")["holds"] == ["dispatcher-custody-unobserved"]
    assert topology.placement("reviewer", "standing")["holds"] == ["dispatcher-custody-unobserved"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("section,field,value,reason", [
    ("accepted_materialization", "request_id", "other", "native-acceptance-relationship-conflict"),
    ("accepted_materialization", "assignment_event_id", 99, "native-acceptance-relationship-conflict"),
    ("accepted_materialization", "assignment_generation", 9, "native-acceptance-relationship-conflict"),
    ("accepted_materialization", "relationship_ref", "other", "native-acceptance-relationship-conflict"),
    ("accepted_materialization", "generation", 9, "native-acceptance-fence-conflict"),
    ("accepted_materialization", "expected_generation", 0, "native-acceptance-fence-conflict"),
    ("accepted_materialization", "expected_generation", 9, "native-acceptance-fence-conflict"),
    ("accepted_materialization", "expected_revision", 3, "native-acceptance-fence-conflict"),
    ("accepted_materialization", "materialization_ref", "other", "native-materialization-revision-not-accepted"),
    ("accepted_materialization", "source_revision", "other", "native-materialization-revision-not-accepted"),
    ("accepted_materialization", "materialization_revision", "other", "native-materialization-revision-not-accepted"),
    ("declaration", "source_revision", "other", "native-declaration-differs-from-current-materialization"),
    ("declaration", "expected_materialization_revision", "other", "native-declaration-differs-from-current-materialization"),
])
def test_mismatched_native_acceptance_retains_owner_without_coverage(
    document: dict[str, Any], section: str, field: str, value: Any, reason: str,
) -> None:
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"]["standing_charter"][section][field] = value
    topology = report(document)
    lane = topology.overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert lane["ownerRetainingCount"] == 1
    assert reason in lane["unqualified"][0]["reasons"]
    route = topology.route("request-review-a")
    assert route["target"] is None
    assert route["retainedWorktree"] == snapshot["relationship"]["request"]["workspace"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("path,value,reason", [
    ("owners.0.head", "successor", "native-accepted-head-conflict"),
    ("owners.0.headRevision", 8, "native-accepted-head-conflict"),
    ("owners.0.headRevision", None, "native-accepted-head-conflict"),
    ("owners.0.state", "unreachable", "current-native-head-unqualified"),
    ("owners.0.observedAt", "2025-12-31T23:00:00Z", "owner-evidence-stale-missing-or-future"),
    ("owners.0.role", "scratch", "role-or-scope-mismatch"),
    ("owners.0.scope", "other/repo", "role-or-scope-mismatch"),
    ("dispatch.0.task.owner_session_id", "other", "native-accepted-head-conflict"),
    ("dispatch.0.task.owner", "alpha/another", "native-relationship-owner-conflict"),
    ("dispatch.0.relationship.request.task_id", "other", "native-relationship-task-conflict"),
    ("dispatch.0.relationship.state", "pending", "native-relationship-not-bound"),
    ("dispatch.0.relationship.receipt", None, "native-relationship-not-bound"),
    ("dispatch.0.relationship", None, "native-relationship-not-bound"),
    ("dispatch.0.task.standing_charter.accepted_materialization", None, "native-materialization-not-accepted"),
    ("dispatch.0.observedAt", "2025-12-31T23:00:00Z", "native-task-evidence-stale-or-future"),
    ("dispatch.0.observedAt", "2026-01-02T00:00:00Z", "native-task-evidence-stale-or-future"),
    ("dispatch", [], "native-task-evidence-missing"),
])
def test_current_native_evidence_is_required(
    document: dict[str, Any], path: str, value: Any, reason: str,
) -> None:
    set_value(document["observations"], path, value)
    topology = report(document)
    lane = topology.overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert reason in lane["unqualified"][0]["reasons"]
    assert topology.route("request-review-a")["target"] is None


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("status,lifecycle,hold,expected", [
    ("queued", "active", None, "desired"),
    ("claimed", "active", None, "reserved"),
    ("suspended", "active", None, "held"),
    ("started", "active", "operator decision", "held"),
    ("started", "retiring", None, "retiring"),
    ("completed", "retired", None, "retired"),
    ("completed", "active", None, "unknown"),
])
def test_native_lifecycle_states_are_not_accepted_coverage(
    document: dict[str, Any], status: str, lifecycle: str, hold: str | None, expected: str,
) -> None:
    snapshot = document["observations"]["dispatch"][0]
    task = snapshot["task"]
    task.update(status=status, hold_reason=hold)
    task["standing_charter"]["lifecycle"] = lifecycle
    if status == "queued":
        task.update(owner=None, owner_session_id=None)
        snapshot["relationship"] = None
        task["standing_charter"]["accepted_materialization"] = None
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert lane["nativeStatusCounts"] == {expected: 1}
    assert lane["unqualified"][0]["holdReason"] == hold


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("retained", [False, True])
def test_closed_detached_relationship_does_not_resurrect_old_owner(
    document: dict[str, Any], retained: bool,
) -> None:
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"].update(status="queued", owner=None, owner_session_id=None)
    snapshot["relationship"]["state"] = "closed"
    if retained:
        document["assignments"][0]["nativeWorktree"] = document["observations"]["owners"][0]["owner"]
    route = report(document).route("request-review-a")
    assert route["target"] is None
    assert route["nativeStatus"] == ("unknown" if retained else "desired")
    assert (route["retainedWorktree"] is not None) == retained
    assert report(document).overview()["unmappedOwners"]


@pytest.mark.portfolio_tier("T1")
def test_updated_declaration_requires_new_acceptance(document: dict[str, Any]) -> None:
    snapshot = document["observations"]["dispatch"][0]
    document["revision"] += 1
    document["assignments"][0]["charter"]["exclusions"] += " No sending."
    updated = bind_snapshot(document)["task"]["standing_charter"]["declaration"]
    snapshot["task"]["standing_charter"]["declaration"] = updated
    snapshot["task"]["standing_revision"] += 1
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert "native-materialization-revision-not-accepted" in lane["unqualified"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
def test_two_native_tasks_cannot_double_count_one_owner(document: dict[str, Any]) -> None:
    alias = copy.deepcopy(document["assignments"][0])
    alias.update(id="alias", requestId="alias-request", creationKey="alias-key", nativeTask="alias-task")
    document["assignments"].append(alias)
    document["roles"][0]["minimum"] = 2
    document["observations"]["dispatch"] = [bind_snapshot(document), bind_snapshot(document, 1)]
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 1
    assert lane["shortfall"] == 1
    assert "owner-already-counted" in lane["unqualified"][0]["reasons"]


@pytest.mark.portfolio_tier("T1")
def test_unmapped_native_task_blocks_placement(document: dict[str, Any]) -> None:
    extra = copy.deepcopy(document["observations"]["dispatch"][0])
    extra["task"]["id"] = "unmapped"
    document["observations"]["dispatch"].append(extra)
    topology = report(document)
    assert topology.overview()["unmappedNativeTasks"] == [extra]
    assert "unmapped-native-standing-charters-require-reconciliation" in topology.placement("reviewer", "standing")["holds"]


@pytest.mark.portfolio_tier("T1")
def test_policy_and_pending_effects_still_hold_native_coverage(document: dict[str, Any]) -> None:
    assignment = document["assignments"][0]
    assignment["nativeWorktree"] = document["observations"]["owners"][0]["owner"]
    assignment["effects"] = [{
        "id": "relay", "kind": "relay", "state": "unknown", "charterRevision": 1,
        "target": assignment["nativeWorktree"], "nativeRef": None,
        "nativeRequest": {"mode": "synthetic", "envelopeRef": "synthetic:retained-envelope"},
    }]
    document["machines"][1]["allowedRoles"] = []
    document["observations"]["dispatch"] = [bind_snapshot(document)]
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert {"role-disallowed", "effect-outcome-unsettled"} <= set(lane["unqualified"][0]["reasons"])
    assignment["nativeWorktree"] = None
    with pytest.raises(ContractError, match="retained native worktree"):
        validate(document)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("path,value", [
    ("observations.dispatch.0.relationship.receipt.token", "synthetic-not-a-token"),
    ("observations.dispatch.0.observedAt", "2026-01-01T00:00:00"),
    ("observations.dispatch.0.task.standing_charter.accepted_materialization.expected_generation", None),
    ("observations.dispatch.0.task.standing_charter.accepted_materialization.head.predecessor_session_id", ""),
])
def test_native_observation_contract_rejects_unsafe_or_ambiguous_shape(
    document: dict[str, Any], path: str, value: Any,
) -> None:
    set_value(document, path, value)
    with pytest.raises(ContractError):
        validate(document)


@pytest.mark.portfolio_tier("T1")
def test_acceptance_requires_expected_generation(document: dict[str, Any]) -> None:
    del document["observations"]["dispatch"][0]["task"]["standing_charter"]["accepted_materialization"]["expected_generation"]
    with pytest.raises(ContractError, match="expected_generation"):
        validate(document)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("duplicate", ["assignment", "observation"])
def test_native_task_identity_is_unique(document: dict[str, Any], duplicate: str) -> None:
    if duplicate == "assignment":
        alias = copy.deepcopy(document["assignments"][0])
        alias.update(id="alias", requestId="alias-request", creationKey="alias-key")
        document["assignments"].append(alias)
    else:
        document["observations"]["dispatch"].append(copy.deepcopy(document["observations"]["dispatch"][0]))
    with pytest.raises(ContractError, match="duplicate native task"):
        validate(document)


@pytest.mark.portfolio_tier("T1")
def test_without_dispatch_adapter_legacy_acceptance_remains_supported() -> None:
    document = read_json(SKILL / "assets" / "topology.example.json")
    document["assignments"][0]["nativeTask"] = "legacy-task-reference"
    topology = report(document)
    assert topology.overview()["coverage"][0]["qualifiedCount"] == 1
    assert topology.route("request-review-a")["status"] == "resolved"
    document["observations"]["dispatch"] = []
    assert report(document).overview()["coverage"][0]["qualifiedCount"] == 0


def progress(state: str = "advancing", **changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "observedAt": AT, "sourceRef": "synthetic:progress-read", "state": state,
        "workRef": "synthetic:child-task", "lastAdvancedAt": "2026-01-01T00:00:00Z",
        "blockingOwner": None,
    }
    result.update(changes)
    return result


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("state", ["advancing", "waiting", "blocked", "complete", "unknown"])
def test_progress_is_reported_beside_accepted_coverage(document: dict[str, Any], state: str) -> None:
    blocker = "synthetic:review-owner" if state == "blocked" else None
    document["observations"]["owners"][0]["progress"] = progress(state, blockingOwner=blocker)
    before = copy.deepcopy(document)
    topology = report(document)
    lane = topology.overview()["coverage"][0]
    assert lane["qualifiedCount"] == 1
    entry = lane["qualified"][0]
    assert entry["liveness"] == {
        "state": "idle", "fresh": True, "observedAt": "2026-01-01T00:00:00Z",
        "sourceRef": "synthetic:current-head-and-charter",
    }
    assert entry["progress"] == {
        "state": state, "workRef": "synthetic:child-task", "blockingOwner": blocker,
        "observedAt": AT, "sourceRef": "synthetic:progress-read",
        "sinceAdvancedSeconds": 60, "reasons": [],
    }
    assert lane["progressCounts"] == {state: 1}
    assert topology.route("request-review-a")["status"] == "resolved"
    assert document == before


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("evidence,reason", [
    ("absent", "progress-evidence-missing"),
    (None, "progress-evidence-missing"),
    (progress(observedAt="2025-12-31T23:50:00Z", lastAdvancedAt="2025-12-31T23:49:00Z"),
     "progress-evidence-stale-or-future"),
    (progress(observedAt="2026-01-01T00:05:00Z"), "progress-evidence-stale-or-future"),
])
def test_unqualified_progress_evidence_is_explicitly_unknown(
    document: dict[str, Any], evidence: Any, reason: str,
) -> None:
    owner = document["observations"]["owners"][0]
    if evidence == "absent":
        owner.pop("progress", None)
    else:
        owner["progress"] = copy.deepcopy(evidence)
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 1
    reported = lane["qualified"][0]["progress"]
    assert reported["state"] == "unknown"
    assert reported["reasons"] == [reason]
    assert reported["sinceAdvancedSeconds"] is None
    assert reported["workRef"] == (evidence["workRef"] if isinstance(evidence, dict) else None)
    assert lane["progressCounts"] == {"unknown": 1}


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["reserved-native-task", "unreachable-owner"])
def test_progress_never_substitutes_for_acceptance_or_presence(
    document: dict[str, Any], change: str,
) -> None:
    owner = document["observations"]["owners"][0]
    owner["progress"] = progress("advancing")
    if change == "reserved-native-task":
        document["observations"]["dispatch"][0]["task"]["status"] = "claimed"
    else:
        owner["state"] = "unreachable"
    topology = report(document)
    lane = topology.overview()["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert lane["shortfall"] == 1
    entry = lane["unqualified"][0]
    assert entry["progress"]["state"] == "advancing"
    assert entry["liveness"]["state"] == ("unreachable" if change == "unreachable-owner" else "idle")
    assert entry["nativeStatus"] == ("reserved" if change == "reserved-native-task" else "held")
    assert topology.route("request-review-a")["target"] is None


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("changes", [
    {"state": "stalled"},
    {"observedAt": "2026-01-01T00:00:10Z", "lastAdvancedAt": "2026-01-01T00:00:30Z"},
    {"lastAdvancedAt": "2026-01-01T00:00:00"},
    {"workRef": ""},
    {"unexpected": True},
])
def test_progress_contract_rejects_ambiguous_evidence(
    document: dict[str, Any], changes: dict[str, Any],
) -> None:
    document["observations"]["owners"][0]["progress"] = progress(**changes)
    with pytest.raises(ContractError):
        validate(document)


@pytest.mark.portfolio_tier("T1")
def test_legacy_lanes_report_progress_without_dispatch_adapter() -> None:
    document = read_json(SKILL / "assets" / "topology.example.json")
    document["observations"]["owners"][0]["progress"] = progress("waiting", lastAdvancedAt=None)
    lane = report(document).overview()["coverage"][0]
    assert lane["qualifiedCount"] == 1
    reported = lane["qualified"][0]["progress"]
    assert (reported["state"], reported["sinceAdvancedSeconds"]) == ("waiting", None)
    assert lane["qualified"][0]["liveness"]["state"] == "idle"

def intent_lane(document: dict[str, Any]) -> dict[str, Any]:
    lane: dict[str, Any] = report(document).intent()["lanes"][0]
    return lane


def change_desired_intent(document: dict[str, Any], effective: bool) -> None:
    document["revision"] += 1
    if effective:
        document["assignments"][0]["charter"]["exclusions"] += " No sending."


@pytest.mark.portfolio_tier("T1")
def test_intent_keeps_desired_proposed_and_adopted_records_separate(document: dict[str, Any]) -> None:
    before = copy.deepcopy(document)
    result = report(document).intent()
    lane = result["lanes"][0]
    desired = report(document).materialize("request-review-a")
    assert lane["state"] == "aligned" and lane["reasons"] == []
    assert lane["desired"]["sourceRevision"] == desired["sourceRevision"]
    assert lane["proposed"]["expectedMaterializationRevision"] == desired["materializationRevision"]
    assert lane["adopted"]["acceptedHead"] == document["observations"]["owners"][0]["head"]
    assert (lane["proposedMatchesDesired"], lane["adoptedMatchesProposed"], lane["adoptedMatchesDesired"]) == (
        True, True, True)
    assert result["stateCounts"] == {"aligned": 1}
    assert (result["advisoryOnly"], result["effectAuthorityGranted"], result["qualifiesCoverage"]) == (
        True, False, False)
    assert "synthetic:relationship" not in json.dumps(result)
    assert document == before


@pytest.mark.portfolio_tier("T1")
def test_locator_only_change_preserves_revision_alignment(document: dict[str, Any]) -> None:
    charter = document["observations"]["dispatch"][0]["task"]["standing_charter"]
    charter["declaration"]["materialization_ref"] = "synthetic:relocated-materialization"
    lane = intent_lane(document)
    assert lane["proposed"]["materializationRef"] != lane["adopted"]["materializationRef"]
    assert lane["state"] == "aligned" and lane["reasons"] == []
    assert (lane["proposedMatchesDesired"], lane["adoptedMatchesProposed"], lane["adoptedMatchesDesired"]) == (
        True, True, True)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("effective,reasons", [
    (False, ["proposed-source-revision-differs-from-desired"]),
    (True, ["proposed-source-revision-differs-from-desired", "proposed-effective-revision-differs-from-desired"]),
])
def test_changed_desired_intent_awaits_a_new_proposal(
    document: dict[str, Any], effective: bool, reasons: list[str],
) -> None:
    change_desired_intent(document, effective)
    lane = intent_lane(document)
    assert lane["state"] == "proposal-pending"
    assert lane["reasons"] == reasons
    assert lane["proposedMatchesDesired"] is False
    assert lane["adoptedMatchesProposed"] is True
    assert lane["adoptedMatchesDesired"] is False


@pytest.mark.portfolio_tier("T1")
def test_updated_proposal_awaits_adoption(document: dict[str, Any]) -> None:
    snapshot = document["observations"]["dispatch"][0]
    change_desired_intent(document, True)
    snapshot["task"]["standing_charter"]["declaration"] = bind_snapshot(document)["task"]["standing_charter"][
        "declaration"]
    lane = intent_lane(document)
    assert lane["state"] == "adoption-pending"
    assert lane["reasons"] == ["adopted-differs-from-proposed"]
    assert (lane["proposedMatchesDesired"], lane["adoptedMatchesProposed"], lane["adoptedMatchesDesired"]) == (
        True, False, False)


@pytest.mark.portfolio_tier("T1")
def test_unaccepted_proposal_is_not_adopted(document: dict[str, Any]) -> None:
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"].update(status="queued", owner=None, owner_session_id=None)
    snapshot["task"]["standing_charter"]["accepted_materialization"] = None
    snapshot["relationship"] = None
    lane = intent_lane(document)
    assert lane["state"] == "adoption-pending"
    assert lane["reasons"] == ["not-adopted"]
    assert lane["adopted"] is None
    assert lane["proposedMatchesDesired"] is True
    assert lane["adoptedMatchesProposed"] is None


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("path,value,reason", [
    ("dispatch.0.observedAt", "2025-12-31T23:00:00Z", "proposed-evidence-stale-or-future"),
    ("dispatch.0.observedAt", "2026-01-02T00:00:00Z", "proposed-evidence-stale-or-future"),
    ("dispatch", [], "proposed-evidence-missing"),
])
def test_unqualified_native_evidence_leaves_intent_unknown(
    document: dict[str, Any], path: str, value: Any, reason: str,
) -> None:
    set_value(document["observations"], path, value)
    lane = intent_lane(document)
    assert lane["state"] == "unknown"
    assert lane["reasons"] == [reason]
    assert lane["proposedMatchesDesired"] is None
    assert lane["adoptedMatchesProposed"] is None
    assert lane["adoptedMatchesDesired"] is None
    assert lane["proposed"] is None or lane["proposed"]["fresh"] is False


@pytest.mark.portfolio_tier("T1")
def test_unsupplied_dispatch_evidence_is_not_absent_proposal(document: dict[str, Any]) -> None:
    del document["observations"]["dispatch"]
    result = report(document).intent()
    assert result["dispatchEvidenceSupplied"] is False
    assert result["lanes"][0]["state"] == "unknown"
    assert result["lanes"][0]["reasons"] == ["dispatch-evidence-not-supplied"]
    assert result["lanes"][0]["proposed"] is None


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("status,lifecycle", [("started", "retiring"), ("completed", "retired")])
def test_inactive_native_lane_is_not_aligned(document: dict[str, Any], status: str, lifecycle: str) -> None:
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"]["status"] = status
    snapshot["task"]["standing_charter"]["lifecycle"] = lifecycle
    lane = intent_lane(document)
    assert lane["state"] == "proposal-inactive"
    assert lane["reasons"] == ["native-lane-" + lifecycle]
    assert lane["proposedMatchesDesired"] is True


@pytest.mark.portfolio_tier("T1")
def test_terminal_desired_intent_reports_active_native_lane(document: dict[str, Any]) -> None:
    document["assignments"][0]["state"] = "completed"
    lane = intent_lane(document)
    assert lane["state"] == "desired-terminal"
    assert lane["reasons"] == ["desired-terminal-native-lane-active"]
    assert lane["desired"]["sourceRevision"] is None
    assert lane["proposedMatchesDesired"] is None
    assert lane["adoptedMatchesProposed"] is None
    assert lane["adoptedMatchesDesired"] is None


@pytest.mark.portfolio_tier("T1")
def test_unmapped_native_proposal_has_no_desired_intent(document: dict[str, Any]) -> None:
    extra = copy.deepcopy(document["observations"]["dispatch"][0])
    extra["task"]["id"] = "unmapped"
    document["observations"]["dispatch"].append(extra)
    result = report(document).intent()
    assert result["lanes"][0]["state"] == "aligned"
    assert [item["proposed"]["nativeTask"] for item in result["proposedWithoutDesired"]] == ["unmapped"]
    assert result["proposedWithoutDesired"][0]["reasons"] == ["proposed-without-desired-intent"]


@pytest.mark.portfolio_tier("T1")
def test_intent_covers_only_standing_assignments() -> None:
    document = read_json(SKILL / "assets" / "topology.example.json")
    temporary = copy.deepcopy(document["assignments"][0])
    temporary.update(
        id="temporary-a", requestId="temporary-request", creationKey="temporary-key", kind="temporary", effects=[],
    )
    document["assignments"].append(temporary)
    result = report(document).intent()
    assert [lane["desired"]["assignment"] for lane in result["lanes"]] == ["review-a"]
    assert result["lanes"][0]["state"] == "unlinked"
    assert result["lanes"][0]["reasons"] == ["not-linked-to-native-task"]


def queued_flow(document: dict[str, Any]) -> dict[str, Any]:
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"].update(status="queued", owner=None, owner_session_id=None)
    snapshot["task"]["standing_charter"]["accepted_materialization"] = None
    snapshot["relationship"] = None
    document["observations"]["owners"] = []
    assignment: dict[str, Any] = document["assignments"][0]
    return assignment


def launch_flow(document: dict[str, Any], state: str = "prepared") -> dict[str, Any]:
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"]["status"] = "claimed"
    snapshot["task"]["standing_charter"]["accepted_materialization"] = None
    assignment = document["assignments"][0]
    assignment["nativeWorktree"] = copy.deepcopy(snapshot["relationship"]["request"]["workspace"])
    effect = {
        "id": "launch-flow", "kind": "launch", "state": state,
        "charterRevision": assignment["charter"]["revision"], "assignmentGeneration": 1,
        "target": copy.deepcopy(assignment["nativeWorktree"]),
        "nativeRef": "synthetic:launch-receipt" if state in {"admitted", "consumed", "accepted"} else None,
        "nativeRequest": {"mode": "bridge-existing-worktree-create", "envelopeRef": "synthetic:original-envelope"},
    }
    assignment["effects"] = [effect]
    return effect


@pytest.mark.portfolio_tier("T1")
def test_flow_persists_assignment_and_declaration_before_allocation(document: dict[str, Any]) -> None:
    queued_flow(document)
    assert report(document).flow("unrelated")["nextStep"]["operation"] == "persist-complete-assignment"
    document["assignments"][0]["nativeTask"] = None
    document["observations"]["dispatch"] = []
    before = copy.deepcopy(document)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "reconcile"
    step = result["nextStep"]
    assert step["operation"] == "declare-standing-charter"
    assert step["persistMaterializationRequired"] is True
    assert step["materialization"] == report(document).materialize("request-review-a")
    assert document == before


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("discovered", [False, True])
def test_flow_uses_one_keyed_allocation_then_reserves_returned_workspace(
    document: dict[str, Any], discovered: bool,
) -> None:
    observation = copy.deepcopy(document["observations"]["owners"][0])
    assignment = queued_flow(document)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "persist-allocation"
    assignment["effects"] = [{
        "id": "allocate-flow", "kind": "allocate", "state": "prepared", "charterRevision": 1,
        "target": None, "nativeRequest": None, "nativeRef": None,
    }]
    result = report(document).flow("request-review-a")
    assert result["phase"] == "allocate"
    assert result["nextStep"]["creationKey"] == assignment["creationKey"]
    assert result["nextStep"]["effect"] == assignment["effects"][0]
    owner = {"machine": "beta", "project": "example-control", "worktree": "returned"}
    assignment["nativeWorktree"] = owner
    assignment["effects"][0].update(state="confirmed", target=owner, nativeRef="synthetic:allocated")
    if discovered:
        observation.update(owner=owner, head=None, headRevision=None, state="unknown")
        document["observations"]["owners"].append(observation)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "reserve" and result["holds"] == []
    assert result["nextStep"] == {
        "owner": "agent-dispatch", "operation": "reserve-standing-charter",
        "nativeTask": assignment["nativeTask"], "expectedRevision": 3,
        "workspace": owner, "exactWorkspaceEvidenceRequired": True,
    }
    assert result["effectAuthorityGranted"] is False


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("unmapped", ["unconfirmed", "untargeted", "unrelated"])
def test_flow_requires_exact_confirmed_allocation_to_map_unattached_owner(
    document: dict[str, Any], unmapped: str,
) -> None:
    observation = copy.deepcopy(document["observations"]["owners"][0])
    assignment = queued_flow(document)
    owner = observation["owner"]
    assignment["nativeWorktree"] = owner
    assignment["effects"] = [{
        "id": "allocate-flow", "kind": "allocate", "state": "confirmed", "charterRevision": 1,
        "target": owner, "nativeRequest": None, "nativeRef": "synthetic:allocated",
    }]
    if unmapped == "unconfirmed":
        assignment["effects"][0].update(state="failed", nativeRef=None)
    elif unmapped == "untargeted":
        assignment["effects"][0]["target"] = None
    else:
        observation["owner"] = {**owner, "worktree": "unrelated"}
    document["observations"]["owners"].append(observation)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and result["nextStep"] is None
    assert "unmapped-native-owners-require-reconciliation" in result["holds"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("own_pending", [False, True])
def test_flow_does_not_ignore_another_assignments_pending_allocation(
    document: dict[str, Any], own_pending: bool,
) -> None:
    assignment = queued_flow(document)
    pending = {
        "id": "foreign-allocate", "kind": "allocate", "state": "unknown", "charterRevision": 1,
        "target": None, "nativeRequest": None, "nativeRef": None,
    }
    other = copy.deepcopy(assignment)
    other.update(id="other", requestId="other-request", creationKey="other-key", nativeTask=None, effects=[pending])
    document["assignments"].append(other)
    if own_pending:
        assignment["effects"] = [{**pending, "id": "own-allocate", "state": "prepared"}]
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and result["nextStep"] is None
    assert "allocation-outcome-unsettled" in result["holds"]


@pytest.mark.portfolio_tier("T1")
def test_flow_persists_full_seed_before_launch_and_fences_acceptance(document: dict[str, Any]) -> None:
    launch_flow(document)
    assignment = document["assignments"][0]
    assignment["effects"] = []
    result = report(document).flow("request-review-a")
    assert result["phase"] == "persist-launch"
    step = result["nextStep"]
    assert step["seed"] == report(document).seed("request-review-a")
    assert step["seed"]["assignment"]["charter"] == assignment["charter"]
    assert (step["assignmentGeneration"], step["charterRevision"]) == (1, 1)
    effect = launch_flow(document)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "launch"
    assert result["nextStep"]["effect"]["nativeRequest"] == effect["nativeRequest"]
    assert result["nextStep"]["expectedGeneration"] == 1
    effect.update(state="consumed", nativeRef="synthetic:consumption")
    result = report(document).flow("request-review-a")
    assert result["phase"] == "accept"
    assert result["nextStep"] == {
        "owner": "agent-dispatch", "operation": "accept-standing-charter",
        "nativeTask": assignment["nativeTask"], "expectedRevision": 3, "expectedGeneration": 1,
        "workspace": assignment["nativeWorktree"], "ownerSessionId": "synthetic-head-a", "expectedHeadRevision": 7,
    }
    document["observations"]["dispatch"] = [bind_snapshot(document)]
    result = report(document).flow("request-review-a")
    assert result["phase"] == "settle-launch"
    assert result["nextStep"]["acceptance"]["assignment_generation"] == 1
    effect["state"] = "accepted"
    result = report(document).flow("request-review-a")
    assert result["phase"] == "accepted" and result["nextStep"] is None


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("phase,field,reason", [
    ("reserve", "standing_revision", "native-standing-revision-unqualified"),
    ("launch", "standing_revision", "native-standing-revision-unqualified"),
    ("accept", "standing_revision", "native-standing-revision-unqualified"),
    ("launch", "generation", "native-task-generation-unqualified"),
    ("accept", "generation", "native-task-generation-unqualified"),
])
def test_flow_requires_positive_native_fences(
    document: dict[str, Any], phase: str, field: str, reason: str,
) -> None:
    if phase == "reserve":
        assignment = queued_flow(document)
        assignment["nativeWorktree"] = {"machine": "beta", "project": "demo", "worktree": "allocated"}
    else:
        launch_flow(document, "prepared" if phase == "launch" else "consumed")
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"][field] = 0
    if field == "generation":
        snapshot["relationship"]["request"]["generation"] = 0
        document["assignments"][0]["effects"][0]["assignmentGeneration"] = 0
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and result["nextStep"] is None
    assert reason in result["holds"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("kind,state,next_phase", [
    ("allocate", "prepared", "allocate"),
    ("launch", "prepared", "launch"),
    ("launch", "admitted", "accept"),
    ("launch", "consumed", "accept"),
])
def test_flow_reconciles_bookkeeping_without_replacing_pending_effects(
    document: dict[str, Any], kind: str, state: str, next_phase: str,
) -> None:
    if kind == "allocate":
        assignment = queued_flow(document)
        assignment["effects"] = [{
            "id": "allocate-flow", "kind": "allocate", "state": state, "charterRevision": 1,
            "target": None, "nativeRequest": None, "nativeRef": None,
        }]
    else:
        launch_flow(document, state)
    document["revision"] += 1
    before = copy.deepcopy(document)
    result = report(document).flow("request-review-a")
    step = result["nextStep"]
    assert result["phase"] == "reconcile" and result["holds"] == []
    assert step["operation"] == "reconcile-standing-charter"
    assert step["sourceOnly"] and step["persistMaterializationRequired"]
    assert step["replayAllowed"] is False
    assert step["retainedEffects"] == document["assignments"][0]["effects"]
    assert step["expectedRevision"] == 3
    assert document == before

    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"]["standing_charter"]["declaration"] = {
        **snapshot["task"]["standing_charter"]["declaration"],
        "source_revision": step["materialization"]["sourceRevision"],
        "expected_materialization_revision": step["materialization"]["materializationRevision"],
    }
    snapshot["task"]["standing_revision"] += 1
    result = report(document).flow("request-review-a")
    assert result["phase"] == next_phase and result["holds"] == []
    if kind == "launch":
        assert result["nextStep"]["expectedRevision"] == 4
        assert result["nextStep"]["expectedGeneration"] == 1
    else:
        assert result["nextStep"]["creationKey"] == document["assignments"][0]["creationKey"]
    assert document["assignments"] == before["assignments"]
    assert snapshot["relationship"] == before["observations"]["dispatch"][0]["relationship"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("change", ["charter", "creation-key", "unknown-source", "started", "old-source", "generation"])
def test_flow_bookkeeping_reconciliation_does_not_weaken_retained_effect_holds(
    document: dict[str, Any], change: str,
) -> None:
    launch_flow(document)
    document["revision"] += 1
    assignment = document["assignments"][0]
    snapshot = document["observations"]["dispatch"][0]
    if change == "charter":
        assignment["charter"]["exclusions"] += " No sending."
    elif change == "creation-key":
        assignment["creationKey"] = "different-key"
    elif change == "unknown-source":
        snapshot["task"]["standing_charter"]["declaration"]["source_revision"] = "unrecognized"
    elif change == "started":
        snapshot["task"]["status"] = "started"
    elif change == "old-source":
        document["revision"] += BOOKKEEPING_REVISION_WINDOW
    else:
        assignment["effects"][0]["assignmentGeneration"] += 1
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and result["nextStep"] is None
    reason = (
        "native-materialization-not-accepted" if change == "started"
        else "retained-owner-revision-reconciliation-required"
    )
    assert reason in result["holds"]


@pytest.mark.portfolio_tier("T1")
def test_flow_unknown_effect_recovery_precedes_bookkeeping_reconciliation(document: dict[str, Any]) -> None:
    effect = launch_flow(document, "unknown")
    document["revision"] += 1
    before = copy.deepcopy(document)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "recover-launch"
    assert result["nextStep"] == {
        "owner": "agent-bridge", "operation": "recover-effect-receipt", "effect": effect, "replayAllowed": False,
    }
    assert document == before


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("launch_state", ["unprepared", "prepared", "consumed"])
@pytest.mark.parametrize("owner_assignment", ["missing", "matching", "conflicting"])
def test_flow_checks_owner_assignment_before_preparing_or_launching(
    document: dict[str, Any], launch_state: str, owner_assignment: str,
) -> None:
    launch_flow(document, "consumed" if launch_state == "consumed" else "prepared")
    assignment = document["assignments"][0]
    if launch_state == "unprepared":
        assignment["effects"] = []
    if owner_assignment == "conflicting":
        other = copy.deepcopy(assignment)
        other.update(
            id="other-assignment", requestId="other-request", creationKey="other-key", nativeTask=None, effects=[],
        )
        document["assignments"].append(other)
    document["observations"]["owners"][0]["assignmentId"] = {
        "missing": None, "matching": assignment["id"], "conflicting": "other-assignment",
    }[owner_assignment]
    before = copy.deepcopy(document)
    result = report(document).flow("request-review-a")
    if owner_assignment == "conflicting":
        assert result["phase"] == "held" and result["nextStep"] is None
        assert "assignment-not-accepted-by-owner" in result["holds"]
    else:
        assert result["phase"] == {
            "unprepared": "persist-launch", "prepared": "launch", "consumed": "accept",
        }[launch_state]
        assert result["holds"] == []
    assert document == before


@pytest.mark.portfolio_tier("T1")
def test_flow_recovers_unknown_launch_with_original_envelope_not_new_head(document: dict[str, Any]) -> None:
    effect = launch_flow(document, "unknown")
    document["observations"]["owners"][0].update(head="new-head", headRevision=8)
    before = copy.deepcopy(document)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "recover-launch"
    assert result["nextStep"] == {
        "owner": "agent-bridge", "operation": "recover-effect-receipt", "effect": effect, "replayAllowed": False,
    }
    assert document == before


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("path,value,reason", [
    ("assignments.0.effects.0.assignmentGeneration", 2, "launch-assignment-generation-conflict"),
    ("observations.dispatch.0.relationship.request.generation", 2, "native-reservation-generation-conflict"),
    ("observations.dispatch.0.relationship.state", "pending", "native-relationship-not-bound"),
    ("observations.owners.0.headRevision", None, "current-native-head-unqualified"),
    ("observations.owners.0.headRevision", 0, "current-native-head-unqualified"),
    ("observations.owners.0.head", "successor", "native-reserved-head-conflict"),
    ("observations.owners.0.scope", "other/repo", "role-or-scope-mismatch"),
    ("observations.dispatch.0.observedAt", "2025-12-31T23:00:00Z", "native-task-evidence-stale-or-future"),
    ("observations.dispatch", [], "native-task-evidence-missing"),
    ("observations.dispatcher.custody", "unknown", "dispatcher-custody-not-exclusive"),
])
def test_flow_refuses_stale_or_conflicting_fences(
    document: dict[str, Any], path: str, value: Any, reason: str,
) -> None:
    launch_flow(document, "consumed")
    set_value(document, path, value)
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and result["nextStep"] is None
    assert reason in result["holds"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("field,value", [("role", "scratch"), ("scope", "other/repo")])
def test_flow_never_reports_wrong_role_or_scope_as_accepted(
    document: dict[str, Any], field: str, value: str,
) -> None:
    document["assignments"][0]["nativeWorktree"] = document["observations"]["owners"][0]["owner"]
    document["observations"]["owners"][0][field] = value
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and result["nextStep"] is None
    assert "role-or-scope-mismatch" in result["holds"]


@pytest.mark.portfolio_tier("T1")
def test_flow_requires_new_launch_envelope_for_new_charter(document: dict[str, Any]) -> None:
    launch_flow(document)
    document["assignments"][0]["charter"]["revision"] = 2
    snapshot = document["observations"]["dispatch"][0]
    snapshot["task"]["standing_charter"]["declaration"] = bind_snapshot(document)["task"]["standing_charter"][
        "declaration"]
    result = report(document).flow("request-review-a")
    assert result["phase"] == "held" and "launch-charter-revision-conflict" in result["holds"]
