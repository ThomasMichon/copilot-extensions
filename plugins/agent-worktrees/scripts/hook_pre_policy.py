"""Standalone pre-tool guard evaluation; transport stays caller-owned."""

from __future__ import annotations

import os
import json
from pathlib import Path
import time


def valid_decision(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) - {
        "permissionDecision",
        "permissionDecisionReason",
        "additionalContext",
    }:
        return False
    if "permissionDecision" in value:
        decision = value["permissionDecision"]
        if not isinstance(decision, str) or decision not in {"allow", "ask", "deny"}:
            return False
    return all(
        isinstance(value[name], str)
        for name in ("permissionDecisionReason", "additionalContext")
        if name in value
    )


def unavailable(boundary: str) -> dict:
    return {"additionalContext": (
        f"[agent-worktrees pre-tool] Boundary: {boundary}. "
        "Effect: this component could not evaluate policy; missing evidence is not authorization. This hook did not execute the tool; independent policy decisions remain in force. Next: inspect the selected installation pre-tool files and existing repairing-worktrees guidance; use its catalog command read-only doctor only if available. Retry: do not repeat the tool solely for this warning; inspect its result first, and retry evaluation only after this boundary changes."
    )}


def deferred_frame(payload):
    initial = payload.get("initial")
    if set(payload) != {"event", "initial"} or not isinstance(initial, dict) or not valid_decision(initial) or not isinstance(payload["event"], dict):
        raise ValueError("invalid deferred policy frame")
    return payload["event"], initial


def hold_pending(error):
    pending = getattr(error, "pretool_decision", None)
    if isinstance(pending, dict) and valid_decision(pending):
        import sys
        from threading import Event

        sys.stdout.write(json.dumps({"_ownedCleanupPending": pending}, separators=(",", ":")))
        sys.stdout.flush()
        Event().wait()


def execute_owned_observations(payload, raw, load_sibling, merge_decisions):
    """Give legacy PR observations the same enclosing settlement owner."""
    guard = load_sibling("pr_supersede_guard.py")
    needs = getattr(guard, "needs_observations", None)
    if not callable(needs) or not needs(payload):
        return None
    deadline = time.monotonic() + 23.0
    initial = decide(
        payload, home=Path.home(), load_sibling=load_sibling, merge_decisions=merge_decisions,
        budget_seconds=25, observe_pr=False, settlement_deadline=deadline,
    )
    if initial.get("permissionDecision") == "deny":
        print(json.dumps(initial, separators=(",", ":")), end="")
        return 0
    try:
        __import__("agent_procutil.owned_budget", fromlist=["OwnedExecutionBudget"])
    except Exception:
        import sys

        sys.stdout.write(json.dumps(merge_decisions(initial, unavailable("supersede SDK unavailable"))))
        return 0
    worker = load_sibling("hook_policy_process.py")
    if callable(getattr(worker, "execute", None)):
        return worker.execute(
            raw.encode("utf-8"), legacy=True, initial_decision=initial, settlement_deadline=deadline,
        )
    print(json.dumps(merge_decisions(initial, unavailable("supersede worker unavailable"))), end="")
    return 0


def decide(
    payload: dict,
    *,
    home: Path,
    load_sibling,
    merge_decisions,
    budget_seconds: float,
    require_policy: bool = False,
    observe_pr: bool = True,
    only_observations: bool = False,
    initial_decision: dict | None = None,
    settlement_deadline: float | None = None,
) -> dict:
    combined: dict = dict(initial_decision or {})
    contextual = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    if contextual:
        helper = load_sibling("registry_root.py")
        if helper is None:
            return merge_decisions(combined, unavailable("installation-context helper unavailable"))
        try:
            helper.resolve_registry_root(
                legacy_root=home / ".agent-worktrees", environment=os.environ,
            )
        except Exception:
            return merge_decisions(combined, unavailable("installation-context validation failed"))
    deadline = time.monotonic() + budget_seconds - 2.0
    if settlement_deadline is not None:
        deadline = min(deadline, settlement_deadline)
    inherited = os.environ.get("AGENT_WORKTREES_PRE_SETTLEMENT_DEADLINE")
    if inherited:
        try:
            import math

            value = float(inherited)
            if math.isfinite(value):
                deadline = min(deadline, value)
        except ValueError:
            pass
    execution_deadline = deadline - 3.0
    for name in (
        "statelessness_guard.py", "cross_repo_guard.py",
        "anchor_write_guard.py", "pr_supersede_guard.py",
    ):
        if (only_observations and name != "pr_supersede_guard.py") or (
            not observe_pr and name == "pr_supersede_guard.py"
        ):
            continue
        module = load_sibling(name)
        if not callable(getattr(module, "decide", None)):
            combined = merge_decisions(combined, unavailable(f"{name} unavailable"))
            continue
        try:
            kwargs = {"deadline": execution_deadline} if name == "cross_repo_guard.py" else {}
            if name == "pr_supersede_guard.py":
                needs = getattr(module, "needs_observations", None)
                if callable(needs) and not needs(payload):
                    continue
                try:
                    from agent_procutil.owned_budget import OwnedExecutionBudget
                except Exception:
                    combined = merge_decisions(combined, unavailable("supersede SDK unavailable"))
                    continue
                kwargs = {"deadline": execution_deadline, "budget": OwnedExecutionBudget(deadline, 3.0)}
            decision = module.decide(payload, home=home, **kwargs)
        except BaseException as error:
            if getattr(module, "owned_cleanup_failure", lambda value: False)(error):
                error.pretool_decision = merge_decisions(
                    combined, unavailable("PR observation cleanup requires enclosing settlement"),
                )
                raise
            if not isinstance(error, Exception):
                raise
            combined = merge_decisions(combined, unavailable(f"{name} evaluation failed"))
            continue
        if not valid_decision(decision):
            combined = merge_decisions(combined, unavailable(f"{name} response invalid"))
            continue
        if isinstance(decision, dict) and decision:
            combined = merge_decisions(combined, decision)
            if combined.get("permissionDecision") == "deny":
                break
    return combined
