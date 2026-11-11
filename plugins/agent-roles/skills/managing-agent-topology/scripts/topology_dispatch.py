"""Derive advisory standing coverage and intent from saved native Dispatch observations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

LIVE_STATES = {"running", "idle", "waiting"}
RETAINING_STATES = {"reserved", "held", "unknown", "retiring"}

SETTLED_EFFECTS = {"accepted", "confirmed", "failed"}


def flow(
    assignment: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    fresh: Callable[[dict[str, Any] | None], bool],
    observe: Callable[[dict[str, str]], dict[str, Any] | None],
    materialization: dict[str, Any] | None,
    seed: dict[str, Any] | None,
    holds: list[str],
    supplied: bool,
) -> dict[str, Any]:
    """Describe the next step; native owners must recheck every effect fence."""
    result: dict[str, Any] = {
        "phase": "held", "holds": list(holds), "nextStep": None,
        "assignment": assignment["id"] if assignment else None,
        "nativeTask": assignment["nativeTask"] if assignment else None,
        "retainedWorktree": native_owner(snapshot) or (assignment["nativeWorktree"] if assignment else None),
        "advisoryOnly": True, "effectAuthorityGranted": False, "nativeRecheckRequired": True,
    }

    def step(phase: str, owner: str, operation: str, **fields: Any) -> dict[str, Any]:
        result.update(phase=phase, nextStep={"owner": owner, "operation": operation, **fields})
        return result

    def hold(*reasons: str) -> dict[str, Any]:
        result["holds"].extend(reasons)
        return result

    if holds:
        return result
    if assignment is None:
        return step("persist-assignment", "dispatcher", "persist-complete-assignment")
    if assignment["state"] in {"completed", "released"}:
        return hold("assignment-terminal-reconcile-with-owner-before-reopening")
    if assignment["kind"] != "standing":
        return hold("native-standing-flow-requires-standing-assignment")
    assert materialization is not None
    if assignment["nativeTask"] is None:
        return step("reconcile", "agent-dispatch", "declare-standing-charter",
                    materialization=materialization, persistMaterializationRequired=True)
    if not supplied:
        return hold("dispatch-evidence-not-supplied")
    if snapshot is None:
        return hold("native-task-evidence-missing")
    if not fresh(snapshot):
        return hold("native-task-evidence-stale-or-future")
    task = snapshot["task"]
    native = lane(assignment, snapshot, fresh, observe, materialization)
    fences = {"nativeTask": task["id"], "expectedRevision": task["standing_revision"]}
    mismatch = "native-declaration-differs-from-current-materialization"
    transitional = {
        mismatch, "native-charter-not-attached", "native-reservation-not-accepted",
    }
    if task["status"] in {"proposed", "queued"} and task["owner"] is None and native_owner(snapshot) is None:
        transitional.add("native-task-state-unqualified")
    blockers = [reason for reason in native["reasons"] if reason not in transitional]
    if blockers:
        return hold(*blockers)
    pending = [effect for effect in assignment["effects"] if effect["state"] not in SETTLED_EFFECTS]
    uncertain = [effect for effect in pending if effect["state"] == "unknown"]
    if uncertain:
        if len(uncertain) != 1:
            return hold("multiple-unknown-effects-require-owner-reconciliation")
        effect = uncertain[0]
        return step("recover-" + effect["kind"],
                    "agent-bridge" if effect["kind"] in {"launch", "relay"} else "agent-worktrees",
                    "recover-effect-receipt", effect=effect, replayAllowed=False)
    if mismatch in native["reasons"]:
        if task["status"] == "started" or pending:
            return hold(mismatch, "retained-owner-revision-reconciliation-required")
        return step("reconcile", "agent-dispatch", "reconcile-standing-charter",
                    **fences, materialization=materialization, persistMaterializationRequired=True)
    if task["status"] in {"proposed", "queued"}:
        if task["owner"] is not None or native_owner(snapshot) is not None:
            return hold("native-owner-retained-no-reallocation")
        allocations = [effect for effect in pending if effect["kind"] == "allocate"]
        if len(allocations) != len(pending) or len(allocations) > 1:
            return hold("effect-outcome-unsettled-no-replay")
        if allocations:
            effect = allocations[0]
            if effect["charterRevision"] != assignment["charter"]["revision"]:
                return hold("allocation-charter-revision-conflict")
            return step("allocate", "agent-worktrees", "allocate-keyed-worktree",
                        creationKey=assignment["creationKey"], machine=assignment["machine"], effect=effect)
        if assignment["nativeWorktree"] is None:
            if any(effect["kind"] == "allocate" for effect in assignment["effects"]):
                return hold("allocation-record-requires-owner-reconciliation")
            return step("persist-allocation", "dispatcher", "persist-allocation-effect",
                        creationKey=assignment["creationKey"], machine=assignment["machine"])
        return step("reserve", "agent-dispatch", "reserve-standing-charter",
                    **fences, workspace=assignment["nativeWorktree"], exactWorkspaceEvidenceRequired=True)
    if task["status"] not in {"claimed", "started"}:
        return hold("native-task-state-unqualified")
    relationship = snapshot["relationship"]
    if relationship is None or relationship["state"] != "bound" or not relationship["receipt"]:
        return hold("native-relationship-not-bound")
    if task["status"] == "claimed" and relationship["request"]["generation"] != task["generation"]:
        return hold("native-reservation-generation-conflict")
    if assignment["nativeWorktree"] != native["owner"]:
        return hold("persist-native-worktree-before-launch")
    launches = [effect for effect in assignment["effects"] if effect["kind"] == "launch"]
    if any(effect["kind"] != "launch" for effect in pending):
        return hold("effect-outcome-unsettled-no-replay")
    observation = observe(native["owner"])
    if observation is not None and (
        observation["role"] not in (None, assignment["role"])
        or (seed is not None and observation["scope"] not in (None, seed["role"]["scope"]))
    ):
        return hold("role-or-scope-mismatch")
    if task["status"] == "started":
        if native["status"] != "accepted":
            return hold(*native["reasons"])
        if pending:
            if len(pending) != 1 or pending[0]["state"] not in {"admitted", "consumed"}:
                return hold("effect-outcome-unsettled-no-replay")
            effect = pending[0]
            accepted = task["standing_charter"]["accepted_materialization"]
            if (effect.get("assignmentGeneration") != accepted["assignment_generation"]
                    or effect["charterRevision"] != assignment["charter"]["revision"]):
                return hold("launch-acceptance-fence-conflict")
            return step("settle-launch", "dispatcher", "record-native-acceptance",
                        effect=effect, acceptance=accepted)
        result["phase"] = "accepted"
        return result
    if not launches:
        return step("persist-launch", "dispatcher", "persist-launch-effect-and-envelope",
                    seed=seed, assignmentGeneration=task["generation"],
                    charterRevision=assignment["charter"]["revision"], workspace=native["owner"])
    if len(launches) != 1:
        return hold("multiple-launch-records-require-owner-reconciliation")
    effect = launches[0]
    if effect.get("assignmentGeneration") != task["generation"]:
        return hold("launch-assignment-generation-conflict")
    if effect["charterRevision"] != assignment["charter"]["revision"]:
        return hold("launch-charter-revision-conflict")
    if effect["state"] == "prepared":
        return step("launch", "agent-bridge", "start-existing-worktree",
                    effect=effect, workspace=native["owner"], **fences,
                    expectedGeneration=task["generation"])
    if effect["state"] not in {"admitted", "consumed", "accepted"}:
        return hold("launch-outcome-requires-owner-reconciliation")
    if not fresh(observation):
        return hold("owner-evidence-stale-missing-or-future")
    if (observation is None or observation["state"] not in LIVE_STATES
            or not observation["head"] or observation.get("headRevision") is None):
        return hold("current-native-head-unqualified")
    if observation["assignmentId"] not in (None, assignment["id"]):
        return hold("assignment-not-accepted-by-owner")
    if task["owner_session_id"] not in (None, observation["head"]):
        return hold("native-reserved-head-conflict")
    return step("accept", "agent-dispatch", "accept-standing-charter",
                **fences, expectedGeneration=task["generation"],
                workspace=relationship["request"]["workspace"],
                ownerSessionId=observation["head"], expectedHeadRevision=observation["headRevision"])


def linked(assignment: dict[str, Any], observations: dict[str, Any]) -> bool:
    """Keep legacy assignments independent of the optional Dispatch adapter."""
    return (
        "dispatch" in observations
        and assignment["kind"] == "standing"
        and assignment["nativeTask"] is not None
    )


def native_owner(snapshot: dict[str, Any] | None) -> dict[str, str] | None:
    """Retain the relationship's workspace, including uncertain attachments."""
    if snapshot is None or snapshot["relationship"] is None:
        return None
    relationship = snapshot["relationship"]
    if relationship["state"] in {"closed", "absent"} and snapshot["task"]["owner"] is None:
        return None
    workspace = relationship["request"]["workspace"]
    return {key: workspace[key] for key in ("machine", "project", "worktree")}


