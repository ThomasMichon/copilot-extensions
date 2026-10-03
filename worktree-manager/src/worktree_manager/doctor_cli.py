"""Doctor surface for Worktree Manager."""

from __future__ import annotations

import json

from . import __version__, daemon_health
from .core_install import core_status
from .model import coverage
from .prereqs import current_os, detect_baseline, missing
from .self_install import status as self_status

_BANNER = "copilot-extensions Worktree Manager"


def _cutover_blocked(report: dict[str, object]) -> bool:
    if report.get("cutover_in_progress"):
        return True
    nested = report.get("before")
    return isinstance(nested, dict) and bool(nested.get("cutover_in_progress"))


def _prereq_line(s) -> str:
    if not s.present:
        state = "optional, absent" if s.optional else "MISSING"
        mark = "○" if s.optional else "✗"
    elif not s.satisfied:
        state = f"{s.version or '?'} < required {s.min_required}"
        mark = "✗"
    else:
        ver = f" {s.version}" if s.version else ""
        state = f"ok{ver}"
        mark = "✓"
    return f"    {mark} {s.name.ljust(9)} {state}"


def _alignment_blocking(cov) -> bool:
    """Whether the plugin-alignment report should fail the doctor exit status.

    A ``source_kind`` of ``"none"`` means no marketplace was reachable at all
    (no checkout, remote fetch failed) — coverage can't be confirmed either
    way, so it is not treated as drift.
    """
    return cov.source_kind != "none" and not cov.ok


def _print_alignment(cov) -> None:
    print("  plugin alignment:")
    if cov.source_kind == "none":
        print("    ○ no marketplace reachable — cannot confirm catalog coverage.")
        print()
        return
    if cov.uncovered:
        print("    ○ discovered but not in the authored catalog (inferred defaults):")
        for n in cov.uncovered:
            print(f"        - {n}")
    if cov.phantom:
        print("    ✗ in the authored catalog but not discovered (phantom/renamed):")
        for n in cov.phantom:
            print(f"        - {n}")
    if cov.published_prereq_gaps:
        print("    ✗ a plugin publishes a prereq the catalog does not carry:")
        for plug, pr in cov.published_prereq_gaps:
            print(f"        - {plug}: {pr}")
    if cov.ok and not cov.uncovered:
        print("    ✓ every discovered plugin has an authored catalog entry; no drift.")
    elif cov.ok:
        print("    ✓ no errors (uncovered plugins are handled by inference).")
    print()


def cmd_doctor(rest: list[str]) -> int:
    apply_daemon_health = "--apply-daemon-health" in rest
    json_mode = "--json" in rest
    statuses = detect_baseline()
    core = core_status()
    daemon_report = daemon_health.doctor_report(apply=apply_daemon_health)
    cov = coverage()

    if json_mode:
        selfst = self_status()
        from . import source_config as _sc

        print(
            json.dumps(
                {
                    "prerequisites": [
                        {
                            "name": status.name,
                            "present": status.present,
                            "satisfied": status.satisfied,
                            "version": status.version,
                            "min_required": status.min_required,
                            "optional": status.optional,
                            "path": status.path,
                            "notes": status.notes,
                        }
                        for status in statuses
                    ],
                    "core": {
                        "state": core.state,
                        "runtime_dir": core.runtime_dir,
                        "runtime_present": core.runtime_present,
                        "venv_present": core.venv_present,
                        "binstub": core.binstub,
                        "installed": core.installed,
                    },
                    "self": {
                        "installed_version": selfst.installed_version,
                        "running_version": __version__,
                        "binstub": selfst.binstub,
                        "root": str(selfst.root),
                    },
                    "daemon_health": daemon_report,
                    "plugin_alignment": {
                        "source_kind": cov.source_kind,
                        "ok": cov.ok,
                        "uncovered": list(cov.uncovered),
                        "phantom": list(cov.phantom),
                        "published_prereq_gaps": [list(g) for g in cov.published_prereq_gaps],
                    },
                    "source": {
                        "repo": _sc.resolved_repo(),
                        "ref": _sc.resolved_ref(),
                    },
                },
                indent=2,
            )
        )
        return 0

    print()
    print(f"  {_BANNER} — doctor  (os: {current_os()})")
    print()
    print("  prerequisites:")
    for status in statuses:
        print(_prereq_line(status))
    print()
    print("  agent-worktrees core:")
    print(f"    state: {core.state}")
    print(
        f"    runtime: {core.runtime_dir} "
        f"({'present' if core.runtime_present else 'absent'}"
        f"{', venv' if core.venv_present else ''})"
    )
    print(f"    binstub: {core.binstub or 'not found in ~/.local/bin'}")
    print()
    selfst = self_status()
    print("  worktree-manager (self):")
    print(f"    installed version: {selfst.installed_version or '(not versioned-installed)'}")
    print(f"    running version:   {__version__}")
    print(f"    binstub: {selfst.binstub or 'not found in ~/.local/bin'}")
    print(f"    root: {selfst.root}")
    from . import source_config as _sc

    cfg_repo, cfg_ref = _sc.configured_source()
    print(
        f"    update source: {_sc.resolved_repo()} @ {_sc.resolved_ref()}"
        f"{' (default)' if not (cfg_repo or cfg_ref) else ' (configured)'}"
    )
    print()
    findings = daemon_report.get("findings") if isinstance(daemon_report, dict) else []
    if findings:
        label = "fix" if apply_daemon_health else "report-only"
        print(f"  mux-daemon health ({label}):")
        detail_source = (
            daemon_report.get("before")
            if apply_daemon_health and isinstance(daemon_report.get("before"), dict)
            else daemon_report
        )
        for finding in detail_source.get("findings", []):
            summary = finding.get("summary") or finding.get("kind", "unknown")
            targets = finding.get("targets") or []
            target_bits = ", ".join(
                f"pid {item['pid']}"
                for item in targets
                if isinstance(item, dict) and "pid" in item
            )
            suffix = f" -> {target_bits}" if target_bits else ""
            print(f"    ! {summary}{suffix}")
        for action in daemon_report.get("actions", []):
            if action.get("blocked"):
                print(f"      - {action.get('kind')}: blocked ({action.get('reason')})")
                continue
            result = action.get("result")
            if isinstance(result, dict):
                print(f"      - {action.get('kind')}: {result.get('reason')}")
                continue
            termination = action.get("termination")
            if isinstance(termination, dict):
                print(
                    f"      - {action.get('kind')}: pid {action.get('pid')} -> "
                    f"{'terminated' if termination.get('killed') else 'left running'} "
                    f"({termination.get('method')})"
                )
        print()
    elif _cutover_blocked(daemon_report):
        print("  mux-daemon health:")
        print("    ! cutover in progress; audit skipped")
        print()
    else:
        print("  mux-daemon health:")
        print("    ✓ no abnormal cutover findings")
        print()

    _print_alignment(cov)

    gaps = missing(statuses)
    if gaps or not core.installed:
        print("  → not fully set up. Run `worktree-manager setup` to see the plan "
              "(add --apply to execute).")
    else:
        print("  ✓ prerequisites satisfied and the core is installed.")
    print()
    return 0 if (not gaps and core.installed and not _alignment_blocking(cov)) else 1
