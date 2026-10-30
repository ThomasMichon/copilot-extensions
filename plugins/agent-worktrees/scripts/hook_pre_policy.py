"""Standalone pre-tool guard evaluation; transport stays caller-owned."""

from __future__ import annotations

import os
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


def decide(
    payload: dict,
    *,
    home: Path,
    load_sibling,
    merge_decisions,
    budget_seconds: float,
    require_policy: bool = False,
) -> dict:
    contextual = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    if contextual:
        helper = load_sibling("registry_root.py")
        if helper is None:
            return unavailable("installation-context helper unavailable")
        try:
            helper.resolve_registry_root(
                legacy_root=home / ".agent-worktrees", environment=os.environ,
            )
        except Exception:
            return unavailable("installation-context validation failed")
    deadline = time.monotonic() + budget_seconds
    combined: dict = {}
    for name in (
        "statelessness_guard.py", "cross_repo_guard.py",
        "anchor_write_guard.py", "pr_supersede_guard.py",
    ):
        module = load_sibling(name)
        if not callable(getattr(module, "decide", None)):
            combined = merge_decisions(combined, unavailable(f"{name} unavailable"))
            continue
        try:
            kwargs = {"deadline": deadline - 2.0} if name == "cross_repo_guard.py" else {}
            decision = module.decide(payload, home=home, **kwargs)
        except Exception:
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
