"""In-process policy for the native tool-hook transport."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn, cast

POLICY_SWITCHES = (
    "AGENT_WORKTREES_STATELESS_GUARD",
    "CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE",
    "ANCHOR_WRITE_GUARD", "ANCHOR_WRITE_GUARD_MODE",
    "PR_SUPERSEDE_GUARD", "PR_SUPERSEDE_GUARD_MODE",
)


class _CallerObservationUnavailable(RuntimeError):
    pass


def _unavailable_pr_observation(*_args: object, **_kwargs: object) -> NoReturn:
    raise _CallerObservationUnavailable


def unavailable(boundary: str) -> dict[str, Any]:
    return {"additionalContext": (
        f"[agent-worktrees pre-tool] Boundary: {boundary}. "
        "Effect: this component could not evaluate policy; missing evidence is not "
        "authorization. Independent policy decisions remain in force. "
        "Next: inspect the selected resident and existing repairing-worktrees guidance. "
        "Retry only after this boundary changes; do not repeat the tool solely for this warning."
    )}


def _result_json_size(result: dict[str, Any]) -> int:
    encoded = json.dumps(result)
    # PowerShell 5 reserializes these HTML-sensitive characters as six-byte escapes.
    return len(encoded) + 5 * sum(encoded.count(character) for character in "<>&'")


def _bounded_result(result: dict[str, Any]) -> dict[str, Any]:
    # Reserve envelope space inside the transport's unchanged 64 KiB frame.
    limit = 60 * 1024
    if _result_json_size(result) <= limit:
        return result
    result = dict(result)
    notice = "\n[agent-worktrees pre-tool] Text truncated to fit native response."
    for field in ("additionalContext", "permissionDecisionReason"):
        text = result.get(field)
        if not isinstance(text, str):
            continue
        result[field] = notice
        if _result_json_size(result) > limit:
            continue
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            result[field] = text[:middle] + notice
            if _result_json_size(result) <= limit:
                low = middle
            else:
                high = middle - 1
        result[field] = text[:low] + notice
        break
    return result


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
    remaining = deadline - time.time()
    if remaining <= 0:
        return unavailable("native policy deadline exhausted")
    monotonic_deadline = time.monotonic() + remaining
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
    recovery_boundaries: set[str] = set()

    def recover(boundary: str) -> dict[str, Any]:
        if boundary in recovery_boundaries:
            return {}
        recovery_boundaries.add(boundary)
        return unavailable(boundary)

    for event in events:
        tool = event.get("toolName") or event.get("tool_name")
        if not isinstance(tool, str) or not tool:
            return _bounded_result(merge(combined, recover("native tool name is invalid")))
        for name in (
            "statelessness_guard.py", "cross_repo_guard.py",
            "anchor_write_guard.py", "pr_supersede_guard.py",
        ):
            if time.monotonic() >= monotonic_deadline:
                return _bounded_result(merge(combined, recover("native policy deadline exhausted")))
            module = load_sibling(name)
            if time.monotonic() >= monotonic_deadline:
                return _bounded_result(merge(combined, recover("native policy deadline exhausted")))
            if module is None or not callable(getattr(module, "decide", None)):
                combined = merge(combined, recover(f"{name} unavailable"))
                continue
            try:
                kwargs: dict[str, Any] = {"deadline": monotonic_deadline} if name in {
                    "cross_repo_guard.py", "pr_supersede_guard.py",
                } else {}
                if name == "cross_repo_guard.py":
                    kwargs["caller_elevated"] = (
                        isinstance(metadata, dict) and metadata.get("callerElevated") is True
                    )
                if name == "pr_supersede_guard.py":
                    # Native frames carry no qualified caller GitHub identity or observation owner.
                    kwargs.update(
                        current_login=_unavailable_pr_observation,
                        pr_lookup=_unavailable_pr_observation,
                    )
                decision = module.decide(
                    event, home=Path.home(), env=environment, **kwargs,
                )
                if not policy.valid_decision(decision):
                    raise ValueError("invalid guard response")
            except _CallerObservationUnavailable:
                decision = recover("caller-scoped PR observation is unavailable in native hooks")
            except Exception:
                decision = recover(f"{name} evaluation failed")
            if decision:
                combined = merge(combined, decision)
                if combined.get("permissionDecision") == "deny":
                    return _bounded_result(combined)
    return _bounded_result(combined)
