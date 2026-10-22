"""Coordinator-owned installation provenance, not execution authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .loop_governance import LoopGovernance
from .queue_records import TaskError


@dataclass(frozen=True)
class ExecutionInstallation:
    identity: str
    baseline: str


def resolve_execution_installation(governance: LoopGovernance) -> ExecutionInstallation:
    """Require the canonical receipt validator; environment labels are not proof."""
    result = governance.recheck("control:resolve-execution-scope")
    if result is None or result.get("status") != "ready":
        raise TaskError("execution scope requires current installation governance")
    baseline = result.get("baseline")
    if (
        not isinstance(baseline, dict)
        or baseline.get("schema") != "copilot-extensions.installation-loop-baseline"
        or type(baseline.get("version")) is not int
        or baseline["version"] != 1
        or baseline.get("pluginId") != "agent-dispatch"
        or baseline.get("actualMode") != "namespaced"
    ):
        raise TaskError("execution scope requires a qualified namespaced installation")
    for name in ("context", "marketplaceId"):
        if not isinstance(baseline.get(name), str) or not baseline[name].strip():
            raise TaskError(f"execution installation baseline omitted {name}")
    for name in ("namespaceGeneration", "installGeneration", "activationGeneration"):
        if type(baseline.get(name)) is not int or baseline[name] < 1:
            raise TaskError(f"execution installation baseline omitted current {name}")
    stable = json.dumps(
        [baseline["context"], baseline["marketplaceId"], baseline["pluginId"]],
        separators=(",", ":"),
    )
    return ExecutionInstallation(
        "installation:" + hashlib.sha256(stable.encode()).hexdigest(),
        json.dumps(baseline, sort_keys=True, separators=(",", ":")),
    )
