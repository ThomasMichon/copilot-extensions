"""Normalized reciprocal session/worktree presentation state."""

from __future__ import annotations

from typing import Any

from . import controller_lineage
from . import tracking
from .picker_tui import reciprocal as picker_reciprocal

_ACTIONABLE_CONTROLLER_STATUSES = frozenset({"resolved", "remote"})
_NONBLOCKING_CONTROLLER_STATUSES = frozenset({
    "controller-terminal",
    "ended",
})


def _binding_state(record: tracking.WorktreeRecord) -> dict[str, Any]:
    head_session = record.resolved_head_session
    pending = list(record.pending_handoffs)
    matching_pending = [
        item for item in pending if item.predecessor == head_session
    ]
    if matching_pending:
        latest = matching_pending[-1]
        result: dict[str, Any] = {
            "state": "handed-off",
            "session_id": latest.predecessor,
            "handoff_ordinal": latest.ordinal,
            "handoff_state": latest.state,
            "head_revision": record.head_revision,
        }
        if latest.candidate:
            result["candidate_session_id"] = latest.candidate
        if len(matching_pending) > 1:
            result["ambiguous"] = True
        return result

    if head_session:
        return {
            "state": "bound-here",
            "session_id": head_session,
            "head_revision": record.head_revision,
        }

    if pending:
        latest = pending[-1]
        result = {
            "state": "handed-off",
            "session_id": latest.predecessor,
            "handoff_ordinal": latest.ordinal,
            "handoff_state": latest.state,
            "head_revision": record.head_revision,
        }
        if latest.candidate:
            result["candidate_session_id"] = latest.candidate
        if len(pending) > 1:
            result["ambiguous"] = True
        return result

    transition = record.replayed_head_transition
    entry = (
        record.session_entry(transition.session_id)
        if transition is not None and transition.session_id
        else None
    )
    if entry is None and record.sessions:
        entry = record.sessions[-1]

    if entry is not None and entry.state == "handed-off":
        terminal = controller_lineage.resolve_terminal_session(
            record,
            entry.session_id,
        )
        result: dict[str, Any] = {
            "state": "handed-off",
            "session_id": entry.session_id,
            "head_revision": record.head_revision,
            "terminal_status": terminal.get("status"),
        }
        terminal_session_id = terminal.get("terminal_session_id")
        if isinstance(terminal_session_id, str) and terminal_session_id:
            result["successor_session_id"] = terminal_session_id
        return result

    sessions = list(record.sessions or ())
    if record.status == "finalized" or (
        sessions and all(item.state == "concluded" for item in sessions)
    ):
        return {
            "state": "terminal",
            "head_revision": record.head_revision,
        }
    return {
        "state": "unbound",
        "head_revision": record.head_revision,
    }


def _controller_target(finding: dict[str, object]) -> dict[str, Any] | None:
    project = finding.get("controller_project")
    worktree_id = finding.get("controller_worktree_id")
    if not isinstance(project, str) or not project:
        return None
    if not isinstance(worktree_id, str) or not worktree_id:
        return None
    target: dict[str, Any] = {
        "project": project,
        "worktree_id": worktree_id,
    }
    machine = finding.get("controller_machine")
    if isinstance(machine, str) and machine:
        target["machine"] = machine
    session_id = (
        finding.get("terminal_session_id")
        if finding.get("status") == "resolved"
        else finding.get("remote_session_id")
    )
    if isinstance(session_id, str) and session_id:
        target["session_id"] = session_id
    return target


def _control_state(
    record: tracking.WorktreeRecord,
    findings: list[dict[str, object]],
) -> dict[str, Any]:
    active = [item for item in record.controllers if item.state == "active"]
    if not active:
        return {
            "state": "released" if record.controllers else "none",
            "controller_revision": record.controller_revision,
        }
    if not findings:
        return {
            "state": "ambiguous",
            "reason": "findings-unavailable",
            "controller_revision": record.controller_revision,
        }

    active_revisions = {item.relation_revision for item in active}
    active_findings = [
        item
        for item in findings
        if item.get("relation_state") == "active"
        and item.get("relation_revision") in active_revisions
    ]
    if len(active_findings) != len(active):
        return {
            "state": "ambiguous",
            "reason": "findings-incomplete",
            "controller_revision": record.controller_revision,
        }

    statuses = {str(item.get("status") or "unknown") for item in active_findings}
    blocking = statuses - (
        _ACTIONABLE_CONTROLLER_STATUSES | _NONBLOCKING_CONTROLLER_STATUSES
    )
    if blocking:
        return {
            "state": "ambiguous",
            "reason": sorted(blocking)[0],
            "statuses": sorted(statuses),
            "controller_revision": record.controller_revision,
        }

    actionable = [
        item
        for item in active_findings
        if item.get("status") in _ACTIONABLE_CONTROLLER_STATUSES
    ]
    if not actionable:
        return {
            "state": "terminal",
            "statuses": sorted(statuses),
            "controller_revision": record.controller_revision,
        }

    targets: dict[tuple[str, str, str], tuple[dict[str, Any], dict[str, object]]] = {}
    for finding in actionable:
        target = _controller_target(finding)
        if target is None:
            return {
                "state": "ambiguous",
                "reason": "invalid-target",
                "controller_revision": record.controller_revision,
            }
        key = (
            str(target.get("machine") or ""),
            str(target["project"]),
            str(target["worktree_id"]),
        )
        targets[key] = (target, finding)
    if len(targets) != 1:
        return {
            "state": "ambiguous",
            "reason": "multiple-controller-targets",
            "controller_revision": record.controller_revision,
        }

    target, finding = next(iter(targets.values()))
    scope = "remote" if finding.get("status") == "remote" else "local"
    return {
        "state": f"controlled-{scope}",
        "target": target,
        "relation_revision": finding.get("relation_revision"),
        "head_revision": finding.get("head_revision"),
        "controller_revision": record.controller_revision,
        "action": {
            "kind": "navigate-worktree",
            "scope": scope,
            "target": target,
            "relation_revision": finding.get("relation_revision"),
            "head_revision": finding.get("head_revision"),
        },
    }


def derive(
    record: tracking.WorktreeRecord,
    findings: list[dict[str, object]],
) -> dict[str, Any]:
    """Reduce authoritative binding and controller findings for presentation."""
    binding = _binding_state(record)
    control = _control_state(record, findings)

    if binding.get("ambiguous") or control["state"] == "ambiguous":
        state = "ambiguous"
    elif binding["state"] == "handed-off":
        state = "handed-off"
    elif control["state"] in {"controlled-local", "controlled-remote"}:
        state = "controlled-elsewhere"
    elif binding["state"] == "bound-here":
        state = "bound-here"
    elif binding["state"] == "terminal" or control["state"] == "terminal":
        state = "terminal"
    else:
        state = "unbound"

    actions = []
    action = control.get("action")
    if state == "controlled-elsewhere" and isinstance(action, dict):
        actions.append(action)
    return {
        "version": 1,
        "state": state,
        "binding": binding,
        "control": control,
        "actions": actions,
    }


def normalize(value: object, *, has_bound_session: bool, has_controllers: bool) -> dict:
    """Normalize additive JSON across current and legacy engine rows."""
    return picker_reciprocal.normalize(
        value,
        has_bound_session=has_bound_session,
        has_controllers=has_controllers,
    )


def short_label(value: dict[str, Any]) -> str:
    """Return the compact Picker label for a normalized presentation state."""
    return picker_reciprocal.short_label(value)
