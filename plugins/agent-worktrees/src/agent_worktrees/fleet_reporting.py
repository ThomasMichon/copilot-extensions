"""Keep observed fleet naming separate from configured source routing."""

from __future__ import annotations

from . import worktrees_reporting
from .worktree_machine_identity import WorktreeMachineIdentity

_NAMING = ("machine", "platform", "machine_identity_version", "environment", "execution_machine")


def source_key(host: dict) -> tuple[str, str]:
    """Return the configured source, never a worktree's descriptive name."""
    return str(host.get("machine") or ""), str(host.get("env") or "")


def reported_rows(host: dict) -> tuple[tuple[tuple[str, str], ...], list[dict]]:
    """Project optional reporter facts without changing the source or its rows."""
    if host.get("reporting_context_error") is True:
        raise ValueError("malformed reporting context")
    rows = [dict(row) for row in host.get("worktrees") or [] if isinstance(row, dict)]
    if "reporting_context" not in host:
        return (source_key(host),), rows
    context = host["reporting_context"]
    worktrees_reporting.validate(context)
    if context["status"] == "resolved":
        identity = WorktreeMachineIdentity(
            context["machine"], context["environment"], context["execution_machine"],
        )
        keys = tuple(dict.fromkeys(
            (name, identity.environment) for name in (identity.execution_machine, identity.machine)
        ))
        fields = identity.to_record_fields()
    else:
        keys = ()
        fields = {"machine": "", "platform": ""}
    for row in rows:
        for field in _NAMING:
            row.pop(field, None)
        row.update(fields)
    return keys, rows
