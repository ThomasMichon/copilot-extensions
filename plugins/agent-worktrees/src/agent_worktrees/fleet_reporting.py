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
    if host.get("reachable") is not True:
        keys = ()
        fields = {"machine": "", "platform": ""}
    elif "reporting_context" not in host:
        return (), rows
    else:
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


class Coverage:
    """Request-local source visibility, independent of row labels and routing."""

    def __init__(self) -> None:
        self._complete: list[bool] = []
        self._named: list[bool] = []
        self._source_by_row: dict[int, int] = {}
        self._selectors_by_row: dict[int, tuple[tuple[str, str], ...]] = {}

    def add(
        self, rows: list[dict], selectors: tuple[tuple[str, str], ...], *, complete: bool,
    ) -> None:
        """Retain the actual containing source for this graph evaluation."""
        source = len(self._complete)
        self._complete.append(complete)
        self._named.append(bool(selectors))
        for row in rows:
            self._source_by_row[id(row)] = source
            self._selectors_by_row[id(row)] = selectors

    def names_for(self, row: dict) -> set[str]:
        """Only live reporter selectors may bind a qualified caller name."""
        return {name for name, _environment in self._selectors_by_row.get(id(row), ())}

    def gap(self, observed: dict | None) -> str | None:
        """Every other source must exclude an unseen or conflicting caller ID."""
        selected = self._source_by_row.get(id(observed)) if observed is not None else None
        reason = None
        for source, complete in enumerate(self._complete):
            if source == selected or complete:
                continue
            if not self._named[source]:
                return "unnamed"
            reason = "incomplete"
        return reason
