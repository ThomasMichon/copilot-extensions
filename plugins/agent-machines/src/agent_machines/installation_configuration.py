"""Provider-owned Configuration projection of the existing required repair flow."""

from __future__ import annotations

import json
import sys

from . import discover
from .installation_repair import repair
from .installation_repair_admission import RepairAdmissionError

PROGRESS_ID = "required-readiness-progress"


def entry(outcome: dict) -> dict:
    observation = outcome.get("observedAfter") or outcome["observedBefore"]
    installer = outcome.get("installer")
    state = (
        "restart-required" if outcome["restartRequired"] else
        "repair-failed" if installer and (
            installer.get("status") != "exited" or installer.get("exitCode") != 0
        ) else
        "blocked" if outcome["blockedBy"] else observation["state"]
    )
    module = outcome["module"]
    body = (
        f"Module: {module}\nClassification: {outcome['classification']}\n"
        f"Readiness: {observation['state']}\nReason: {observation['reason']}\n"
        f"Blocked by: {', '.join(outcome['blockedBy']) or 'none'}\n"
        f"Pending restart: {outcome['restartRequired']} ({outcome['restart']})\n"
        f"Installer outcome: {json.dumps(installer) if installer else 'not invoked'}\n"
        "Installation readiness is not authentication or first-task admission.\n"
        "Repair uses only the existing owner's declared actions. Configuration, "
        "unsupported owners and restart obligations may require their separate flows."
    )
    return {
        "id": module, "title": module.split("::")[-1], "state": state,
        "classification": outcome["classification"],
        "card": {"title": module, "body": body},
    }


def projection(result: dict, project: str) -> dict:
    pending = [
        row for row in result["restarts"]["pending"] if row["module"] in result["selected"]
    ]
    complete = not result["interrupted"] and not result["selectionRefusal"]
    ready = complete and result["requiredSatisfied"] and not pending
    return {
        "entries": [entry(outcome) for outcome in result["outcomes"]],
        "summary": {
            "status": "ready" if ready else "incomplete",
            "project": project, "plan_digest": result["planDigest"],
            "repairable": str(bool(complete and not ready and not pending and result["selected"])).lower(),
            "required_satisfied": ready,
            "restart_guidance": (
                "Complete the listed restarts and use the native explicit confirmation flow."
                if pending else "No pending restart was reported."
            ),
        },
    }


def command(args) -> int:
    def emit(kind, **fields):
        print(json.dumps({"type": kind, **fields}), flush=True)

    if args.stream and args.apply or args.progress and not args.apply:
        print("agent-machines: list streaming is read-only; action progress requires apply.", file=sys.stderr)
        return 1
    if args.stream:
        emit("summary", summary={"status": "checking", "repairable": "false"})
        emit("row", entry={
            "id": PROGRESS_ID, "title": "Checking required applications", "state": "checking",
        })

    def observed(outcomes):
        for outcome in outcomes:
            row = entry(outcome)
            if args.progress:
                phase = (
                    "Checking" if outcome["observedBefore"]["reason"] == "readiness-not-observed" else
                    "Repairing" if outcome["attempted"] and outcome["observedAfter"] is None else "Observed"
                )
                emit("progress", msg=f"{phase} {row['title']}: {row['state']}")
            else:
                emit("delta", entry=row)

    try:
        selected = discover.resolve_registered_repo(args.project)
        if selected is None or not selected[1].is_dir():
            raise RepairAdmissionError("selected project has no available canonical repository")
        result = repair(
            selected[1], required=True, apply=args.apply,
            expected_plan_digest=args.expected_plan_digest,
            on_observation=observed if args.stream or args.progress else None,
        )
        value = projection(result, args.project)
    except (RepairAdmissionError, OSError, ValueError) as error:
        if args.stream or args.progress:
            emit("error", message=f"Application readiness unavailable: {error}")
        else:
            print(f"agent-machines: {error}", file=sys.stderr)
        return 1
    if args.stream:
        for row in value["entries"]:
            emit("delta", entry=row)
        emit("removed", id=PROGRESS_ID)
        emit("summary", summary=value["summary"])
        if result["interrupted"] or result["selectionRefusal"]:
            emit("error", message="Required readiness was interrupted or its selection changed; refresh.")
        else:
            emit("done")
    elif args.progress:
        ok = not result["interrupted"] and not result["selectionRefusal"] and result["status"] == "ready"
        emit(
            "done" if ok else "error",
            message=(
                "Required repair completed; independent owner readiness passed."
                if ok else
                "Required repair remains incomplete; refresh and inspect owner or restart obligations before retry."
            ),
        )
    else:
        print(json.dumps({**value, "repair": result}, sort_keys=True))
    if result["interrupted"]:
        return 130
    if result["selectionRefusal"]:
        return 1
    return 0 if not args.apply or result["status"] == "ready" else 2
