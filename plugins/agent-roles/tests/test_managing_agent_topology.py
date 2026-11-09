"""Offline behavioral qualification; no live fleet authority or adapters."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "managing-agent-topology"
sys.path.insert(0, str(SKILL / "scripts"))
from topology import Topology  # noqa: E402
from topology_contract import ContractError, MAX_BYTES, _shape, read_json, timestamp, validate  # noqa: E402

AT = "2026-01-01T00:01:00Z"
pytestmark = [
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_roles.topology"),
]


@pytest.fixture(autouse=True)
def _isolate_discovery() -> None:
    """This suite consumes only synthetic saved observations."""


@pytest.fixture
def document() -> dict[str, Any]:
    return cast(dict[str, Any], read_json(SKILL / "assets" / "topology.example.json"))


def report(document: dict[str, Any], at: str = AT) -> Topology:
    return Topology(validate(document), timestamp(at))


def invoke(
    tmp_path: Path, document: dict[str, Any], command: str, *args: str,
) -> tuple[int, dict[str, Any]]:
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "topology.py"), command, str(path), "--at", AT, *args],
        capture_output=True, text=True, timeout=15,
        creationflags=creationflags,
    )
    assert path.read_bytes() == before
    assert result.stderr == ""
    return result.returncode, json.loads(result.stdout)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("mutate", [
    lambda d: d.update(version=2),
    lambda d: d.update(revision=True),
    lambda d: d.update(extra="unsupported"),
    lambda d: d["assignments"][0]["charter"].pop("exclusions"),
    lambda d: d["assignments"].append(copy.deepcopy(d["assignments"][0])),
    lambda d: d["assignments"][0].update(machine="missing"),
    lambda d: d["assignments"][0].update(role="missing"),
    lambda d: d["assignments"][0]["charter"].update(scope="another/repo"),
    lambda d: d["assignments"][0]["effects"][1].update(target=None),
    lambda d: d["assignments"][0]["effects"][1].update(charterRevision=2),
    lambda d: d["assignments"][0]["effects"][1].update(nativeRef=None),
    lambda d: d["assignments"][0]["effects"][1].pop("nativeRequest"),
    lambda d: d["assignments"][0]["effects"][1].update(nativeRequest=None),
    lambda d: d["assignments"][0]["effects"][1]["nativeRequest"].update(envelopeRef=""),
    lambda d: d["assignments"][0]["effects"][1]["nativeRequest"].pop("mode"),
    lambda d: d["assignments"][0]["effects"][0].update(state="accepted"),
    lambda d: d["assignments"][0].update(state="released"),
    lambda d: d["observations"]["owners"][0].update(assignmentId="missing"),
    lambda d: d["observations"]["owners"][0].update(observedAt="2026-01-01T00:00:00"),
])
def test_invalid_contracts_fail_closed(
    document: dict[str, Any], mutate: Callable[[dict[str, Any]], object],
) -> None:
    mutate(document)
    with pytest.raises(ContractError):
        validate(document)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("text", ['{"version":1,"version":2}', '{"version":NaN}'])
def test_json_ambiguity_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError):
        read_json(path)


@pytest.mark.portfolio_tier("T1")
def test_oversized_input_rejected_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (MAX_BYTES + 1))
    with pytest.raises(ContractError, match="exceeds 2 MiB"):
        read_json(path)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("rule", [
    {"type": "string", "pattern": "^required$"},
    {"$ref": "https://example.invalid/schema"},
    {"$ref": "#/$defs/missing"},
    {"$ref": "#/$defs/text", "maxLength": 1},
])
def test_unsupported_schema_rules_never_silently_pass(rule: dict[str, Any]) -> None:
    schema = {"$defs": {"text": {"type": "string"}}}
    with pytest.raises(ContractError, match="unsupported bundled schema"):
        _shape("not-required", rule, schema, "$")


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("changes,reason", [
    ({"state": "unreachable"}, "current-native-head-unqualified"),
    ({"head": None}, "current-native-head-unqualified"),
    ({"role": "scratch"}, "role-or-scope-mismatch"),
    ({"scope": "another/repo"}, "role-or-scope-mismatch"),
    ({"acceptedCharterRevision": None}, "charter-revision-not-accepted"),
    ({"acceptanceRef": None}, "charter-revision-not-accepted"),
    ({"observedAt": "2025-12-31T23:00:00Z"}, "owner-evidence-stale-missing-or-future"),
    ({"observedAt": "2026-01-01T01:00:00Z"}, "owner-evidence-stale-missing-or-future"),
])
def test_uncertain_or_wrong_role_is_not_coverage(
    document: dict[str, Any], changes: dict[str, Any], reason: str,
) -> None:
    document["observations"]["owners"][0].update(changes)
    overview = report(document).overview()
    lane = overview["coverage"][0]
    assert lane["qualifiedCount"] == 0
    assert lane["shortfall"] == 1
    assert reason in lane["unqualified"][0]["reasons"]
    assert overview["shortfallIsLaunchAuthority"] is False


@pytest.mark.portfolio_tier("T1")
def test_same_native_owner_cannot_satisfy_two_lanes(document: dict[str, Any]) -> None:
    alias = copy.deepcopy(document["assignments"][0])
    alias.update(id="alias", requestId="alias-request", creationKey="alias-key", effects=[])
    document["assignments"].append(alias)
    document["roles"][0]["minimum"] = 2
    coverage = report(document).overview()["coverage"][0]
    assert coverage["qualifiedCount"] == 1
    assert coverage["shortfall"] == 1
    assert coverage["unqualified"][0]["assignment"] == "alias"
    route = report(document).route("alias-request")
    assert route["target"] is None
    assert "assignment-not-accepted-by-owner" in route["holds"]
    assert report(document).route("request-review-a")["status"] == "resolved"


@pytest.mark.portfolio_tier("T1")
def test_route_retains_assignment_for_charter_clarification_not_unaccepted_adoption(
    document: dict[str, Any],
) -> None:
    document["assignments"][0]["charter"]["revision"] = 2
    route = report(document).route("request-review-a")
    assert route["target"]["head"] == document["observations"]["owners"][0]["head"]
    document["observations"]["owners"][0]["assignmentId"] = None
    route = report(document).route("request-review-a")
    assert route["target"] is None
    assert "assignment-not-accepted-by-owner" in route["holds"]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.parametrize("terminal_state", ["completed", "released"])
def test_historical_assignment_survives_current_role_scope_change(
    document: dict[str, Any], terminal_state: str,
) -> None:
    historical = document["assignments"][0]
    original_charter = copy.deepcopy(historical["charter"])
    historical["state"] = terminal_state
    if terminal_state == "released":
        historical["effects"].append({
            "id": "retire-a", "kind": "retire", "state": "confirmed",
            "charterRevision": 1, "target": historical["nativeWorktree"],
            "nativeRequest": None, "nativeRef": "synthetic:retired-a",
        })
    current = copy.deepcopy(historical)
    owner = {**historical["nativeWorktree"], "worktree": "review-b"}
    current.update(
        id="review-b", requestId="request-review-b", creationKey="synthetic-review-b",
        state="active", nativeWorktree=owner, effects=[],
    )
    document["roles"][0]["scope"] = "example/new-project"
    current["charter"]["scope"] = "example/new-project"
    document["assignments"].append(current)
    observation = copy.deepcopy(document["observations"]["owners"][0])
    observation.update(
        owner=owner, head="synthetic-head-b", assignmentId="review-b",
        scope="example/new-project",
    )
    document["observations"]["owners"].append(observation)
    coverage = report(document).overview()["coverage"][0]
    assert coverage["qualifiedCount"] == 1
    assert coverage["shortfall"] == 0
    assert historical["charter"] == original_charter
    assert report(document).route("request-review-a")["target"] is None


@pytest.mark.portfolio_tier("T1")
def test_placement_filters_before_balancing_and_preserves_unknowns(document: dict[str, Any]) -> None:
    placement = report(document).placement("reviewer", "standing")
    assert [item["machine"] for item in placement["candidates"]] == ["beta", "alpha"]
    assert "role-disallowed" in placement["excluded"][0]["reasons"]
    document["observations"]["machines"][1]["usedSlots"] = 2
    assert [item["machine"] for item in report(document).placement("reviewer", "standing")["candidates"]] == ["alpha"]
    document["machines"][0]["capacitySlots"] = None
    assert report(document).placement("reviewer", "standing")["candidates"] == []
    document["machines"][0]["capacitySlots"] = 4
    document["machines"][0]["constraints"] = ["Native memory admission required"]
    assert report(document).placement("reviewer", "standing")["candidates"] == []
    temporary = report(document).placement("scratch", "temporary")
    assert [item["machine"] for item in temporary["candidates"]] == ["gamma"]


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_first_touch_discovers_existing_owners_instead_of_assuming_empty(
    document: dict[str, Any], tmp_path: Path,
) -> None:
    document["assignments"] = []
    document["observations"]["owners"][0]["assignmentId"] = None
    code, overview = invoke(tmp_path, document, "overview")
    assert code == 0
    assert overview["coverage"][0]["qualifiedCount"] == 0
    assert "unmapped-native-owners-require-reconciliation" in overview["holds"]
    assert overview["unmappedOwners"][0]["owner"]["worktree"] == "review-a"
    placement = report(document).placement("reviewer", "standing")
    assert placement["holds"] == overview["holds"]
    document["observations"]["discovery"]["complete"] = False
    assert "discovery-incomplete-stale-or-future" in report(document).overview()["holds"]


@pytest.mark.portfolio_tier("T1")
def test_dispatcher_collision_and_unknown_launch_hold_same_owner(document: dict[str, Any]) -> None:
    document["observations"]["dispatcher"]["custody"] = "conflict"
    route = report(document).route("request-review-a")
    assert route["target"] is None
    assert "dispatcher-custody-not-exclusive" in route["holds"]
    document["observations"]["dispatcher"]["custody"] = "exclusive"
    document["assignments"][0]["effects"][1]["state"] = "unknown"
    route = report(document).route("request-review-a")
    assert route["retainedWorktree"] == document["assignments"][0]["nativeWorktree"]
    assert route["target"] is None
    assert "effect-outcome-unsettled-no-replay" in route["holds"]
    assert report(document).overview()["coverage"][0]["qualifiedCount"] == 0


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_real_cli_persist_allocate_launch_restart_and_followup(
    document: dict[str, Any], tmp_path: Path,
) -> None:
    assignment = document["assignments"][0]
    native_owner = copy.deepcopy(assignment["nativeWorktree"])
    owner_observation = copy.deepcopy(document["observations"]["owners"][0])
    assignment.update(state="proposed", nativeWorktree=None, effects=[])
    document["observations"]["owners"] = []
    code, seed = invoke(tmp_path, document, "seed", "--request", "request-review-a")
    assert code == 0
    assert seed["assignment"]["charter"] == assignment["charter"]
    assert seed["assignment"]["creationKey"] == "synthetic-review-a"
    assert seed["effectAuthorityGranted"] is False

    allocation = {"id": "allocation-a", "kind": "allocate", "state": "unknown",
                  "charterRevision": 1, "target": None, "nativeRequest": None, "nativeRef": None}
    assignment["effects"].append(allocation)
    code, placement = invoke(tmp_path, document, "placement", "--role", "reviewer")
    assert code == 0
    assert "allocation-outcome-unsettled" in next(
        item["reasons"] for item in placement["excluded"] if item["machine"] == "beta"
    )
    assignment["nativeWorktree"] = native_owner
    allocation.update(state="confirmed", target=native_owner, nativeRef="synthetic:allocation-a")
    launch = {"id": "launch-a", "kind": "launch", "state": "admitted", "charterRevision": 1,
              "target": native_owner, "nativeRef": "synthetic:launch-a",
              "nativeRequest": {"mode": "bridge-existing-worktree-create",
                                "envelopeRef": "synthetic:immutable-launch-a-envelope-v1"}}
    assignment["effects"].append(launch)
    assignment["state"] = "active"
    document["observations"]["owners"] = [owner_observation]
    _, route = invoke(tmp_path, document, "route", "--request", "request-review-a")
    assert route["status"] == "held"  # Admission is not charter acceptance.
    launch["state"] = "accepted"
    owner_observation["head"] = "synthetic-successor"
    _, route = invoke(tmp_path, document, "route", "--request", "request-review-a")
    assert route["target"] == {"owner": native_owner, "head": "synthetic-successor"}
    _, unknown = invoke(tmp_path, document, "route", "--request", "unrelated-request")
    assert unknown["status"] == "unassigned"

    assignment["charter"]["revision"] = 2
    assignment["charter"]["objective"] = "Inspect the additional synthetic review scope."
    assignment["followups"].append({
        "id": "adjustment-a", "kind": "adjustment", "content": "Use charter revision 2.",
        "charterRevision": 2, "effectId": "relay-a",
    })
    relay = {"id": "relay-a", "kind": "relay", "state": "prepared", "charterRevision": 2,
             "target": native_owner, "nativeRef": None,
             "nativeRequest": {"mode": "bridge-native",
                               "envelopeRef": "synthetic:immutable-relay-a-envelope-v1"}}
    assignment["effects"].append(relay)
    _, overview = invoke(tmp_path, document, "overview")
    assert overview["coverage"][0]["qualifiedCount"] == 0
    relay.update(state="accepted", nativeRef="synthetic:revision-2-accepted")
    owner_observation.update(acceptedCharterRevision=2, acceptanceRef=relay["nativeRef"])
    _, overview = invoke(tmp_path, document, "overview")
    assert overview["coverage"][0]["qualifiedCount"] == 1
    assert overview["pendingEffects"] == []
    assert assignment["creationKey"] == "synthetic-review-a"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_unknown_relay_preserves_original_envelope_after_reload_and_succession(
    document: dict[str, Any], tmp_path: Path,
) -> None:
    envelope = {
        "session_id": "synthetic-bridge-a",
        "expected_native_session_id": "synthetic-head-a",
        "invocation_key": "relay-status-a",
        "caller_id": "synthetic-dispatcher",
        "prompt": "Report progress.\r\nKeep the original scope.",
        "no_resume": True,
    }
    envelope_path = tmp_path / "relay-status-a-v1.json"
    envelope_bytes = json.dumps(envelope).encode("utf-8")
    envelope_path.write_bytes(envelope_bytes)
    binding = {"mode": "bridge-native", "envelopeRef": str(envelope_path)}
    assignment = document["assignments"][0]
    assignment["effects"].append({
        "id": "relay-status-a", "kind": "relay", "state": "unknown", "charterRevision": 1,
        "target": assignment["nativeWorktree"], "nativeRequest": binding, "nativeRef": None,
    })
    assignment["followups"].append({
        "id": "status-a", "kind": "status", "content": envelope["prompt"],
        "charterRevision": 1, "effectId": "relay-status-a",
    })
    code, _ = invoke(tmp_path, document, "validate")
    assert code == 0
    reloaded = validate(read_json(tmp_path / "topology.json"))
    reloaded["observations"]["owners"][0]["head"] = "synthetic-successor"
    _, route = invoke(tmp_path, reloaded, "route", "--request", "request-review-a")
    assert route["target"] is None
    assert "effect-outcome-unsettled-no-replay" in route["holds"]
    _, seed = invoke(tmp_path, reloaded, "seed", "--request", "request-review-a")
    retained = seed["assignment"]["effects"][-1]
    assert retained["nativeRef"] is None
    assert retained["nativeRequest"] == binding
    assert Path(retained["nativeRequest"]["envelopeRef"]).read_bytes() == envelope_bytes
    assert read_json(envelope_path) == envelope


@pytest.mark.portfolio_tier("T1")
def test_dangling_followup_or_duplicate_request_rejected(document: dict[str, Any]) -> None:
    document["assignments"][0]["followups"].append({
        "id": "question-a", "kind": "status", "content": "Report progress.",
        "charterRevision": 1, "effectId": "missing",
    })
    with pytest.raises(ContractError, match="matching relay"):
        validate(document)
    document["assignments"][0]["followups"] = []
    other = copy.deepcopy(document["assignments"][0])
    other.update(id="other", creationKey="other", effects=[])
    document["assignments"].append(other)
    with pytest.raises(ContractError, match="requestId"):
        validate(document)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_unrelated_temporary_work_retains_standing_owner_and_native_exit(
    document: dict[str, Any], tmp_path: Path,
) -> None:
    standing = copy.deepcopy(document["assignments"][0])
    temporary = copy.deepcopy(standing)
    owner = {"machine": "gamma", "project": "example-control", "worktree": "scratch-a"}
    temporary.update(
        id="scratch-a", requestId="request-scratch-a", kind="temporary", role="scratch",
        machine="gamma", creationKey="synthetic-scratch-a", nativeWorktree=owner, effects=[],
    )
    temporary["charter"].update(scope="synthetic/scratch", objective="One unrelated analysis.")
    document["assignments"].append(temporary)
    observation = copy.deepcopy(document["observations"]["owners"][0])
    observation.update(
        owner=owner, head="synthetic-scratch-head", assignmentId="scratch-a",
        role="scratch", scope="synthetic/scratch",
    )
    document["observations"]["owners"].append(observation)
    _, route = invoke(tmp_path, document, "route", "--request", "request-scratch-a")
    assert route["target"]["owner"] == owner
    _, overview = invoke(tmp_path, document, "overview")
    assert overview["coverage"][0]["qualifiedCount"] == 1
    assert document["assignments"][0] == standing
    temporary["state"] = "completed"
    observation["state"] = "completed"
    assert report(document).route("request-scratch-a")["target"] is None
    temporary["state"] = "released"
    with pytest.raises(ContractError, match="native retirement"):
        validate(document)
    temporary["effects"].append({
        "id": "retire-scratch-a", "kind": "retire", "state": "confirmed",
        "charterRevision": 1, "target": owner, "nativeRequest": None,
        "nativeRef": "synthetic:retired-scratch-a",
    })
    _, overview = invoke(tmp_path, document, "overview")
    assert overview["coverage"][1]["qualifiedCount"] == 0
    assert overview["coverage"][0]["qualifiedCount"] == 1


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_cli_bad_input_and_missing_selector_return_structured_error(
    document: dict[str, Any], tmp_path: Path,
) -> None:
    code, result = invoke(tmp_path, document, "seed")
    assert code == 2 and "requires --request" in result["error"]
    document["version"] = 99
    code, result = invoke(tmp_path, document, "validate")
    assert code == 2 and result["valid"] is False