def lane(
    assignment: dict[str, Any],
    snapshot: dict[str, Any] | None,
    fresh: Callable[[dict[str, Any] | None], bool],
    observe: Callable[[dict[str, str]], dict[str, Any] | None],
    materialization: dict[str, Any] | None,
) -> dict[str, Any]:
    """Join native acceptance and current head evidence without granting effects."""
    owner = native_owner(snapshot) or assignment["nativeWorktree"]
    result: dict[str, Any] = {
        "status": "unknown", "owner": owner, "head": None, "headRevision": None,
        "reasons": [], "holdReason": None, "nativeTask": assignment["nativeTask"],
    }
    reasons = result["reasons"]
    if snapshot is None:
        reasons.append("native-task-evidence-missing")
        return result
    task = snapshot["task"]
    result["holdReason"] = task["hold_reason"]
    if not fresh(snapshot):
        reasons.append("native-task-evidence-stale-or-future")
        return result
    charter = task["standing_charter"]
    declaration = charter["declaration"]
    relationship = snapshot["relationship"]
    if materialization is not None and (
        declaration["source_revision"] != materialization["sourceRevision"]
        or declaration["expected_materialization_revision"] != materialization["materializationRevision"]
    ):
        reasons.append("native-declaration-differs-from-current-materialization")
    if task["id"] != assignment["nativeTask"]:
        reasons.append("native-task-identity-conflict")
    if relationship is not None:
        request = relationship["request"]
        workspace = request["workspace"]
        if request["task_id"] != task["id"]:
            reasons.append("native-relationship-task-conflict")
        if native_owner(snapshot) is not None and task["owner"] != (
            f'{workspace["machine"]}/{workspace["worktree"]}'
        ):
            reasons.append("native-relationship-owner-conflict")
    if owner is not None:
        if owner["machine"] != assignment["machine"]:
            reasons.append("native-owner-machine-conflict")
        if assignment["nativeWorktree"] is not None and owner != assignment["nativeWorktree"]:
            reasons.append("native-worktree-conflict")
    lifecycle = charter["lifecycle"]
    if lifecycle != "active":
        result["status"] = lifecycle if lifecycle in {"retiring", "retired"} else "unknown"
        reasons.append("native-charter-" + lifecycle)
        return result
    if task["hold_reason"] or task["status"] == "suspended":
        result["status"] = "held"
        reasons.append("native-task-held")
        return result
    if reasons:
        result["status"] = "held"
        return result
    if task["status"] in {"proposed", "queued"} and task["owner"] is None and owner is None:
        result["status"] = "desired"
        reasons.append("native-charter-not-attached")
        return result
    if task["status"] == "claimed":
        result["status"] = "reserved"
        reasons.append("native-reservation-not-accepted")
        return result
    if task["status"] != "started":
        reasons.append("native-task-state-unqualified")
        return result

    accepted = charter["accepted_materialization"]
    if relationship is None or relationship["state"] != "bound" or not relationship["receipt"]:
        reasons.append("native-relationship-not-bound")
    if accepted is None:
        reasons.append("native-materialization-not-accepted")
    else:
        if relationship is not None and (
            accepted["request_id"] != relationship["request"]["request_id"]
            or accepted["assignment_event_id"] != relationship["request"]["event_id"]
            or accepted["assignment_generation"] != relationship["request"]["generation"]
            or not relationship["receipt"]
            or accepted["relationship_ref"] != relationship["receipt"]["ref"]
        ):
            reasons.append("native-acceptance-relationship-conflict")
        predecessor = accepted["head"].get("predecessor_session_id")
        if (
            accepted["generation"] != task["generation"]
            or accepted["generation"] != accepted["expected_generation"] + int(predecessor is not None)
            or accepted["expected_generation"] < accepted["assignment_generation"]
            or accepted["expected_revision"] >= task["standing_revision"]
            or predecessor == accepted["head"]["session_id"]
        ):
            reasons.append("native-acceptance-fence-conflict")
        if (
            accepted["materialization_ref"] != declaration["materialization_ref"]
            or accepted["source_revision"] != declaration["source_revision"]
            or accepted["materialization_revision"] != declaration["expected_materialization_revision"]
        ):
            reasons.append("native-materialization-revision-not-accepted")
        observation = observe(owner) if owner is not None else None
        if not fresh(observation):
            reasons.append("owner-evidence-stale-missing-or-future")
        if observation is None or observation["state"] not in LIVE_STATES or not observation["head"]:
            reasons.append("current-native-head-unqualified")
        if observation is not None:
            result.update(head=observation["head"], headRevision=observation.get("headRevision"))
            if (
                observation["head"] != accepted["head"]["session_id"]
                or observation["head"] != task["owner_session_id"]
                or observation.get("headRevision") != accepted["head"]["revision"]
            ):
                reasons.append("native-accepted-head-conflict")
            if observation["assignmentId"] not in (None, assignment["id"]):
                reasons.append("assignment-not-accepted-by-owner")
    result["status"] = "held" if reasons else "accepted"
    return result


