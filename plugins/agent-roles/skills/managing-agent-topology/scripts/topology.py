#!/usr/bin/env python3
"""Offline topology reports. Never writes, contacts, launches or claims anything."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from topology_contract import (
    ContractError, TERMINAL_EFFECTS, owner_key, read_json, timestamp, validate,
)

LIVE_STATES = {"running", "idle", "waiting"}


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

    def fresh(self, observation: dict[str, Any] | None) -> bool:
        if observation is None:
            return False
        age = (self.at - timestamp(observation["observedAt"])).total_seconds()
        max_age: int = self.document["observationMaxAgeSeconds"]
        return 0 <= age <= max_age

    def custody_holds(self) -> list[str]:
        observation = self.observations["dispatcher"]
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
        if any(item["assignmentId"] is None for item in self.observations["owners"]):
            holds.append("unmapped-native-owners-require-reconciliation")
        return holds

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

    def coverage(self, role: dict[str, Any]) -> dict[str, Any]:
        qualified = []
        unqualified = []
        counted = set()
        for assignment in self.assignments:
            if assignment["role"] != role["id"] or assignment["state"] in {"completed", "released"}:
                continue
            reasons = self.policy_reasons(self.machines[assignment["machine"]], role, assignment["kind"])
            if assignment["state"] != "active":
                reasons.append("assignment-not-active")
            owner = assignment["nativeWorktree"]
            observation = self.owners.get(owner_key(owner)) if owner else None
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
                unqualified.append({"assignment": assignment["id"], "reasons": reasons})
            else:
                assert observation is not None
                counted.add(owner_key(owner))
                qualified.append({"assignment": assignment["id"], "owner": owner, "head": observation["head"]})
        return {
            "role": role["id"], "meaning": role["meaning"], "scope": role["scope"],
            "minimum": role["minimum"], "qualifiedCount": len(qualified),
            "shortfall": max(0, role["minimum"] - len(qualified)),
            "qualified": qualified, "unqualified": unqualified,
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
            "unmappedOwners": [
                item for item in self.observations["owners"] if item["assignmentId"] is None
            ],
            "advisoryOnly": True, "shortfallIsLaunchAuthority": False,
        }

    def assignment(self, request_id: str) -> dict[str, Any] | None:
        return next((item for item in self.assignments if item["requestId"] == request_id), None)

    def route(self, request_id: str) -> dict[str, Any]:
        assignment = self.assignment(request_id)
        if assignment is None:
            return {"status": "unassigned", "requestId": request_id, "target": None,
                    "reason": "persist-a-separate-assignment-for-unrelated-work", "advisoryOnly": True}
        holds = self.custody_holds()
        if assignment["state"] in {"completed", "released"}:
            holds.append("assignment-terminal-reconcile-with-owner-before-reopening")
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
            "dispatcher": self.document["dispatcher"]["owner"],
            "topologyRevision": self.document["revision"],
            "advisoryOnly": True, "effectAuthorityGranted": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "overview", "placement", "route", "seed"])
    parser.add_argument("document", type=Path)
    parser.add_argument("--at", help="ISO timestamp with timezone; default: current UTC time")
    parser.add_argument("--role")
    parser.add_argument("--kind", choices=["standing", "temporary"], default="standing")
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    try:
        document = validate(read_json(args.document))
        at = timestamp(args.at) if args.at else datetime.now(timezone.utc)
        topology = Topology(document, at)
        if args.command == "validate":
            result = {"valid": True, "version": document["version"], "revision": document["revision"]}
        elif args.command == "overview":
            result = topology.overview()
        elif args.command == "placement":
            if not args.role:
                raise ContractError("placement requires --role")
            result = topology.placement(args.role, args.kind)
        elif args.command == "route":
            if not args.request:
                raise ContractError("route requires --request")
            result = topology.route(args.request)
        else:
            if not args.request:
                raise ContractError("seed requires --request")
            result = topology.seed(args.request)
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
