"""In-process policy for the native tool-hook transport."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

POLICY_SWITCHES = (
    "AGENT_WORKTREES_STATELESS_GUARD",
    "CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE",
    "ANCHOR_WRITE_GUARD", "ANCHOR_WRITE_GUARD_MODE",
    "PR_SUPERSEDE_GUARD", "PR_SUPERSEDE_GUARD_MODE",
)


def unavailable(boundary: str) -> dict[str, Any]:
    return {"additionalContext": (
        f"[agent-worktrees pre-tool] Boundary: {boundary}. "
        "Effect: this component could not evaluate policy; missing evidence is not "
        "authorization. Independent policy decisions remain in force. "
        "Next: inspect the selected resident and existing repairing-worktrees guidance. "
        "Retry only after this boundary changes; do not repeat the tool solely for this warning."
    )}


def dispatch_pre(
    policy: Any, payload: dict[str, Any], deadline: float | None,
) -> dict[str, Any]:
    metadata = payload.get("_agentWorktrees")
    if not isinstance(metadata, dict) or "nativePolicy" not in metadata:
        return cast(dict[str, Any], policy.pre(payload))
    if (
        type(metadata["nativePolicy"]) is not int or metadata["nativePolicy"] != 1
        or policy.hook_client is None or deadline is None
    ):
        return unavailable("resident policy owner is unavailable")
    return decide_pre(
        payload, load_sibling=policy._module,
        merge=policy.hook_client._merge_pre_decisions, deadline=deadline,
    )


def decide_pre(
    payload: dict[str, Any],
    *,
    load_sibling: Callable[[str], ModuleType | None],
    merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    deadline: float,
) -> dict[str, Any]:
    """Use original guards without a policy worker, runtime ensure or interpreter."""
    metadata = payload.get("_agentWorktrees")
    forwarded = metadata.get("environment") if isinstance(metadata, dict) else None
    if not isinstance(forwarded, dict) or any(
        name not in forwarded or (
            forwarded[name] is not None and not isinstance(forwarded[name], str)
        )
        for name in POLICY_SWITCHES
    ):
        return unavailable("native policy switches are invalid")
    environment = dict(os.environ)
    for name in POLICY_SWITCHES:
        value = forwarded[name]
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    policy = load_sibling("hook_pre_policy.py")
    forms = load_sibling("tool_call_forms.py")
    if policy is None or forms is None:
        return unavailable("native policy helpers are unavailable")
    try:
        events = forms.tool_events(payload, strict=True)
    except Exception:
        return unavailable("native tool batch is invalid")
    combined: dict[str, Any] = {}
    monotonic_deadline = time.monotonic() + max(0.0, deadline - time.time())
    for event in events:
        tool = event.get("toolName") or event.get("tool_name")
        if not isinstance(tool, str) or not tool:
            return merge(combined, unavailable("native tool name is invalid"))
        for name in (
            "statelessness_guard.py", "cross_repo_guard.py",
            "anchor_write_guard.py", "pr_supersede_guard.py",
        ):
            module = load_sibling(name)
            if module is None or not callable(getattr(module, "decide", None)):
                combined = merge(combined, unavailable(f"{name} unavailable"))
                continue
            try:
                # The original PR guard directly owns its bounded gh observation.
                # Do not route through the standalone policy-process/SDK launcher.
                kwargs = {"deadline": monotonic_deadline} if name in {
                    "cross_repo_guard.py", "pr_supersede_guard.py",
                } else {}
                decision = module.decide(
                    event, home=Path.home(), env=environment, **kwargs,
                )
                if not policy.valid_decision(decision):
                    raise ValueError("invalid guard response")
            except Exception:
                decision = unavailable(f"{name} evaluation failed")
            if decision:
                combined = merge(combined, decision)
                if combined.get("permissionDecision") == "deny":
                    return combined
    return combined
