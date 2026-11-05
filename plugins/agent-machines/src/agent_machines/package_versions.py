"""Numeric package compatibility constraints shared by parsing and restore."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .resources import ResourceContribution, ResourceFinding


def version_parts(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    if not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,10}){1,3}", value):
        return None
    return tuple(int(part) for part in value.split("."))


def version_key(value: str) -> tuple[int, ...]:
    parts = version_parts(value)
    if parts is None:
        raise ValueError("package compatibility versions must be dotted numeric strings")
    return parts + (0,) * (4 - len(parts))


def compatible(version: Any, minimum: str, blocked: list[str]) -> bool:
    parts = version_parts(version)
    if parts is None or version_key(version) < version_key(minimum):
        return False
    padded = version_key(version)
    return not any(
        padded[: len(excluded)] == excluded
        for value in blocked
        if (excluded := version_parts(value)) is not None
    )


def validate(declaration: dict[str, Any], schema: int) -> None:
    if not ({"minimum_version", "blocked_versions"} & declaration.keys()):
        return
    if declaration.get("type") != "package":
        raise ValueError("package compatibility fields require a package resource")
    if schema < 5:
        raise ValueError("package compatibility constraints require schema_version 5")
    version_key(declaration.get("minimum_version"))
    blocked = declaration.get("blocked_versions", [])
    if not isinstance(blocked, list) or any(version_parts(value) is None for value in blocked):
        raise ValueError("blocked_versions must be a list of dotted numeric versions")
    if "pin" in declaration and type(declaration["pin"]) is not bool:
        raise ValueError("package compatibility pin must be a boolean")
    if declaration.get("version") is not None or declaration.get("pin") not in (None, False):
        raise ValueError("package compatibility constraints cannot declare an exact version or pin")


def conflicts(desired: dict[str, Any]) -> bool:
    minimum, version = desired.get("minimum_version"), desired.get("version")
    return bool(minimum and (
        (version and not compatible(version, minimum, desired.get("blocked_versions", [])))
        or (desired.get("pin") and not version)
    ))


def merge(
    members: list[ResourceContribution], identity: tuple,
) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
    from .resources import ResourceFinding, _select_field

    findings, decisions, selected_values = [], [], {}
    declarations = [member.declaration for member in members]
    for field in ("state", "version"):
        value, selected, _, conflict, decision, info = _select_field(
            members, identity, field,
            lambda member: (
                str(member.declaration.get("state", "present"))
                if field == "state" else str(member.declaration["version"])
            ),
            include=lambda member: field == "state" or bool(member.declaration.get("version")),
        )
        selected_values[field] = value
        if conflict:
            owners = ", ".join(sorted(member.owner for member in selected))
            description = (
                "is declared both present and absent"
                if field == "state" else
                f"is pinned to conflicting versions "
                f"{sorted({str(member.declaration['version']) for member in selected})}"
            )
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"package '{identity[2]}' ({identity[1]}) {description} across packages: {owners}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
    pin = any(bool(declaration.get("pin")) for declaration in declarations)
    minimums = [
        declaration["minimum_version"]
        for declaration in declarations if declaration.get("minimum_version")
    ]
    minimum = max(minimums, key=version_key) if minimums else None
    blocked = sorted(
        {value for declaration in declarations for value in declaration.get("blocked_versions", [])},
        key=lambda value: (version_key(value), value),
    )
    if conflicts({
        **selected_values, "minimum_version": minimum, "blocked_versions": blocked, "pin": pin,
    }):
        findings.append(ResourceFinding(
            "error", "resource-conflict",
            f"package '{identity[2]}' ({identity[1]}) exact pin conflicts with compatibility constraints",
        ))
    guard_names = sorted({
        str(name).strip().casefold()
        for declaration in declarations
        for name in (declaration.get("process_guard") or {}).get("names", [])
    })
    return {
        "manager": identity[1],
        "id": identity[2],
        **selected_values,
        "minimum_version": minimum,
        "blocked_versions": blocked,
        "pin": pin,
        "unpin": not pin and any(
            declaration.get("minimum_version") and declaration.get("pin") is False
            for declaration in declarations
        ),
        "maintenance_safe": any(bool(declaration.get("maintenance_safe")) for declaration in declarations),
        "process_guard": {"names": guard_names} if guard_names else None,
    }, findings, decisions
