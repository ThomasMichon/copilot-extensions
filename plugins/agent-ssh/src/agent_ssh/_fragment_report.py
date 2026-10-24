"""Presentation of classified fragment evidence, independent of source scanning."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dropin_registry import Finding, ScanAuthority

if TYPE_CHECKING:
    from .fragment_registry import FragmentRegistryReport


def render_payload(
    report: FragmentRegistryReport, registry: str, root: Path,
    shadow_findings: tuple[Finding, ...],
) -> dict[str, object]:
    return {
        "registry": registry,
        "path": str(root),
        "authority": report.snapshot.authority.value,
        "active": [
            fragment.to_dict()
            for _, fragment in sorted(report.entries.items())
        ],
        "findings": [
            finding.to_dict() for finding in (*report.findings, *shadow_findings)
        ],
        "fix_available": False,
        "active_basis": "current-evidence-only",
        "retention_possible": (
            report.snapshot.authority is ScanAuthority.INDETERMINATE
            or any(finding.status == "indeterminate" for finding in report.findings)
        ),
        "unscoped_blocking": report.unscoped_blocking,
    }


def render_text(
    report: FragmentRegistryReport, registry: str, root: Path,
    shadow_findings: tuple[Finding, ...],
) -> str:
    label = "[WARN]" if (report.findings or shadow_findings) else "[OK]"
    lines = [
        f"{label} {registry} is {report.snapshot.authority.value}; "
        f"{len(report.entries)} managed fragment(s) confirmed active by current evidence.",
        f"  path: {root}",
    ]
    for finding in shadow_findings:
        lines.append(f"  [WARN] {finding.entry}: {finding.remedy}")
    if (
        report.snapshot.authority is ScanAuthority.INDETERMINATE
        or any(finding.status == "indeterminate" for finding in report.findings)
    ):
        lines.append(
            "  A running process may retain only matching last-known fragments "
            "for indeterminate entries."
        )
    if report.unscoped_blocking:
        lines.append(
            "  Incomplete managed-fragment identity blocks fresh alias probes "
            "until every managed entry can be classified."
        )
    for _, fragment in sorted(report.entries.items()):
        lines.append(
            f"  + {fragment.transport}: {fragment.path} "
            f"[{', '.join(fragment.aliases) or 'no aliases'}]"
        )
        if fragment.metadata is None:
            lines.append("    class: legacy-managed")
        else:
            lines.append("    class: managed")
            lines.append(f"    registry: {fragment.metadata.registry}")
            lines.append(f"    module: {fragment.metadata.module}")
    for finding in report.findings:
        target = f" -> {finding.target}" if finding.target else ""
        lines.append(f"  - {finding.reason}: {finding.entry}{target}")
        if finding.detail:
            lines.append(f"    {finding.detail}")
        if finding.remedy:
            lines.append(f"    {finding.remedy}")
    lines.append("  Cleanup is report-only; no --fix operation is available.")
    return "\n".join(lines)
