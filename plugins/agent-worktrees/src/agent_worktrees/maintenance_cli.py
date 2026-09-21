"""Diagnostics / maintenance CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from . import installer as inst, output, reclaim, sessions, tracking


def _core():
    from . import __main__ as core

    return core


def _find_repo_dir(*args, **kwargs):
    return _core()._find_repo_dir(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _current_session_ids(*args, **kwargs):
    return _core()._current_session_ids(*args, **kwargs)


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "config-migrate",
        help="Migrate machine-local config schemas in ~/.agent-worktrees/ (idempotent)",
    )
    sp.add_argument("--quiet", action="store_true", help="Suppress per-file output")

    sp = sub.add_parser(
        "reconcile-binstubs",
        help="Reconcile ~/.local/bin project binstubs against projects.yaml "
        "(add for every registered project, remove deregistered ones)",
    )
    ownership_action = sp.add_mutually_exclusive_group()
    ownership_action.add_argument(
        "--transfer",
        metavar="PROJECT",
        help="explicitly transfer one registered project command to this payload",
    )
    ownership_action.add_argument(
        "--remove",
        metavar="PROJECT",
        help="remove one project command only when this payload owns its receipt",
    )

    sp = sub.add_parser(
        "register-project-entry",
        help="Write a lean projects.yaml entry (installer-invoked; the single "
        "Python owner of the registry write)",
    )
    sp.add_argument("project", help="Project name")
    sp.add_argument(
        "--repo-dir", default=None, help="Anchor dir used to register repository identity"
    )
    sp.add_argument("--display-name", default=None, help="Harness display casing override")
    expose = sp.add_mutually_exclusive_group()
    expose.add_argument(
        "--expose-agent",
        dest="expose_agent",
        action="store_true",
        default=None,
        help="Force agent exposure on (default: from repos.yaml)",
    )
    expose.add_argument(
        "--no-expose-agent",
        dest="expose_agent",
        action="store_false",
        help="Force reference-only (no agent)",
    )
    sp.add_argument(
        "--base-repo",
        dest="base_repo",
        action="store_true",
        default=None,
        help="Mark base-repo (no-worktree) adoption",
    )
    sp.add_argument(
        "--elevated",
        dest="elevated",
        action="store_true",
        default=None,
        help="Mark elevated agent context",
    )
    sp.add_argument(
        "--wsl-state", default=None, choices=["adopted", "bootstrap"], help="WSL adoption state"
    )
    sp.add_argument("--wsl-distro", default=None, help="WSL distro name")
    sp.add_argument("--wsl-path", default=None, help="Repo anchor path in WSL")

    sp = sub.add_parser("dev", help="Dev venv and test runner")
    sp.add_argument(
        "dev_action",
        nargs="?",
        default="status",
        choices=["setup", "test", "status"],
        help="Action: setup, test, or status",
    )

    sp = sub.add_parser(
        "backfill-sessions",
        help="Populate session registries and reciprocal metadata from records",
    )
    sp.add_argument(
        "--projection-budget",
        type=int,
        default=256,
        help="Maximum exact session projection relations to inspect (default: 256)",
    )

    sp = sub.add_parser(
        "anchor-check", help="Check anchor repo for uncommitted work and stash entries"
    )
    sp.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")
    sp.add_argument("--quiet", action="store_true", help="Only print if issues are found")
    sp.add_argument("--strict", action="store_true", help="Exit nonzero if anchor is not clean")
    sp.add_argument(
        "--fetch",
        action="store_true",
        help="Refresh the upstream ref before the behind-count "
        "(slower; unneeded post pre-launch fetch)",
    )
    sp.add_argument("--repo-path", default=None, help="Path inside a repo (defaults to cwd)")

    sp = sub.add_parser(
        "doctor",
        help="Diagnose (and with --fix, repair) worktree/session health: "
        "corrupt tracking records, empty session registries, stale "
        "status, orphaned empty session shells, cwd/path misalignment, "
        "and drop-in registry hygiene.",
    )
    sp.add_argument(
        "--fix",
        action="store_true",
        help="Apply non-destructive repairs (YAML integrity, "
        "registry/title backfill, stale status). Default: "
        "report only.",
    )
    sp.add_argument(
        "--gc-sessions",
        action="store_true",
        dest="gc_sessions",
        help="With --fix, also delete empty (0-user-message) "
        "session-state shells and purge their session-store "
        "rows (destructive; guarded by age/lock/current/"
        "registered).",
    )
    sp.add_argument(
        "--prune-pivots",
        action="store_true",
        dest="prune_pivots",
        help="With --fix, also delete stale Picker pivot manifest "
        "files (duplicate/identity-mismatch/missing-target/"
        "invalid-entry) once a live, correct manifest for the "
        "same plugin already exists elsewhere in the registry "
        "(destructive; never touches an operator-authored or "
        "indeterminate manifest -- explicit opt-in, off by "
        "default even with --fix).",
    )
    sp.add_argument("--json", action="store_true", help="Emit the health report as JSON.")
    sp.add_argument(
        "--projection-budget",
        type=int,
        default=256,
        help="Maximum exact session projection relations to inspect (default: 256)",
    )

    sp = sub.add_parser(
        "hygiene",
        help="Detect (and with --fix, remove) stale global-Python editable "
        "installs left by a manual 'pip install -e .' against a worktree "
        "checkout (see #2726). Machine-wide; no project context needed.",
    )
    sp.add_argument(
        "--fix",
        action="store_true",
        help="Remove each finding (pip uninstall, falling back to deleting "
        "the .pth file). Default: report only.",
    )
    sp.add_argument("--json", action="store_true", help="Emit the report as JSON.")


def cmd_hygiene(args) -> int:
    """Report (and with ``--fix``, remove) stale global-Python editable installs."""
    from . import hygiene

    fix = getattr(args, "fix", False)
    json_mode = getattr(args, "json", False)
    report = hygiene.scan_and_clean(fix=fix)

    if json_mode:
        print(json.dumps(report, indent=2))
        return 0

    findings = report["findings"]
    if not findings:
        print("No stale global-Python editable installs found.")
        return 0
    for item in findings:
        print(
            f"{item['distribution']} {item['version']} -> {item['target']} "
            f"[{item['status']}]"
        )
        print(f"  {item['pth']}")
    if not fix:
        print(f"\n{len(findings)} stale editable install(s) found. Re-run with --fix to remove.")
    return 0


def cmd_dev(args) -> int:
    """Dispatch to tools/dev/setup.{sh,ps1} for dev venv management."""
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot determine repo root.")
        return 1

    dev_action = args.dev_action if hasattr(args, "dev_action") else "status"

    if sys.platform == "win32":
        script = repo_dir / "tools" / "dev" / "setup.ps1"
        if not script.exists():
            output.err(f"Dev script not found: {script}")
            return 1
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script), dev_action],
            cwd=str(repo_dir),
        )
        return result.returncode

    script = repo_dir / "tools" / "dev" / "setup.sh"
    if not script.exists():
        output.err(f"Dev script not found: {script}")
        return 1
    os.execvp("bash", ["bash", str(script), dev_action])
    return 1


def _run_reciprocal_backfill(
    records: list[tracking.WorktreeRecord],
    *,
    apply: bool,
    projection_budget: int,
) -> dict:
    """Audit or repair legacy controllers and exact session projections."""
    from . import session_projection

    controller_items = []
    for record in records:
        try:
            candidates = tracking.derive_legacy_controller_relations(record)
        except tracking.ControllerRelationError as exc:
            controller_items.append(
                {
                    "worktree_id": record.worktree_id,
                    "status": "blocked",
                    "detail": str(exc),
                    "relations": 0,
                    "repaired": False,
                }
            )
            continue
        if not candidates:
            continue
        repaired = False
        status = "candidate"
        if apply:
            try:
                persisted = tracking.backfill_legacy_controller_relations(record)
                repaired = bool(persisted)
                status = "repaired" if repaired else "current"
            except tracking.ControllerRelationError as exc:
                status = "blocked"
                controller_items.append(
                    {
                        "worktree_id": record.worktree_id,
                        "status": status,
                        "detail": str(exc),
                        "relations": len(candidates),
                        "repaired": False,
                    }
                )
                continue
        controller_items.append(
            {
                "worktree_id": record.worktree_id,
                "status": status,
                "relations": len(candidates),
                "controller_session_ids": sorted(
                    {
                        relation.controller_session_id
                        for relation in candidates
                        if relation.controller_session_id
                    }
                ),
                "repaired": repaired,
            }
        )

    projections = session_projection.backfill_relations(
        records,
        apply=apply,
        budget=projection_budget,
    )
    return {
        "legacy_controllers": {
            "candidates": len(controller_items),
            "items": controller_items,
            "repaired": sum(bool(item["repaired"]) for item in controller_items),
            "blocked": sum(item["status"] == "blocked" for item in controller_items),
        },
        "projections": projections,
    }


def _run_backfill(
    tracking_path: Path,
    *,
    projection_budget: int = 256,
) -> dict:
    """Registry + title backfill core shared by ``backfill-sessions`` and ``doctor``."""
    records = tracking.list_records(tracking_path)

    need_backfill = [r for r in records if not r.sessions]
    discovered: dict[str, list[str]] = {}
    sess_updated = 0
    if need_backfill:
        discovered = sessions.backfill_sessions(need_backfill)
        for rec in need_backfill:
            sids = discovered.get(rec.worktree_id, [])
            if not sids:
                if rec.sessions is None:
                    rec.sessions = []
                    tracking.save_record(rec)
                    sess_updated += 1
                continue

            rec.sessions = [tracking.SessionEntry(session_id=sid, started_at="") for sid in sids]
            tracking.save_record(rec)
            sess_updated += 1

    titled = 0
    title_targets = [r for r in records if not (r.title and r.title != "null")]
    if title_targets:
        tctx = sessions.scan_sessions_fast(title_targets)
        for rec in title_targets:
            summary = tctx.latest_summary.get(_core()._normalize_path(rec.worktree_path), "")
            if summary and summary != "null":
                rec.title = summary
                tracking.save_record(rec)
                titled += 1

    reciprocal = _run_reciprocal_backfill(
        records,
        apply=True,
        projection_budget=projection_budget,
    )
    return {
        "scanned": len(need_backfill),
        "sessions": sum(len(v) for v in discovered.values()),
        "worktrees": len(discovered),
        "registry": sess_updated,
        "titles": titled,
        "reciprocal_metadata": reciprocal,
    }


def cmd_backfill_sessions(args) -> int:
    """Populate empty session registries and reciprocal metadata from records."""
    result = _run_backfill(
        cfg.tracking_dir(),
        projection_budget=getattr(args, "projection_budget", 256),
    )
    if result["scanned"]:
        print(f"Scanning session-state for {result['scanned']} worktree(s)...")
    print(
        f"Backfilled {result['sessions']} session(s) across "
        f"{result['worktrees']} worktree(s); "
        f"{result['registry']} registry + {result['titles']} title record(s) updated"
    )
    reciprocal = result["reciprocal_metadata"]
    controllers = reciprocal["legacy_controllers"]
    projections = reciprocal["projections"]
    print(
        f"Reciprocal metadata: {controllers['repaired']} legacy controller "
        f"record(s) + {projections['repaired']} projection relation(s) repaired; "
        f"{projections['report_only']} report-only, "
        f"{projections['remaining']} deferred by budget"
    )
    return 0


def cmd_doctor(args) -> int:
    """Diagnose (and with ``--fix``, repair) worktree/session health for this project."""
    from . import health

    apply = getattr(args, "fix", False)
    do_gc = getattr(args, "gc_sessions", False)
    do_prune_pivots = getattr(args, "prune_pivots", False)
    json_mode = getattr(args, "json", False)
    projection_budget = getattr(args, "projection_budget", 256)

    try:
        proj_name = cfg.project_name()
    except Exception:
        proj_name = ""
    project_names = sorted(
        str(name)
        for name in ((inst.read_projects_registry().get("projects") or {}).keys())
        if isinstance(name, str) and cfg._PROJECT_NAME_RE.fullmatch(name)
    )
    if not proj_name:
        discovered = health.find_record_by_cwd_across_projects(os.getcwd(), project_names)
        if discovered is not None:
            proj_name = discovered.repo
            cfg.set_active_project(proj_name)

    yaml_findings = []
    backfill = {
        "scanned": 0,
        "sessions": 0,
        "worktrees": 0,
        "registry": 0,
        "titles": 0,
    }
    stale = []
    stale_fixed = 0
    gc_result = {"count": 0, "removed_dirs": 0, "removed_rows": 0, "ids": []}
    misaligned = []
    orphaned = []
    orphaned_fixed = 0
    stale_heads: list[str] = []
    stale_heads_fixed = 0
    reciprocal = {
        "legacy_controllers": {
            "candidates": 0,
            "repaired": 0,
            "blocked": 0,
            "items": [],
        },
        "projections": {
            "budget": max(0, projection_budget),
            "candidates": 0,
            "checked": 0,
            "remaining": 0,
            "repairable": 0,
            "repaired": 0,
            "report_only": 0,
            "status_counts": {},
            "items": [],
        },
    }
    pair_integrity = health.audit_pair_integrity(
        project_names,
        apply=apply,
    )

    if proj_name:
        tracking_dir = cfg.tracking_dir()
        session_dir = sessions._session_state_dir()
        store_db = health.default_store_db(session_dir)

        yaml_findings = health.repair_yaml_integrity(tracking_dir, apply=apply)

        if apply:
            backfill = _run_backfill(
                tracking_dir,
                projection_budget=projection_budget,
            )
            reciprocal = backfill.pop("reciprocal_metadata")
        else:
            recs = tracking.list_records(tracking_dir)
            need = [r for r in recs if not r.sessions]
            disc = sessions.backfill_sessions(need) if need else {}
            backfill = {
                "scanned": len(need),
                "sessions": sum(len(v) for v in disc.values()),
                "worktrees": len(disc),
                "registry": len(disc),
                "titles": len([r for r in recs if not (r.title and r.title != "null")]),
            }
            reciprocal = _run_reciprocal_backfill(
                recs,
                apply=False,
                projection_budget=projection_budget,
            )

        records = tracking.list_records(tracking_dir)

        for record in health.find_stale_head_caches(records):
            stale_heads.append(record.worktree_id)
            if apply and tracking.repair_head_cache(record):
                tracking.save_record(record)
                stale_heads_fixed += 1

        stale = health.find_stale_status(records)
        if apply:
            for record in stale:
                record.status = "complete"
                tracking.save_record(record)
                stale_fixed += 1

        exclude = health.registered_session_ids(records) | _current_session_ids()
        shells = health.find_empty_session_shells(session_dir, exclude_ids=frozenset(exclude))
        gc_result = health.gc_empty_shells(session_dir, store_db, shells, apply=(apply and do_gc))

        misaligned = health.audit_alignment(records, session_dir)

        orphaned = health.find_orphaned_handoffs(records)
        if apply:
            for orphan in orphaned:
                entry = orphan.record.session_entry(orphan.session_id)
                if entry is not None and entry.state == "handed-off":
                    entry.state = "active"
                    tracking.set_head_session(
                        orphan.record,
                        orphan.session_id,
                        save=False,
                    )
                    tracking.save_record(orphan.record)
                    orphan.reactivated = True
                    orphaned_fixed += 1

    bare_orphans = reclaim.find_bare_orphans()

    try:
        from . import reconcile as _reconcile

        repo_dir = _find_repo_dir()
        runtime_lag = _reconcile.running_version_lag(Path(repo_dir)) if repo_dir else []
    except Exception:
        runtime_lag = []

    from . import config_dropins
    from .picker_support import pivots as pivot_registry

    pivot_report = pivot_registry.scan_pivot_registry(materialize=False)
    pivot_pruned: list[dict[str, object]] = []
    if apply and do_prune_pivots:
        pivot_pruned = pivot_registry.prune_stale_entries(pivot_report, apply=True)
        if any(item.get("removed") for item in pivot_pruned):
            pivot_report = pivot_registry.scan_pivot_registry(materialize=False)
    config_d_report = (
        config_dropins.scan_config_dropin_registry(
            cfg.default_config_path().parent / "config.d",
            project_name=proj_name,
        )
        if proj_name
        else config_dropins.empty_config_dropin_report()
    )

    report = {
        "project": proj_name,
        "project_health_available": bool(proj_name),
        "mode": "fix" if apply else "report",
        "yaml_integrity": {
            "bad": len(yaml_findings),
            "repairable": sum(1 for f in yaml_findings if f.repairable),
            "repaired": sum(1 for f in yaml_findings if f.repaired),
            "files": [
                {
                    "file": f.path.name,
                    "error": f.error,
                    "repairable": f.repairable,
                    "repaired": f.repaired,
                }
                for f in yaml_findings
            ],
        },
        "backfill": backfill,
        "reciprocal_metadata": reciprocal,
        "pair_integrity": {
            "found": len(pair_integrity),
            "repairable": sum(1 for finding in pair_integrity if finding.repairable),
            "repaired": sum(1 for finding in pair_integrity if finding.repaired),
            "items": [finding.as_dict() for finding in pair_integrity],
        },
        "head_cache": {
            "found": len(stale_heads),
            "fixed": stale_heads_fixed,
            "ids": stale_heads,
        },
        "stale_status": {
            "found": len(stale),
            "fixed": stale_fixed,
            "ids": [r.worktree_id for r in stale],
        },
        "empty_sessions": gc_result,
        "misaligned": {"count": len(misaligned), "worktrees": misaligned},
        "orphaned_handoffs": {
            "found": len(orphaned),
            "reactivated": orphaned_fixed,
            "items": [
                {
                    "worktree_id": orphan.worktree_id,
                    "session_id": orphan.session_id,
                    "age_h": round(orphan.age_h, 1),
                    "last_stage": orphan.last_stage,
                    "last_stage_name": orphan.last_stage_name,
                    "reactivated": orphan.reactivated,
                }
                for orphan in orphaned
            ],
        },
        "bare_orphans": {"count": len(bare_orphans), "items": bare_orphans},
        "runtime_lag": runtime_lag,
        "pivots": {
            **pivot_report.to_dict(),
            "pruned": {
                "found": sum(1 for item in pivot_pruned),
                "removed": sum(1 for item in pivot_pruned if item.get("removed")),
                "items": pivot_pruned,
            },
        },
        "config_d": config_d_report.to_dict(),
    }

    if json_mode:
        _json_output(report)
        return 0

    _render_doctor_report(
        report,
        applied=apply,
        gc_applied=(apply and do_gc),
        prune_pivots_applied=(apply and do_prune_pivots),
    )
    return 0


def _render_doctor_report(
    report: dict, *, applied: bool, gc_applied: bool, prune_pivots_applied: bool = False
) -> None:
    chk = "\u2713"
    print(f"Worktree/session doctor ({'fix' if applied else 'report-only'})")
    if not report.get("project_health_available", True):
        print("  \u2022 Project health skipped: no active project context")

    yi = report["yaml_integrity"]
    if yi["bad"]:
        tail = f", {yi['repaired']} repaired" if applied else f", {yi['repairable']} repairable"
        print(f"  ! Corrupt tracking records: {yi['bad']}{tail}")
        for finding in yi["files"]:
            mark = (
                "fixed"
                if finding["repaired"]
                else ("repairable" if finding["repairable"] else "manual")
            )
            print(f"      - {finding['file']} [{mark}] {finding['error']}")
    else:
        print("  \u2713 Tracking records parse cleanly")

    backfill = report["backfill"]
    if applied:
        print(
            f"  \u2713 Backfill: {backfill['registry']} registry + "
            f"{backfill['titles']} title record(s) updated "
            f"({backfill['sessions']} session(s))"
        )
    else:
        print(
            f"  \u2022 Backfill candidates: {backfill['worktrees']} worktree(s) "
            f"w/ discoverable sessions, {backfill['titles']} missing title(s)"
        )

    reciprocal = report.get("reciprocal_metadata", {})
    controllers = reciprocal.get("legacy_controllers", {})
    projections = reciprocal.get("projections", {})
    controller_candidates = controllers.get("candidates", 0)
    projection_repairable = projections.get("repairable", 0)
    projection_repaired = projections.get("repaired", 0)
    projection_report_only = projections.get("report_only", 0)
    projection_remaining = projections.get("remaining", 0)
    if applied:
        print(
            f"  {chk} Reciprocal metadata: "
            f"{controllers.get('repaired', 0)} legacy controller record(s), "
            f"{projection_repaired} projection relation(s) repaired; "
            f"{projection_report_only} report-only"
        )
    else:
        print(
            f"  \u2022 Reciprocal metadata candidates: "
            f"{controller_candidates} legacy controller record(s), "
            f"{projection_repairable} projection relation(s) repairable, "
            f"{projection_report_only} report-only"
        )
    if projection_remaining:
        print(
            f"      {projection_remaining} projection relation(s) remain outside this run's budget"
        )

    pairs = report.get(
        "pair_integrity",
        {"found": 0, "repairable": 0, "repaired": 0, "items": []},
    )
    if pairs["found"]:
        print(
            f"  {chk if applied and pairs['repaired'] == pairs['found'] else '!'} "
            f"Pair registry placement: {pairs['found']} finding(s), "
            f"{pairs['repaired'] if applied else pairs['repairable']} "
            f"{'repaired' if applied else 'repairable'}"
        )
        for item in pairs["items"][:8]:
            mark = (
                "fixed" if item["repaired"] else ("needs --fix" if item["repairable"] else "manual")
            )
            print(f"      - {item['worktree_id']} [{mark}] {item['detail']}")
    else:
        print(f"  {chk} Pair records are stored in their owning project registries")

    head_cache = report.get("head_cache", {"found": 0, "fixed": 0, "ids": []})
    if head_cache["found"]:
        print(
            f"  {chk if applied else '!'} Stale head cache: {head_cache['found']} "
            f"{'fixed' if applied else 'found'} -> "
            f"{', '.join(head_cache['ids'][:8])}"
        )
    else:
        print(f"  {chk} Head caches match the transition ledger")

    stale_status = report["stale_status"]
    if stale_status["found"]:
        print(
            f"  {chk if applied else '!'} Stale status "
            f"(active + completed_at): {stale_status['found']} "
            f"{'fixed' if applied else 'found'} -> {', '.join(stale_status['ids'][:8])}"
        )
    else:
        print(f"  {chk} No stale statuses")

    empty_sessions = report["empty_sessions"]
    if empty_sessions["count"]:
        if gc_applied:
            print(
                f"  \u2713 Empty session shells: removed {empty_sessions['removed_dirs']} "
                f"dir(s), purged {empty_sessions['removed_rows']} store row(s)"
            )
        else:
            hint = "" if applied else " (needs --fix --gc-sessions)"
            print(f"  \u2022 Empty session shells: {empty_sessions['count']} candidate(s){hint}")
    else:
        print("  \u2713 No orphaned empty session shells")

    misaligned = report["misaligned"]
    if misaligned["count"]:
        print(
            f"  \u2022 Alignment audit: {misaligned['count']} session-less "
            f"worktree(s) point at a foreign parent cwd "
            f"(resume handled by Fix; informational)"
        )
    else:
        print("  \u2713 No worktree/path misalignment")

    orphaned_handoffs = report.get("orphaned_handoffs", {"found": 0, "items": []})
    if orphaned_handoffs["found"]:
        verb = "re-activated" if applied else "found"
        print(
            f"  {chk if applied else '!'} Orphaned handoffs "
            f"(head lost to a failed cutover): {orphaned_handoffs['found']} {verb}"
        )
        for item in orphaned_handoffs["items"][:8]:
            mark = (
                "fixed"
                if item.get("reactivated")
                else ("re-activate" if applied else "needs --fix")
            )
            stalled = ""
            if item.get("last_stage"):
                stalled = (
                    f"; last observed stage {item['last_stage']} "
                    f"({item.get('last_stage_name') or '?'})"
                )
            print(
                f"      - {item['worktree_id']}  <- {item['session_id'][:8]} "
                f"(idle {item['age_h']}h{stalled}) [{mark}]"
            )
    else:
        print(f"  {chk} No orphaned handoffs")

    bare_orphans = report.get("bare_orphans", {"count": 0, "items": []})
    if bare_orphans["count"]:
        print(
            f"  \u2022 Bare (un-muxed) Copilot orphan(s): {bare_orphans['count']} "
            f"machine-wide (invisible to the mux fleet view)"
        )
        for item in bare_orphans["items"][:8]:
            worktree_id = item.get("worktree_id") or "?"
            print(f"      - {item['session_id'][:8]}  pid {item['pid']:<6} {worktree_id}")
        print("      reclaim: agent-worktrees reclaim --worktree-id <id> --bare-only  (or --all)")
    else:
        print("  \u2713 No bare (un-muxed) Copilot orphans")

    lag = report.get("runtime_lag") or []
    if lag:
        print(f"  ! Runtime version lag: {len(lag)} service(s) serving older code than installed")
        for entry in lag:
            print(
                f"      - {entry['service']}: running {entry['running']} but "
                f"{entry['payload']} installed -> "
                f"{entry['service']} service restart"
            )
        print(
            "      (a new launch heals this automatically; restart to "
            "converge this running session sooner)"
        )
    else:
        print("  \u2713 Runtime services match installed payload")

    _render_dropin_registry_report("Picker pivots", report.get("pivots") or {})
    pruned = (report.get("pivots") or {}).get("pruned") or {}
    if pruned.get("found"):
        removed = pruned.get("removed", 0)
        found = pruned.get("found", 0)
        if prune_pivots_applied:
            print(f"      pruned {removed}/{found} stale pivot manifest(s)")
            for item in pruned.get("items", []):
                if not item.get("removed"):
                    print(f"        - kept {item['entry']}: {item.get('error', 'not removed')}")
        else:
            print(
                f"      {found} pivot finding(s) are prunable -- "
                "run `doctor --fix --prune-pivots` to remove them"
            )
    _render_dropin_registry_report("Project config.d", report.get("config_d") or {})


def _render_dropin_registry_report(label: str, report: dict) -> None:
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


def cmd_reconcile_binstubs(args) -> int:
    """Reconcile ~/.local/bin project binstubs against the projects registry."""
    try:
        if getattr(args, "transfer", None):
            inst.transfer_project_binstub(args.transfer)
        elif getattr(args, "remove", None):
            inst.remove_project_binstub(args.remove)
        else:
            inst.reconcile_binstubs()
    except inst.BinstubOwnershipError as exc:
        output.err(str(exc))
        return 1
    return 0


def cmd_register_project_entry(args) -> int:
    """Write a lean projects.yaml entry for both platform installers."""
    project = args.project
    repo_dir = getattr(args, "repo_dir", None)
    if inst.is_reserved_project_command(project):
        output.skipped(f"'{project}' is the runtime itself, not a project")
        return 0
    with inst.project_binstub_registration(project, repo_dir=repo_dir) as binstub_registration:
        if repo_dir:
            try:
                from . import repos as _repos

                existing_repo = _repos.find_repo(project)
                repo_class = (
                    existing_repo.repo_class
                    if existing_repo is not None and existing_repo.repo_class != "reference"
                    else "worktree"
                )
                remote = ""
                result = subprocess.run(
                    ["git", "-C", repo_dir, "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    remote = result.stdout.strip()
                requested_exposure = getattr(args, "expose_agent", None)
                repo_exposure = (
                    requested_exposure
                    if requested_exposure is not None
                    else existing_repo.agent
                    if existing_repo is not None
                    else True
                )
                _repos.add_repo(
                    project,
                    repo_dir,
                    repo_class=repo_class,
                    remote=remote,
                    agent=repo_exposure,
                    plat=cfg.detect_platform(),
                )
            except Exception as exc:
                output.err(f"Could not record repository identity for {project}: {exc}")
                return 1
        expose = getattr(args, "expose_agent", None)
        if expose is None:
            try:
                from . import repos as _repos

                entry = _repos.find_repo(project)
                if entry is not None:
                    expose = entry.agent
            except Exception:
                expose = None

        inst.register_project(
            project,
            repo_dir=repo_dir,
            expose_agent=expose,
            base_repo=getattr(args, "base_repo", None),
            elevated=getattr(args, "elevated", None),
            display_name=getattr(args, "display_name", None),
            wsl_state=getattr(args, "wsl_state", None),
            wsl_distro=getattr(args, "wsl_distro", None),
            wsl_path=getattr(args, "wsl_path", None),
        )
        binstub_registration.commit()
    return 0


def cmd_anchor_check(args) -> int:
    """Check anchor repo for uncommitted work and stash entries."""
    from . import anchor_hygiene

    repo_path = getattr(args, "repo_path", None) or os.getcwd()
    use_json = getattr(args, "json", False)
    quiet = getattr(args, "quiet", False)
    strict = getattr(args, "strict", False)
    fetch = getattr(args, "fetch", False)

    try:
        report = anchor_hygiene.check_anchor(repo_path, fetch=fetch)
    except Exception as exc:
        if use_json:
            json.dump({"version": 1, "error": str(exc)}, sys.stdout)
            print()
        else:
            output.err(f"Anchor check failed: {exc}")
        return 1

    if use_json:
        json.dump(anchor_hygiene.report_as_json(report), sys.stdout, indent=2)
        print()
    else:
        anchor_hygiene.report_anchor_state(report, quiet=quiet)

    if strict and not report.is_clean:
        return 1
    return 0


def cmd_config_migrate(args) -> int:
    """Migrate machine-local config schemas in place."""
    from . import config_migrations

    quiet = getattr(args, "quiet", False)
    if not config_migrations.available():
        if not quiet:
            output.warn("config-migrate: migration library unavailable; skipping")
        return 0

    results = config_migrations.run_migrations()
    if not quiet:
        print(config_migrations.summarize(results))
    return 0
