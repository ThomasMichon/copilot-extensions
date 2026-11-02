"""Shared ``Finding``/authority helpers used across plugin-activation resolution."""

from __future__ import annotations

from pathlib import Path

from dropin_registry import Finding, ScanAuthority

REGISTRY_NAME = "plugin-activation"


def _finding(
    entry: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: str | None = None,
    owner: str | None = None,
    detail: str | None = None,
) -> Finding:
    return Finding(
        registry=REGISTRY_NAME,
        entry=str(entry),
        status=status,
        reason=reason,
        target=target,
        owner=owner,
        remedy="Fix the registered repo/plugin settings or reinstall the plugin.",
        detail=detail,
    )


def _combine_authority(*authorities: ScanAuthority) -> ScanAuthority:
    if ScanAuthority.INDETERMINATE in authorities:
        return ScanAuthority.INDETERMINATE
    if all(authority is ScanAuthority.ABSENT for authority in authorities):
        return ScanAuthority.ABSENT
    return ScanAuthority.COMPLETE
