#!/usr/bin/env python3
"""Offline topology reports. Never writes, contacts, launches or claims anything."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from topology_contract import (
    ContractError, TERMINAL_EFFECTS, content_revision, owner_key, read_json,
    timestamp, validate, validate_materialization,
)
from topology_dispatch import (
    LIVE_STATES, RETAINING_STATES, adopted_record, flow, intent_lane, lane, linked, native_owner,
    proposed_record,
)


class Topology:
    def __init__(self, document: dict[str, Any], at: datetime):
        self.document = document
        self.at = at
        self.machines = {item["id"]: item for item in document["machines"]}
        self.roles = {item["id"]: item for item in document["roles"]}
        self.assignments = document["assignments"]
        self.observations = document["observations"]
        self.owners = {owner_key(item["owner"]): item for item in self.observations["owners"]}
        self.loads = {item["machine"]: item for item in self.observations["machines"]}
        self.dispatch = {item["task"]["id"]: item for item in self.observations.get("dispatch", [])}

    def fresh(self, observation: dict[str, Any] | None) -> bool:
        if observation is None:
            return False
        age = (self.at - timestamp(observation["observedAt"])).total_seconds()
        max_age: int = self.document["observationMaxAgeSeconds"]
        return 0 <= age <= max_age

    def custody_holds(self) -> list[str]:
        if self.document["dispatcher"] is None:
            return ["dispatcher-not-appointed"]
        observation = self.observations["dispatcher"]
        if observation is None:
            return ["dispatcher-custody-unobserved"]
        holds = []
        if not self.fresh(observation):
            holds.append("dispatcher-custody-stale-or-future")
        if observation["custody"] != "exclusive":
            holds.append("dispatcher-custody-not-exclusive")
        if observation["owner"] != self.document["dispatcher"]["owner"]:
            holds.append("dispatcher-owner-conflict")
        return holds

    @staticmethod
    def pending(assignment: dict[str, Any]) -> list[dict[str, Any]]:
        return [effect for effect in assignment["effects"] if effect["state"] not in TERMINAL_EFFECTS]

    def discovery_holds(self) -> list[str]:
        observation = self.observations["discovery"]
        holds = []
        if not self.fresh(observation) or not observation["complete"]:
            holds.append("discovery-incomplete-stale-or-future")
        if self.unmapped_owners():
            holds.append("unmapped-native-owners-require-reconciliation")
        if self.unmapped_tasks():
            holds.append("unmapped-native-standing-charters-require-reconciliation")
        return holds

    def unmapped_owners(self) -> list[dict[str, Any]]:
        mapped = {
            owner_key(owner) for assignment in self.assignments
            if linked(assignment, self.observations)
            if (owner := native_owner(self.dispatch.get(assignment["nativeTask"]))) is not None
        }
        return [item for item in self.observations["owners"]
                if item["assignmentId"] is None and owner_key(item["owner"]) not in mapped]

    def unmapped_tasks(self) -> list[dict[str, Any]]:
        mapped = {item["nativeTask"] for item in self.assignments if linked(item, self.observations)}
        return [snapshot for task_id, snapshot in self.dispatch.items() if task_id not in mapped]

    def native_lane(self, assignment: dict[str, Any]) -> dict[str, Any]:
        result = lane(
            assignment, self.dispatch.get(assignment["nativeTask"]), self.fresh,
            lambda owner: self.owners.get(owner_key(owner)),
            None if assignment["state"] in {"completed", "released"}
            else self.materialize(assignment["requestId"]),
        )
        role = self.roles[assignment["role"]]
        result["reasons"].extend(self.policy_reasons(self.machines[assignment["machine"]], role, assignment["kind"]))
        observation = self.owners.get(owner_key(result["owner"])) if result["owner"] else None
        if observation is not None and (
            observation["role"] not in (None, role["id"])
            or observation["scope"] not in (None, role["scope"])
        ):
            result["reasons"].append("role-or-scope-mismatch")
        if self.pending(assignment):
            result["reasons"].append("effect-outcome-unsettled")
        if result["status"] == "accepted" and result["reasons"]:
            result["status"] = "held"
        return result

    def policy_reasons(self, machine: dict[str, Any], role: dict[str, Any], kind: str) -> list[str]:
        reasons = []
        if role["id"] not in machine["allowedRoles"]:
            reasons.append("role-disallowed")
        if kind not in machine["allowedKinds"]:
            reasons.append("assignment-kind-disallowed")
        if set(role["requiredCapabilities"]) - set(machine["capabilities"]):
            reasons.append("required-capability-missing")
        return reasons

    def placement(self, role_id: str, kind: str) -> dict[str, Any]:
        if role_id not in self.roles:
            raise ContractError("unknown placement role")
        role = self.roles[role_id]
        candidates = []
        excluded = []
        for machine in self.machines.values():
            reasons = self.policy_reasons(machine, role, kind)
            load = self.loads.get(machine["id"])
            capacity = machine["capacitySlots"]
            used = load["usedSlots"] if load else None
            if not self.fresh(load):
                reasons.append("load-stale-missing-or-future")
            if load is None or load["state"] != "ready":
                reasons.append("machine-not-ready")
            if capacity is None or used is None:
                reasons.append("capacity-or-load-unknown")
            elif capacity == 0 or used >= capacity:
                reasons.append("capacity-exhausted")
            if any(
                item["machine"] == machine["id"]
                and any(effect["kind"] == "allocate" for effect in self.pending(item))
                for item in self.assignments
            ):
                reasons.append("allocation-outcome-unsettled")
            constraints = machine["constraints"] + role["constraints"]
            if constraints:
                reasons.append("additional-constraints-require-native-admission")
            if reasons:
                excluded.append({"machine": machine["id"], "reasons": reasons, "constraints": constraints})
            else:
                candidates.append({
                    "machine": machine["id"], "usedSlots": used,
                    "capacitySlots": capacity, "utilization": used / capacity,
                })
        candidates.sort(key=lambda item: (item["utilization"], item["machine"]))
        return {
            "role": role_id, "kind": kind, "candidates": candidates, "excluded": excluded,
            "holds": self.custody_holds() + self.discovery_holds(),
            "advisoryOnly": True, "nativeAdmissionRequired": True,
        }

    def signals(self, observation: dict[str, Any] | None) -> dict[str, Any]:
        """Report presence and progress beside coverage; neither qualifies a lane."""
        liveness: dict[str, Any] = {"state": "unobserved", "fresh": False, "observedAt": None, "sourceRef": None}
        progress: dict[str, Any] = {
            "state": "unknown", "workRef": None, "blockingOwner": None, "observedAt": None,
            "sourceRef": None, "sinceAdvancedSeconds": None, "reasons": [],
        }
        if observation is not None:
            liveness.update(
                state=observation["state"], fresh=self.fresh(observation),
                observedAt=observation["observedAt"], sourceRef=observation["sourceRef"],
            )
        evidence = observation.get("progress") if observation is not None else None
        if evidence is None:
            progress["reasons"].append("progress-evidence-missing")
        else:
            progress.update({key: evidence[key] for key in ("workRef", "blockingOwner", "observedAt", "sourceRef")})
            if not self.fresh(evidence):
                progress["reasons"].append("progress-evidence-stale-or-future")
            else:
                progress["state"] = evidence["state"]
                if evidence["lastAdvancedAt"] is not None:
                    progress["sinceAdvancedSeconds"] = int(
                        (self.at - timestamp(evidence["lastAdvancedAt"])).total_seconds()
                    )
        return {"liveness": liveness, "progress": progress}

    def coverage(self, role: dict[str, Any]) -> dict[str, Any]:
        qualified = []
        unqualified = []
        counted = set()
        native_counts: dict[str, int] = {}
        progress_counts: Counter[str] = Counter()
        retaining_count = 0
        for assignment in self.assignments:
            if assignment["role"] != role["id"] or assignment["state"] in {"completed", "released"}:
                continue
            if linked(assignment, self.observations):
                native = self.native_lane(assignment)
                owner = native["owner"]
                signals = self.signals(self.owners.get(owner_key(owner)) if owner else None)
                progress_counts[signals["progress"]["state"]] += 1
                if owner and owner_key(owner) in counted:
                    native["reasons"].append("owner-already-counted")
                    if native["status"] == "accepted":
                        native["status"] = "held"
                status = native["status"]
                native_counts[status] = native_counts.get(status, 0) + 1
                retaining_count += status in RETAINING_STATES
                if status == "accepted":
                    counted.add(owner_key(owner))
                    qualified.append({
                        "assignment": assignment["id"], "owner": owner, "head": native["head"],
                        "headRevision": native["headRevision"], "nativeTask": native["nativeTask"],
                        "nativeStatus": status, **signals,
                    })
                else:
                    unqualified.append({
                        "assignment": assignment["id"], "reasons": native["reasons"],
                        "nativeTask": native["nativeTask"], "nativeStatus": status,
                        "retainedOwner": owner, "holdReason": native["holdReason"], **signals,
                    })
                continue
            reasons = self.policy_reasons(self.machines[assignment["machine"]], role, assignment["kind"])
            if assignment["state"] != "active":
                reasons.append("assignment-not-active")
            owner = assignment["nativeWorktree"]
            observation = self.owners.get(owner_key(owner)) if owner else None
            signals = self.signals(observation)
            progress_counts[signals["progress"]["state"]] += 1
            if not self.fresh(observation):
                reasons.append("owner-evidence-stale-missing-or-future")
            if observation is None or observation["state"] not in LIVE_STATES or not observation["head"]:
                reasons.append("current-native-head-unqualified")
            if observation is not None:
                if observation["assignmentId"] != assignment["id"]:
                    reasons.append("assignment-not-accepted-by-owner")
                if observation["role"] != role["id"] or observation["scope"] != role["scope"]:
                    reasons.append("role-or-scope-mismatch")
                if (
                    observation["acceptedCharterRevision"] != assignment["charter"]["revision"]
                    or not observation["acceptanceRef"]
                ):
                    reasons.append("charter-revision-not-accepted")
            if self.pending(assignment):
                reasons.append("effect-outcome-unsettled")
            if owner and owner_key(owner) in counted:
                reasons.append("owner-already-counted")
            if reasons:
                unqualified.append({"assignment": assignment["id"], "reasons": reasons, **signals})
            else:
                assert observation is not None
                counted.add(owner_key(owner))
                qualified.append({
                    "assignment": assignment["id"], "owner": owner, "head": observation["head"], **signals,
                })
        return {
            "role": role["id"], "meaning": role["meaning"], "scope": role["scope"],
            "minimum": role["minimum"], "qualifiedCount": len(qualified),
            "shortfall": max(0, role["minimum"] - len(qualified)),
            "qualified": qualified, "unqualified": unqualified,
            "nativeStatusCounts": native_counts, "ownerRetainingCount": retaining_count,
            "progressCounts": dict(progress_counts),
        }

    def overview(self) -> dict[str, Any]:
        return {
            "topology": self.document["id"], "revision": self.document["revision"],
            "evaluatedAt": self.at.isoformat(),
            "holds": self.custody_holds() + self.discovery_holds(),
            "coverage": [self.coverage(role) for role in self.roles.values()],
            "pendingEffects": [
                {"assignment": item["id"], "effect": effect}
                for item in self.assignments for effect in self.pending(item)
            ],
            "unmappedOwners": self.unmapped_owners(),
            "unmappedNativeTasks": self.unmapped_tasks(),
            "advisoryOnly": True, "shortfallIsLaunchAuthority": False,
        }

    def intent(self) -> dict[str, Any]:
        """Compare desired standing intent with proposed and adopted native records."""
        supplied = "dispatch" in self.observations
        lanes = []
        for assignment in self.assignments:
            if assignment["kind"] != "standing":
                continue
            terminal = assignment["state"] in {"completed", "released"}
            materialized = None if terminal else self.materialize(assignment["requestId"])
            desired = {
                "assignment": assignment["id"],
                **{name: assignment[name] for name in ("requestId", "role", "machine", "state", "nativeTask")},
                "charterRevision": assignment["charter"]["revision"],
                "sourceRevision": materialized["sourceRevision"] if materialized else None,
                "materializationRevision": materialized["materializationRevision"] if materialized else None,
            }
            snapshot = self.dispatch.get(assignment["nativeTask"]) if supplied else None
            lanes.append(intent_lane(desired, snapshot, self.fresh(snapshot), supplied))
        unmatched = [{
            "proposed": proposed_record(snapshot, self.fresh(snapshot)),
            "adopted": adopted_record(snapshot),
            "reasons": ["proposed-without-desired-intent"],
        } for snapshot in self.unmapped_tasks()]
        return {
            "topology": self.document["id"], "revision": self.document["revision"],
            "evaluatedAt": self.at.isoformat(), "dispatchEvidenceSupplied": supplied,
            "lanes": lanes, "stateCounts": dict(Counter(item["state"] for item in lanes)),
            "proposedWithoutDesired": unmatched,
            "advisoryOnly": True, "effectAuthorityGranted": False, "qualifiesCoverage": False,
        }

    def assignment(self, request_id: str) -> dict[str, Any] | None:
        return next((item for item in self.assignments if item["requestId"] == request_id), None)

    def flow(self, request_id: str) -> dict[str, Any]:
        """Plan one native-owner step from saved evidence without executing it."""
        assignment = self.assignment(request_id)
        holds = self.custody_holds()
        if assignment is not None:
            holds.extend(self.policy_reasons(
                self.machines[assignment["machine"]], self.roles[assignment["role"]], assignment["kind"],
            ))
        result = flow(
            assignment, self.dispatch.get(assignment["nativeTask"]) if assignment else None,
            self.fresh, lambda owner: self.owners.get(owner_key(owner)),
            self.materialize(request_id) if assignment and assignment["state"] not in {"completed", "released"} else None,
            self.seed(request_id) if assignment else None, holds,
            "dispatch" in self.observations,
        )
        if result["phase"] in {"persist-assignment", "persist-allocation", "allocate", "reserve", "reconcile"}:
            result["holds"].extend(self.discovery_holds())
        if assignment and result["phase"] in {"persist-allocation", "allocate"}:
            placement = self.placement(assignment["role"], assignment["kind"])
            foreign_allocation = any(
                item["id"] != assignment["id"] and item["machine"] == assignment["machine"]
                and any(effect["kind"] == "allocate" for effect in self.pending(item))
                for item in self.assignments
            )
            result["holds"].extend(
                reason for item in placement["excluded"] if item["machine"] == assignment["machine"]
                for reason in item["reasons"] if reason != "allocation-outcome-unsettled" or foreign_allocation
            )
        if result["holds"]:
            result.update(phase="held", nextStep=None)
        return {
            "topology": self.document["id"], "revision": self.document["revision"],
            "evaluatedAt": self.at.isoformat(), "requestId": request_id, **result,
        }

    def route(self, request_id: str) -> dict[str, Any]:
        assignment = self.assignment(request_id)
        if assignment is None:
            return {"status": "unassigned", "requestId": request_id, "target": None,
                    "reason": "persist-a-separate-assignment-for-unrelated-work", "advisoryOnly": True}
        holds = self.custody_holds()
        if assignment["state"] in {"completed", "released"}:
            holds.append("assignment-terminal-reconcile-with-owner-before-reopening")
        if linked(assignment, self.observations):
            native = self.native_lane(assignment)
            holds.extend(native["reasons"])
            return {
                "status": "held" if holds else "resolved", "assignment": assignment["id"],
                "requestId": request_id, "retainedWorktree": native["owner"],
                "target": None if holds else {
                    "owner": native["owner"], "head": native["head"], "headRevision": native["headRevision"],
                },
                "nativeTask": native["nativeTask"], "nativeStatus": native["status"],
                "holdReason": native["holdReason"], "holds": holds,
                "advisoryOnly": True, "nativeHeadRecheckRequired": True,
            }
        owner = assignment["nativeWorktree"]
        observation = self.owners.get(owner_key(owner)) if owner else None
        if owner is None:
            holds.append("native-worktree-not-yet-bound")
        if not self.fresh(observation):
            holds.append("owner-evidence-stale-missing-or-future")
        if observation is None or observation["state"] not in LIVE_STATES or not observation["head"]:
            holds.append("current-native-head-unqualified")
        if observation is not None and observation["assignmentId"] != assignment["id"]:
            holds.append("assignment-not-accepted-by-owner")
        if self.pending(assignment):
            holds.append("effect-outcome-unsettled-no-replay")
        target = None
        if not holds:
            assert observation is not None
            target = {"owner": owner, "head": observation["head"]}
        return {
            "status": "held" if holds else "resolved", "assignment": assignment["id"],
            "requestId": request_id, "retainedWorktree": owner,
            "target": target,
            "holds": holds, "advisoryOnly": True, "nativeHeadRecheckRequired": True,
        }

    def seed(self, request_id: str) -> dict[str, Any]:
        assignment = self.assignment(request_id)
        if assignment is None:
            raise ContractError("seed requires a persisted assignment")
        return {
            "assignment": assignment,
            "role": self.roles[assignment["role"]],
            "machine": self.machines[assignment["machine"]],
            "dispatcher": self.document["dispatcher"]["owner"] if self.document["dispatcher"] else None,
            "holds": self.custody_holds(),
            "topologyRevision": self.document["revision"],
            "advisoryOnly": True, "effectAuthorityGranted": False,
        }

    def materialize(self, request_id: str) -> dict[str, Any]:
        assignment = self.assignment(request_id)
        if assignment is None or assignment["state"] in {"completed", "released"}:
            raise ContractError("materialization requires a nonterminal persisted assignment")
        source = {
            "topology": self.document["id"], "topologyRevision": self.document["revision"],
            **{name: assignment[name] for name in (
                "id", "requestId", "creationKey", "kind", "role", "machine",
            )},
        }
        effective = copy.deepcopy({
            "charter": assignment["charter"],
            "role": self.roles[assignment["role"]],
            "machine": self.machines[assignment["machine"]],
            "dispatcher": self.document["dispatcher"],
        })
        return validate_materialization({
            "schema": "agent-roles.charter-materialization", "version": 1,
            "source": source, "effective": effective,
            "sourceRevision": content_revision({"source": source, "effective": effective}),
            "materializationRevision": content_revision(effective),
            "advisoryOnly": True, "effectAuthorityGranted": False,
        })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[
        "validate", "overview", "intent", "placement", "flow", "route", "seed", "materialize",
        "inspect-materialization",
    ])
    parser.add_argument("document", type=Path)
    parser.add_argument("--at", help="ISO timestamp with timezone; default: current UTC time")
    parser.add_argument("--role")
    parser.add_argument("--kind", choices=["standing", "temporary"], default="standing")
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    try:
        document = read_json(args.document)
        if args.command == "inspect-materialization":
            print(json.dumps(validate_materialization(document), indent=2))
            return 0
        document = validate(document)
        at = timestamp(args.at) if args.at else datetime.now(timezone.utc)
        topology = Topology(document, at)
        if args.command == "validate":
            result = {"valid": True, "version": document["version"], "revision": document["revision"]}
        elif args.command == "overview":
            result = topology.overview()
        elif args.command == "intent":
            result = topology.intent()
        elif args.command == "flow":
            if not args.request:
                raise ContractError("flow requires --request")
            result = topology.flow(args.request)
        elif args.command == "placement":
            if not args.role:
                raise ContractError("placement requires --role")
            result = topology.placement(args.role, args.kind)
        elif args.command == "route":
            if not args.request:
                raise ContractError("route requires --request")
            result = topology.route(args.request)
        elif args.command == "seed":
            if not args.request:
                raise ContractError("seed requires --request")
            result = topology.seed(args.request)
        else:
            if not args.request:
                raise ContractError("materialize requires --request")
            result = topology.materialize(args.request)
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
