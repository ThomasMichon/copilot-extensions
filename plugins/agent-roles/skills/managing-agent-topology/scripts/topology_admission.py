"""Rank saved work evidence without claiming, resuming or allocating anything."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from topology_contract import owner_key, validate_admission
from topology_dispatch import native_owner

if TYPE_CHECKING:
    from topology import Topology


def admission(topology: Topology, evidence: Any) -> dict[str, Any]:
    evidence = validate_admission(evidence, topology.document)
    holds = topology.custody_holds() + topology.discovery_holds()
    if not topology.fresh(evidence) or not evidence["complete"]:
        holds.append("admission-evidence-incomplete-stale-or-future")
    budgets: dict[str, dict[str, Any]] = {}
    for policy in evidence["machines"]:
        machine = topology.machines[policy["machine"]]
        load = topology.loads.get(machine["id"])
        capacity = machine["capacitySlots"]
        used = load["usedSlots"] if load else None
        reasons = []
        if not topology.fresh(load) or load is None or load["state"] != "ready":
            reasons.append("load-not-ready-stale-missing-or-future")
        if capacity is None or used is None:
            reasons.append("capacity-or-load-unknown")
        free = max(0, capacity - used) if capacity is not None and used is not None else 0
        interactive = policy["interactiveReserveSlots"]
        review = policy["reviewReserveSlots"]
        if free < interactive + review:
            reasons.append("reserved-capacity-unavailable")
        if any(
            item["machine"] == machine["id"]
            and any(effect["kind"] == "allocate" for effect in topology.pending(item))
            for item in topology.assignments
        ):
            reasons.append("allocation-outcome-unsettled")
        budgets[machine["id"]] = {
            "machine": machine["id"], "interactiveReserveSlots": interactive,
            "reviewSlotsRemaining": review, "generalSlotsRemaining": max(0, free - interactive - review),
            "reasons": reasons,
        }
    result: dict[str, Any] = {
        "schema": "agent-roles.work-admission-report", "version": 1,
        "topologyRevision": topology.document["revision"], "observedAt": evidence["observedAt"],
        "sourceRef": evidence["sourceRef"], "holds": holds, "proposals": [], "held": [],
        "capacity": list(budgets.values()), "advisoryOnly": True,
        "effectAuthorityGranted": False, "nativeRecheckRequired": True,
    }
    operator_owners = {owner_key(task["owner"]) for task in evidence["tasks"]
                       if task["operatorTask"] and task["owner"] is not None}
    proposed_owners: set[tuple[str, str, str]] = set()
    for task in sorted(evidence["tasks"], key=lambda item: (-item["priority"], item["id"])):
        reasons = list(holds)
        if not topology.fresh(task):
            reasons.append("task-evidence-stale-or-future")
        if task["operatorTask"]:
            reasons.append("operator-task-preserved")
        if task["readiness"] != "ready":
            reasons.append("task-not-ready")
        if task["holdReason"] is not None:
            reasons.append("native-task-held")
        if task["status"] not in {"queued", "suspended"}:
            reasons.append("task-not-eligible")
        owner = task["owner"]
        if owner is not None and owner_key(owner) in operator_owners:
            reasons.append("operator-owner-preserved")
        if owner is not None and owner_key(owner) in proposed_owners:
            reasons.append("retained-owner-already-proposed")
        dormant = task["status"] == "suspended"
        snapshot = topology.dispatch.get(task["id"])
        if snapshot is not None:
            native = snapshot["task"]
            if (
                not topology.fresh(snapshot) or native["status"] != task["status"]
                or native["generation"] != task["generation"]
                or native["owner_session_id"] != task["ownerSessionId"]
                or native_owner(snapshot) != owner or native["hold_reason"] != task["holdReason"]
            ):
                reasons.append("native-task-evidence-conflicting-or-stale")
        if not dormant and (owner is not None or task["ownerSessionId"] is not None):
            reasons.append("queued-task-retains-owner")
        head_revision = None
        if dormant:
            if task["generation"] < 1:
                reasons.append("dormant-generation-unqualified")
            observation = topology.owners.get(owner_key(owner)) if owner is not None else None
            if owner is None or task["ownerSessionId"] is None:
                reasons.append("dormant-owner-unobserved")
            elif topology.dispatcher_self(owner):
                reasons.append("dispatcher-cannot-own-worker-assignment")
            if observation is None or not topology.fresh(observation):
                reasons.append("dormant-owner-evidence-stale-missing-or-future")
            elif (
                observation["state"] not in {"idle", "waiting"}
                or not observation["head"] or not observation.get("headRevision")
                or observation["head"] != task["ownerSessionId"]
                or observation["role"] != task["role"]
                or observation["scope"] != topology.roles[task["role"]]["scope"]
            ):
                reasons.append("dormant-owner-not-ready-or-conflicting")
            else:
                head_revision = observation["headRevision"]
        for assignment in topology.assignments:
            if assignment["state"] in {"completed", "released"}:
                continue
            if assignment["requestId"] != task["requestId"] and assignment["nativeTask"] != task["id"]:
                if dormant and owner is not None and assignment["nativeWorktree"] == owner:
                    reasons.append("retained-owner-has-other-assignment")
                continue
            if not dormant:
                reasons.append("request-already-assigned")
            elif (
                assignment["role"] != task["role"] or assignment["kind"] != task["kind"]
                or assignment["nativeWorktree"] != owner or topology.pending(assignment)
            ):
                reasons.append("retained-assignment-requires-reconciliation")
        pool = "reviewSlotsRemaining" if task["role"] in evidence["reviewRoles"] else "generalSlotsRemaining"
        candidates = []
        excluded = []
        for machine in topology.machines.values():
            if dormant and (owner is None or machine["id"] != owner["machine"]):
                continue
            policy = budgets.get(machine["id"])
            blocked = topology.policy_reasons(machine, topology.roles[task["role"]], task["kind"])
            if machine["constraints"] or topology.roles[task["role"]]["constraints"]:
                blocked.append("additional-constraints-require-native-admission")
            if policy is None:
                blocked.append("capacity-reservation-evidence-missing")
            else:
                blocked.extend(policy["reasons"])
                if policy[pool] < 1:
                    blocked.append("reserved-pool-capacity-exhausted")
            if blocked:
                excluded.append({"machine": machine["id"], "reasons": blocked})
            else:
                candidates.append(machine["id"])
        if not candidates:
            reasons.append("no-eligible-reserved-capacity")
        if reasons:
            result["held"].append({"task": task["id"], "reasons": list(dict.fromkeys(reasons)),
                                   "excluded": excluded})
            continue
        selected = min(candidates, key=lambda name: (-budgets[name][pool], name))
        budgets[selected][pool] -= 1
        if owner is not None:
            proposed_owners.add(owner_key(owner))
        result["proposals"].append({
            "rank": len(result["proposals"]) + 1, "task": task["id"], "requestId": task["requestId"],
            "priority": task["priority"], "role": task["role"], "kind": task["kind"],
            "machine": selected, "pool": "review" if pool == "reviewSlotsRemaining" else "general",
            "nextStep": {
                "owner": "agent-dispatch", "operation": "resume-task" if dormant else "claim-task",
                "expectedStatus": task["status"], "expectedGeneration": task["generation"],
                "retainedWorktree": owner, "expectedHead": task["ownerSessionId"],
                "expectedHeadRevision": head_revision,
            },
            "sourceRef": task["sourceRef"],
        })
    return result
