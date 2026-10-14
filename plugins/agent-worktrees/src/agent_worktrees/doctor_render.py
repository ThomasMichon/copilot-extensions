"""Render helpers for ``agent-worktrees doctor``."""

from __future__ import annotations


def _cutover_blocked(report: dict[str, object]) -> bool:
    if report.get("cutover_in_progress"):
        return True
    nested = report.get("before")
    return isinstance(nested, dict) and bool(nested.get("cutover_in_progress"))


def _census(report: dict[str, object]) -> dict[str, object]:
    # An apply report's fresh re-census can become uncertain even when the
    # pre-repair census was not; show the census that explains the refusal.
    after = report.get("after")
    if isinstance(after, dict) and after.get("grouping_uncertain"):
        return after
    before = report.get("before")
    return before if isinstance(before, dict) else report


def _generation_lines(census: dict[str, object]) -> list[str]:
    """A launcher and its worker are one daemon generation, shown as one."""
    lines: list[str] = []
    generations = census.get("generations")
    for generation in generations if isinstance(generations, list) else []:
        if isinstance(generation, dict) and generation.get("launcher_pid") is not None:
            lines.append(
                f"      generation: launcher pid {generation['launcher_pid']} -> "
                f"worker pid {generation.get('worker_pid')} (one daemon)"
            )
    if census.get("grouping_uncertain"):
        lines.append(
            "      ! generation grouping uncertain; terminations refused "
            f"({census.get('grouping_uncertain_reason')})"
        )
    return lines


def render_daemon_health_report(report: dict[str, object]) -> None:
    chk = "\u2713"
    findings = report.get("findings")
    if _cutover_blocked(report):
        print("  ! Resident daemon health: cutover in progress; audit skipped")
        return
    if not isinstance(findings, list) or not findings:
        print(f"  {chk} Resident daemon health: no abnormal cutover findings")
        for line in _generation_lines(_census(report)):
            print(line)
        return

    mode = str(report.get("mode") or "report")
    label = "fix" if mode == "apply" else "report-only"
    remaining = report.get("remaining_findings")
    unresolved = remaining if isinstance(remaining, list) else findings
    marker = chk if mode == "apply" and not unresolved else "!"
    print(
        f"  {marker} Resident daemon health ({label}): "
        f"{len(findings)} finding(s)"
    )
    detail_source = (
        report.get("before")
        if isinstance(report.get("before"), dict)
        else report
    )
    detail_findings = (
        detail_source.get("findings")
        if isinstance(detail_source, dict)
        else findings
    )
    for finding in detail_findings or []:
        kind = str(finding.get("kind") or "unknown")
        summary = str(finding.get("summary") or kind.replace("_", " "))
        target_bits: list[str] = []
        targets = finding.get("targets")
        if isinstance(targets, list):
            target_bits = [
                f"pid {item['pid']}" + (f" ({item['role']})" if item.get("role") else "")
                for item in targets
                if isinstance(item, dict) and "pid" in item
            ]
        suffix = f" -> {', '.join(target_bits)}" if target_bits else ""
        blocked = finding.get("blocked_reason")
        if blocked and mode != "apply":
            suffix += f" [{blocked}]"
        print(f"      - {summary}{suffix}")
    for line in _generation_lines(_census(report)):
        print(line)

    actions = report.get("actions")
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        kind = str(action.get("kind") or "unknown")
        if action.get("blocked"):
            print(f"        {kind}: blocked ({action.get('reason')})")
            continue
        result = action.get("result")
        if isinstance(result, dict):
            print(f"        {kind}: {result.get('reason')}")
            continue
        termination = action.get("termination")
        if isinstance(termination, dict):
            print(
                f"        {kind}: pid {action.get('pid')} -> "
                f"{'terminated' if termination.get('killed') else 'left running'} "
                f"({termination.get('method')})"
            )


def render_dropin_registry_report(label: str, report: dict) -> None:
    """Render one exhaustive report-only drop-in registry section."""
    authority = report.get("authority", "indeterminate")
    active = report.get("active_entries") or []
    findings = report.get("findings") or []
    if not findings:
        print(f"  \u2713 {label}: {len(active)} active, authority={authority}")
        return
    print(
        f"  ! {label}: {len(active)} active, {len(findings)} finding(s), "
        f"authority={authority} (report-only)"
    )
    for finding in findings:
        target = f" target={finding['target']}" if finding.get("target") else ""
        print(f"      - {finding.get('entry', '?')}: {finding.get('reason', 'unknown')}{target}")
        if finding.get("remedy"):
            print(f"        -> {finding['remedy']}")