DESIRED_PROPOSED = (
    ("sourceRevision", "sourceRevision", "proposed-source-revision-differs-from-desired"),
    ("materializationRevision", "expectedMaterializationRevision",
     "proposed-effective-revision-differs-from-desired"),
)
PROPOSED_ADOPTED = (
    ("sourceRevision", "sourceRevision", "adopted-differs-from-proposed"),
    ("expectedMaterializationRevision", "materializationRevision", "adopted-differs-from-proposed"),
)
DESIRED_ADOPTED = (
    ("sourceRevision", "sourceRevision", "adopted-differs-from-desired"),
    ("materializationRevision", "materializationRevision", "adopted-differs-from-desired"),
)


def proposed_record(snapshot: dict[str, Any], fresh: bool) -> dict[str, Any]:
    """Project the native declaration; never the relationship or its receipt."""
    task = snapshot["task"]
    charter = task["standing_charter"]
    declaration = charter["declaration"]
    return {
        "nativeTask": task["id"], "observedAt": snapshot["observedAt"],
        "sourceRef": snapshot["sourceRef"], "fresh": fresh, "status": task["status"],
        "lifecycle": charter["lifecycle"], "holdReason": task["hold_reason"],
        "standingRevision": task["standing_revision"],
        "declarationGeneration": charter["declaration_generation"],
        "materializationRef": declaration["materialization_ref"],
        "sourceRevision": declaration["source_revision"],
        "expectedMaterializationRevision": declaration["expected_materialization_revision"],
    }


