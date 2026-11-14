"""Admit reporter naming without importing the engine or resolving local identity."""

from __future__ import annotations

from worktree_identity import WorktreeMachineIdentity, parse_machine_identity

RECEIVER_FIELD = "_reporting_context"


def parse_reporting_context(envelope: dict) -> dict | None:
    """Keep legacy absence distinct from an explicitly unavailable reporter."""
    if "reporting_context" not in envelope:
        return None
    value = envelope["reporting_context"]
    if not isinstance(value, dict) or type(value.get("version")) is not int or (
        value["version"] != 1
    ):
        raise ValueError("unsupported current-roster reporting context")
    if value.get("status") == "unavailable" and set(value) == {"version", "status"}:
        return dict(value)
    if value.get("status") != "resolved" or set(value) != {
        "version", "status", "hostname", "machine", "environment", "execution_machine",
    }:
        raise ValueError("invalid current-roster reporting context fields")
    if not isinstance(value["environment"], str) or value["environment"] not in {
        "windows", "wsl", "linux",
    }:
        raise ValueError("invalid current-roster reporting environment")
    for name in ("hostname", "machine", "execution_machine"):
        text = value[name]
        if (
            not isinstance(text, str) or not text.strip()
            or len(text.encode("utf-8")) > 256 or any(char in text for char in "\r\n\0")
            or text != text.strip()
        ):
            raise ValueError("invalid current-roster reporting name")
    return dict(value)


def row_identity(row: dict) -> WorktreeMachineIdentity | None:
    """Prefer admitted reporter facts; legacy row tuples remain a skew fallback."""
    if RECEIVER_FIELD not in row:
        return parse_machine_identity(row)
    context = parse_reporting_context({"reporting_context": row[RECEIVER_FIELD]})
    if context["status"] == "unavailable":
        return None
    return WorktreeMachineIdentity(
        machine=context["machine"], environment=context["environment"],
        execution_machine=context["execution_machine"],
    )


def row_execution_machine(row: dict) -> str:
    """Do not revive a stamped name when live reporting is unavailable."""
    identity = row_identity(row)
    if identity is not None:
        return identity.execution_machine
    return "" if RECEIVER_FIELD in row else str(row.get("machine") or "")
