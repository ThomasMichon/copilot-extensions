"""Compatibility normalization for reciprocal presentation rows."""

from __future__ import annotations

from typing import Any

_SUMMARY_STATES = frozenset({
    "bound-here",
    "controlled-elsewhere",
    "handed-off",
    "terminal",
    "ambiguous",
    "unbound",
})
_BINDING_STATES = frozenset({
    "bound-here",
    "handed-off",
    "terminal",
    "unbound",
    "unknown",
})
_CONTROL_STATES = frozenset({
    "none",
    "released",
    "controlled-local",
    "controlled-remote",
    "terminal",
    "ambiguous",
})


def _valid_action(
    action: object,
    control_state: str,
    control: dict[str, Any],
) -> bool:
    if not isinstance(action, dict) or action.get("kind") != "navigate-worktree":
        return False
    scope = action.get("scope")
    if scope not in {"local", "remote"}:
        return False
    if control_state != f"controlled-{scope}":
        return False
    target = action.get("target")
    return bool(
        isinstance(target, dict)
        and target == control.get("target")
        and isinstance(target.get("project"), str)
        and target.get("project")
        and isinstance(target.get("worktree_id"), str)
        and target.get("worktree_id")
        and isinstance(target.get("machine"), str)
        and target.get("machine")
        and (
            target.get("session_id") is None
            or isinstance(target.get("session_id"), str)
        )
    )


def _expected_state(binding_state: str, control_state: str) -> str:
    if binding_state == "unknown" or control_state == "ambiguous":
        return "ambiguous"
    if binding_state == "handed-off":
        return "handed-off"
    if control_state in {"controlled-local", "controlled-remote"}:
        return "controlled-elsewhere"
    if binding_state == "bound-here":
        return "bound-here"
    if binding_state == "terminal" or control_state == "terminal":
        return "terminal"
    return "unbound"


def normalize(
    value: object,
    *,
    has_bound_session: bool,
    has_controllers: bool,
) -> dict[str, Any]:
    """Normalize the additive engine field across current and legacy rows."""
    if isinstance(value, dict):
        state = value.get("state")
        binding = value.get("binding")
        control = value.get("control")
        actions = value.get("actions")
        binding_state = binding.get("state") if isinstance(binding, dict) else None
        control_state = control.get("state") if isinstance(control, dict) else None
        valid = (
            value.get("version") == 1
            and state in _SUMMARY_STATES
            and binding_state in _BINDING_STATES
            and control_state in _CONTROL_STATES
            and isinstance(actions, list)
        )
        if valid:
            if state != _expected_state(
                str(binding_state),
                str(control_state),
            ):
                valid = False
            elif state == "ambiguous" and actions:
                valid = False
            elif control_state == "ambiguous" and (
                state != "ambiguous" or actions
            ):
                valid = False
            elif actions and (
                state != "controlled-elsewhere"
                or len(actions) != 1
                or not _valid_action(actions[0], str(control_state), control)
            ):
                valid = False
        if valid:
            return dict(value)
        return {
            "version": 1,
            "state": "ambiguous",
            "binding": {"state": "unknown"},
            "control": {"state": "ambiguous", "reason": "invalid-presentation"},
            "actions": [],
            "compatibility": "invalid",
        }
    if has_controllers:
        return {
            "version": 1,
            "state": "ambiguous",
            "binding": {
                "state": "bound-here" if has_bound_session else "unknown",
            },
            "control": {"state": "ambiguous", "reason": "legacy-engine"},
            "actions": [],
            "compatibility": "legacy",
        }
    return {
        "version": 1,
        "state": "bound-here" if has_bound_session else "unbound",
        "binding": {
            "state": "bound-here" if has_bound_session else "unbound",
        },
        "control": {"state": "none"},
        "actions": [],
        "compatibility": "legacy",
    }


def short_label(value: dict[str, Any]) -> str:
    """Return the compact Picker label for a normalized presentation state."""
    return {
        "bound-here": "BOUND",
        "controlled-elsewhere": "CONTROL",
        "handed-off": "HANDOFF",
        "terminal": "TERM",
        "ambiguous": "AMBIG",
        "unbound": "",
    }.get(str(value.get("state") or ""), "AMBIG")
