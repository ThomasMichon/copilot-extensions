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
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "agent-worktrees installation context cannot be validated"
                ),
            }
        try:
            helper.resolve_registry_root(
                legacy_root=home / ".agent-worktrees",
                environment=os.environ,
            )
        except Exception as error:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"agent-worktrees installation context is invalid: {error}"
                ),
            }
    deadline = time.monotonic() + budget_seconds
    combined: dict = {}
    for name in (
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "anchor_write_guard.py",
        "pr_supersede_guard.py",
    ):
        module = load_sibling(name)
        if require_policy and not callable(getattr(module, "decide", None)):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": "Required agent-worktrees pre-tool policy is unavailable",
            }
        if module is None:
            continue
        try:
            kwargs = {"deadline": deadline - 2.0} if name == "cross_repo_guard.py" else {}
            decision = module.decide(payload, home=home, **kwargs)
        except Exception:
            if require_policy:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Required agent-worktrees pre-tool policy evaluation failed",
                }
            if contextual:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "agent-worktrees installation context changed during policy evaluation"
                    ),
                }
            continue
        if require_policy and not valid_decision(decision):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": "Required agent-worktrees pre-tool policy returned an invalid response",
            }
        if isinstance(decision, dict) and decision:
            combined = merge_decisions(combined, decision)
            if combined.get("permissionDecision") == "deny":
                break
    return combined