def adopted_record(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Project the native accepted materialization, which is not current-head proof."""
    accepted = snapshot["task"]["standing_charter"]["accepted_materialization"]
    if accepted is None:
        return None
    return {
        "acceptedHead": accepted["head"]["session_id"],
        "acceptedHeadRevision": accepted["head"]["revision"],
        "generation": accepted["generation"],
        "materializationRef": accepted["materialization_ref"],
        "sourceRevision": accepted["source_revision"],
        "materializationRevision": accepted["materialization_revision"],
    }


def differences(
    left: dict[str, Any] | None, right: dict[str, Any] | None,
    pairs: tuple[tuple[str, str, str], ...],
) -> list[str] | None:
    """Return mismatch reasons, or None when either side is not comparable."""
    if left is None or right is None or any(left[a] is None or right[b] is None for a, b, _ in pairs):
        return None
    return list(dict.fromkeys(reason for a, b, reason in pairs if left[a] != right[b]))


def intent_lane(
    desired: dict[str, Any], snapshot: dict[str, Any] | None, fresh: bool, supplied: bool,
) -> dict[str, Any]:
    """Keep desired, proposed and adopted intent separate; derive no effect."""
    proposed = None if snapshot is None else proposed_record(snapshot, fresh)
    adopted = None if snapshot is None else adopted_record(snapshot)
    current = proposed if fresh else None
    comparable = current if desired["sourceRevision"] is not None else None
    to_proposed = differences(desired, comparable, DESIRED_PROPOSED)
    to_adopted = differences(comparable, adopted if comparable else None, PROPOSED_ADOPTED)
    from_desired = differences(desired, adopted if comparable else None, DESIRED_ADOPTED)
    reasons: list[str] = []
    if desired["nativeTask"] is None:
        reasons.append("not-linked-to-native-task")
    elif not supplied:
        reasons.append("dispatch-evidence-not-supplied")
    elif snapshot is None:
        reasons.append("proposed-evidence-missing")
    elif not fresh:
        reasons.append("proposed-evidence-stale-or-future")
    if desired["sourceRevision"] is None:
        if current is not None and current["lifecycle"] == "active":
            reasons.append("desired-terminal-native-lane-active")
        state = "desired-terminal"
    elif reasons:
        state = "unlinked" if desired["nativeTask"] is None else "unknown"
    elif current is None or to_proposed is None:
        reasons.append("proposed-evidence-incomplete")
        state = "unknown"
    else:
        if current["lifecycle"] != "active":
            reasons.append("native-lane-" + current["lifecycle"])
        reasons.extend(to_proposed)
        if adopted is None:
            reasons.append("not-adopted")
        else:
            reasons.extend(to_adopted or [])
        if current["lifecycle"] != "active":
            state = "proposal-inactive"
        elif to_proposed:
            state = "proposal-pending"
        else:
            state = "adoption-pending" if reasons else "aligned"
    return {
        "desired": desired, "proposed": proposed, "adopted": adopted,
        "proposedMatchesDesired": None if to_proposed is None else not to_proposed,
        "adoptedMatchesProposed": None if to_adopted is None else not to_adopted,
        "adoptedMatchesDesired": None if from_desired is None else not from_desired,
        "state": state, "reasons": reasons,
    }
