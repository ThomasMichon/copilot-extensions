"""``agent-logger`` top-level CLI.

Subcommands are added as the plugin grows. It exposes version, configuration,
and repository organization introspection; the segmenter ships its own scripts
(``collate-session`` etc.).
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys

import yaml

from agent_logger._build_info import BUILT_AT, COMMIT, __version__
from agent_logger.aggregate import (
    ExecutionMode,
    FileSystemAggregateInputProvider,
    Finding,
    MachineIdentity,
    ResolvedPlan,
    compile_from_provider,
)
from agent_logger.config import RepositoryConfigError, home_dir, load_config
from agent_logger.segmenter.platform import detect_machine


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"agent-logger {__version__} (commit {COMMIT}, built {BUILT_AT})")
    return 0


def _load_aggregate_plan() -> ResolvedPlan:
    home = home_dir()
    machine = MachineIdentity(
        name=detect_machine(),
        platform=_platform_name(),
    )
    try:
        legacy = load_config(home=home, include_repo=False)
        raw_machine = legacy.get("machine", {})
        if not isinstance(raw_machine, dict):
            raise ValueError("machine configuration must be a mapping")
        machine = MachineIdentity(
            name=_machine_text(legacy.machine_name, "name") or machine.name,
            platform=machine.platform,
            role=_machine_text(legacy.machine_role, "role"),
        )
        return compile_from_provider(
            FileSystemAggregateInputProvider(machine=machine, home=home)
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, yaml.YAMLError):
        return ResolvedPlan(
            machine=machine,
            mode=ExecutionMode.OBSERVE,
            findings=[
                Finding(
                    code="invalid-machine-configuration",
                    message="machine aggregate configuration could not be loaded",
                )
            ],
            authorized=False,
            passive=True,
        )


def _platform_name() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "macos"
    if name == "windows":
        return "windows"
    return "linux"


def _machine_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"machine.{field_name} must be null or a non-empty string")
    return value.strip()


def _print_aggregate_plan(plan: ResolvedPlan, *, canonical: bool) -> None:
    if canonical:
        print(plan.canonical_json())
    else:
        print(json.dumps(plan.as_dict(), indent=2, sort_keys=True))


def _cmd_config(args: argparse.Namespace) -> int:
    if getattr(args, "resolved", False):
        plan = _load_aggregate_plan()
        _print_aggregate_plan(plan, canonical=bool(getattr(args, "json", False)))
        return 0 if plan.authorized else 2

    cfg = load_config()
    summary = {
        "home": str(cfg.home),
        "store_dir": str(cfg.store_dir),
        "sync_target": cfg.sync_target,
        "sync_path": str(cfg.sync_path),
        "repo_config_path": str(cfg.repo_config_path) if cfg.repo_config_path else None,
        "log_root": str(cfg.log_root),
        "log_path_template": cfg.log_path_template,
        "log_template_configured": cfg.log_template is not None,
        "narration_style_configured": cfg.narration_style is not None,
        "exemplars_configured": cfg.exemplars is not None,
        "closing_remark_configured": cfg.closing_remark is not None,
        "voice_pack": cfg.voice_pack,
        "note_marker": cfg.note_marker,
        "machine_name": cfg.machine_name,
    }
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    plan = _load_aggregate_plan()
    if getattr(args, "json", False):
        _print_aggregate_plan(plan, canonical=True)
    else:
        state = (
            "passive"
            if plan.authorized and plan.passive
            else "authorized"
            if plan.authorized
            else "invalid"
        )
        print(f"aggregate configuration: {state}")
        for finding in plan.as_dict()["findings"]:
            print(f"- {finding['code']}: {finding['message']}")
    return 0 if plan.authorized else 2


def _cmd_organization(_args: argparse.Namespace) -> int:
    cfg = load_config()
    result = {
        "repository_root": str(cfg.repo_root) if cfg.repo_root else None,
        "config_path": str(cfg.repo_config_path) if cfg.repo_config_path else None,
        "manifest": cfg.organization_manifest(),
    }
    print(json.dumps(result, indent=2))
    return 0


def _cmd_chronicle_status(_args: argparse.Namespace) -> int:
    cfg = load_config()
    aggregate_plan = _load_aggregate_plan()
    block = cfg.chronicle
    summary = {
        "enabled": cfg.chronicle_enabled,
        "settle_seconds": cfg.chronicle_settle_seconds,
        "corpus_root": str(cfg.chronicle_corpus_root),
        "db_path": str(cfg.chronicle_db_path),
        "manifests_dir": str(cfg.chronicle_manifests_dir),
        "default_sink": block.get("default_sink"),
        "routes": block.get("routes", []),
        "skip_repositories": block.get("skip_repositories", []),
        "sinks": sorted((block.get("sinks", {}) or {}).keys()),
        "aggregate": aggregate_plan.as_dict(),
    }
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_chronicle_scan(_args: argparse.Namespace) -> int:
    from agent_logger.chronicle.digest import group_by_day
    from agent_logger.chronicle.factory import build_chronicler

    cfg = load_config()
    chronicler = build_chronicler(cfg)
    sessions = chronicler.source.scan()
    digests = group_by_day(sessions, chronicler.router)
    result = {
        "scanned": len(sessions),
        "digests": [
            {
                "sink": d.sink_id,
                "day": d.day,
                "sessions": len(d.sessions),
                "journaled_now": False,
            }
            for d in digests
        ],
    }
    print(json.dumps(result, indent=2))
    return 0


def _cmd_chronicle_tick(_args: argparse.Namespace) -> int:
    from agent_logger.chronicle.factory import build_chronicler

    cfg = load_config()
    if not cfg.chronicle_enabled and not getattr(_args, "force", False):
        print(
            json.dumps(
                {"skipped": "chronicle disabled (set chronicle.enabled or --force)"}
            )
        )
        return 0
    chronicler = build_chronicler(cfg)
    result = chronicler.run_once()
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def _cmd_session_fetch(args: argparse.Namespace) -> int:
    """agent-bridge cold-store-provider verb: resolve one session by id.

    Exit ``0`` with a JSON ``{"session": {...}, "events": [...]}`` payload on
    stdout when found, ``cold_store.NOT_FOUND_EXIT_CODE`` (never an error) when
    this host has no evidence of the session at all. See
    ``agent_logger.cold_store`` for the resolution order.
    """
    from agent_logger.cold_store import fetch_session_json

    code, payload = fetch_session_json(args.session_id)
    if payload:
        print(payload)
    return code


def _cmd_catalog_status(_args: argparse.Namespace) -> int:
    """Report the resolved review-annotation catalog index db path."""
    cfg = load_config()
    print(json.dumps({"db_path": str(cfg.catalog_db_path)}, indent=2))
    return 0


def _cmd_catalog_rebuild(args: argparse.Namespace) -> int:
    """Rebuild the review-annotation catalog index from every discoverable
    session's own ``review-annotations.json`` sidecar.

    Additive and idempotent -- safe to run against an index that already has
    rows (e.g. one populated incrementally by a production annotation writer
    passing ``index=default_index()``), and the only path that seeds the
    catalog for annotations written *before* this index existed, or by a
    caller that never wired ``index=`` at all. Run this periodically (a cron
    tick / scheduled task, mirroring ``chronicle tick``) when no production
    writer passes ``index=`` explicitly.
    """
    from pathlib import Path

    from agent_logger.catalog import default_index, rebuild_from_sidecars
    from agent_logger.segmenter.collate import find_copilot_dir, session_archive_stores
    from agent_logger.sessions import SESSION_STATE_SUBDIR

    cfg = load_config()
    index = default_index(cfg)
    state_root = Path(args.state_root) if args.state_root else (
        find_copilot_dir() / SESSION_STATE_SUBDIR
    )
    archive_stores = list(session_archive_stores())
    scanned = rebuild_from_sidecars(index, state_root, *archive_stores)
    print(json.dumps({"scanned": scanned, "db_path": str(cfg.catalog_db_path)}))
    return 0


def _cmd_catalog_query(args: argparse.Namespace) -> int:
    """Read-side cross-repo query verb: given ``(repo, pr_number)``, return
    every session this host can resolve for it, same process-boundary shape
    ``annotate`` establishes for the write side (a caller in a different
    repository, e.g. a downstream review-link fallback chain, shells out
    to this CLI rather than importing agent-logger as a library).

    Backed by :func:`agent_logger.cold_store.query_reviewer_sessions` --
    see that function's docstring for the resolution/ordering/dedup
    contract. Always exits ``0``; an empty catalog, a query that matches
    nothing this host can resolve, and a catalog storage failure (a corrupt
    or unavailable SQLite database, or a ``--pr-number`` too large for
    SQLite's 64-bit ``INTEGER`` column) all print ``{"sessions": []}``
    rather than raising -- this is a best-effort fallback-tier read for a
    caller that has already exhausted its own faster resolution paths, so a
    storage-layer hiccup here must degrade to "nothing found", never an
    uncaught traceback.
    """
    import sqlite3

    from agent_logger.cold_store import query_reviewer_sessions

    try:
        refs = query_reviewer_sessions(
            args.repo, args.pr_number, since=args.since, until=args.until
        )
    except (OSError, sqlite3.Error, OverflowError):
        refs = []
    print(
        json.dumps(
            {
                "repo": args.repo,
                "pr_number": args.pr_number,
                "sessions": [{"session_id": ref.id, "kind": ref.kind} for ref in refs],
            }
        )
    )
    return 0


def _cmd_catalog_annotate(args: argparse.Namespace) -> int:
    """Write one review annotation for a LIVE local session, across a
    process boundary -- the same cross-repo integration shape ``session-fetch``
    already establishes (a caller in a different repository, e.g. an external
    reviewer-identity backfill tool, shells out to this CLI rather than
    importing agent-logger as a library).

    Only ever resolves the local live session-state tier (never an archive):
    :func:`agent_logger.sessions.write_review_annotation` requires a live
    session directory to mutate, matching its own contract. Rejects an unsafe
    ``session_id`` (path separator, ``..``, absolute anchor), a session
    directory that resolves through a symlink/reparse point, and any real
    directory lacking the live-session marker
    (:data:`agent_logger.sessions.EVENTS_MEMBER`) -- the same checks
    :mod:`agent_logger.sessions`/:mod:`agent_logger.cold_store` already apply
    to a read, since a process-boundary caller here is just as untrusted as
    one there. Exits non-zero with a clear message when the session id isn't
    found locally or isn't safe, or the write itself fails (a lock timeout, a
    catalog database error, or a ``--pr-number`` too large for SQLite's
    64-bit ``INTEGER`` column -- the sidecar write may already have succeeded
    even if the catalog side then fails, so the caller should know about it
    rather than have it silently discarded). A malformed *existing* sidecar
    is not a failure here: :func:`write_review_annotation` treats it the same
    as a missing one and simply overwrites it with a fresh, well-formed
    entry.
    """
    import sqlite3

    from agent_logger.catalog import default_index
    from agent_logger.cold_store import _is_safe_session_id
    from agent_logger.segmenter.collate import find_copilot_dir
    from agent_logger.sessions import (
        EVENTS_MEMBER,
        SESSION_STATE_SUBDIR,
        write_review_annotation,
    )
    from agent_logger.sync.provenance import existing_real_directory

    if not _is_safe_session_id(args.session_id):
        print(f"error: {args.session_id!r} is not a valid session id", file=sys.stderr)
        return 1
    try:
        state_root = find_copilot_dir() / SESSION_STATE_SUBDIR
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    session_dir = state_root / args.session_id
    real_session_dir = existing_real_directory(session_dir)
    if real_session_dir is None or not (real_session_dir / EVENTS_MEMBER).is_file():
        print(
            f"error: no local live session directory for {args.session_id!r} "
            f"(looked under {state_root})",
            file=sys.stderr,
        )
        return 1

    cfg = load_config()
    try:
        write_review_annotation(
            session_dir,
            repo=args.repo,
            pr_number=args.pr_number,
            role=args.role,
            recorded_at=args.recorded_at,
            index=default_index(cfg),
        )
    except (OSError, TimeoutError, sqlite3.Error, OverflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "session_id": args.session_id,
                "repo": args.repo,
                "pr_number": args.pr_number,
                "role": args.role,
            }
        )
    )
    return 0


def _cmd_origin_backfill_local(args: argparse.Namespace) -> int:
    """Backfill origin.json across the LOCAL session store (this machine)."""
    from pathlib import Path

    from agent_logger.sync.origin import mark_all

    cfg = load_config()
    harness = list(args.harness_repo) if args.harness_repo else cfg.sync_harness_repos
    source = Path(args.source).expanduser() if args.source else cfg.sync_path
    machine = args.machine or cfg.machine_name or "unknown"
    summary = mark_all(source, machine, harness, dry_run=args.dry_run)
    print(json.dumps(
        {"mode": "local", "source": str(source), "machine": machine,
         "harness_repos": harness, "dry_run": args.dry_run, **summary},
        indent=2,
    ))
    return 0


def _cmd_origin_backfill_corpus(args: argparse.Namespace) -> int:
    """Backfill origin.json across a multi-machine synced corpus (e.g. the NAS)."""
    from pathlib import Path

    from agent_logger.sync.origin import backfill_corpus

    cfg = load_config()
    harness = list(args.harness_repo) if args.harness_repo else cfg.sync_harness_repos
    root = Path(args.root).expanduser()
    summary = backfill_corpus(root, harness, dry_run=args.dry_run)
    print(json.dumps(
        {"mode": "corpus", "root": str(root), "harness_repos": harness,
         "dry_run": args.dry_run, **summary},
        indent=2,
    ))
    return 0


def _cmd_tenants_list(args: argparse.Namespace) -> int:
    """Discover the adopted-repo tenants resolved for this machine."""
    from agent_logger import tenancy

    machine = getattr(args, "machine", None) or detect_machine()
    tenants = tenancy.discover_tenants(machine=machine)
    payload = {
        "machine": machine,
        "tenants": [
            {
                "id": t.tenant_id,
                "repo": t.repo_name,
                "repo_path": str(t.repo_path),
                "roles": list(t.roles),
                "enabled": t.enabled,
                "config_path": str(t.config_path),
                "scope": t.scope_summary(),
                "advisories": list(t.advisories),
            }
            for t in tenants
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_tenants_sync(args: argparse.Namespace) -> int:
    """Run one sync pass per adopted source tenant (the orchestrated tick).

    With no adopted tenants (no agent-worktrees registry, or no repo declares a
    tenant), fall back to the legacy single-home ``run_sync`` so a scheduler
    wired to ``tenants sync`` keeps syncing on a plain single-tenant install.
    """
    from agent_logger import tenancy

    machine = getattr(args, "machine", None) or detect_machine()
    tenants = tenancy.discover_tenants(machine=machine)
    if not tenants:
        from agent_logger.config import load_config
        from agent_logger.sync.engine import run_sync

        code = run_sync(
            load_config(),
            dry_run=bool(getattr(args, "dry_run", False)),
            prune=bool(getattr(args, "prune", False)),
        )
        print(json.dumps({"machine": machine, "tenants": [], "fallback": "single-tenant",
                          "exit": code}, indent=2))
        return code
    result = tenancy.run_all(
        tenants,
        roles=("source",),
        dry_run=bool(getattr(args, "dry_run", False)),
        prune=bool(getattr(args, "prune", False)),
        machine=machine,
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if all(o.status != "failed" for o in result.outcomes) else 1


def _cmd_config_migrate(_args: argparse.Namespace) -> int:
    """Migrate the machine-local config.yaml schema in place (idempotent + atomic)."""
    from agent_logger import config_migrations

    if not config_migrations.available():
        print("config-migrate: migration library unavailable; skipping")
        return 0
    print(config_migrations.summarize(config_migrations.run_migrations()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-logger", description=__doc__)
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    p_version = sub.add_parser("version", help="print version")
    p_version.set_defaults(func=_cmd_version)

    p_config = sub.add_parser("config", help="show resolved configuration")
    p_config.add_argument(
        "--resolved",
        action="store_true",
        help="show the resolved aggregate machine plan",
    )
    p_config.add_argument(
        "--json",
        action="store_true",
        help="emit canonical compact JSON (with --resolved)",
    )
    p_config.set_defaults(func=_cmd_config)

    p_doctor = sub.add_parser(
        "doctor",
        help="validate aggregate configuration without side effects",
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        help="emit the resolved aggregate plan as canonical JSON",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_organization = sub.add_parser(
        "organization",
        help="show repository organization config as manifest-ready JSON",
    )
    p_organization.set_defaults(func=_cmd_organization)

    p_session_fetch = sub.add_parser(
        "session-fetch",
        help="agent-bridge cold-store-provider verb: resolve one session by id",
    )
    p_session_fetch.add_argument("session_id", help="session id to resolve")
    p_session_fetch.add_argument(
        "--json", action="store_true",
        help="present for contract parity with agent-bridge's invocation "
        "(output is always JSON)",
    )
    p_session_fetch.set_defaults(func=_cmd_session_fetch)

    p_annotate = sub.add_parser(
        "annotate",
        help="write one review annotation for a local live session (cross-repo "
        "callers use this instead of importing agent-logger as a library)",
    )
    p_annotate.add_argument("session_id", help="the local live session id to annotate")
    p_annotate.add_argument("--repo", required=True, help="e.g. owner/name")
    p_annotate.add_argument("--pr-number", required=True, type=int)
    p_annotate.add_argument("--role", default="reviewer")
    p_annotate.add_argument(
        "--recorded-at",
        help="ISO-8601 timestamp (default: now)",
    )
    p_annotate.set_defaults(func=_cmd_catalog_annotate)

    p_migrate = sub.add_parser(
        "config-migrate", help="migrate machine-local config.yaml schema (idempotent)"
    )
    p_migrate.set_defaults(func=_cmd_config_migrate)

    p_catalog = sub.add_parser(
        "catalog",
        help="review-annotation catalog index -- (repo, pr_number) -> sessions",
    )
    cat_sub = p_catalog.add_subparsers(dest="catalog_command", required=True)
    cat_status = cat_sub.add_parser(
        "status", help="show the resolved catalog index db path"
    )
    cat_status.set_defaults(func=_cmd_catalog_status)
    cat_rebuild = cat_sub.add_parser(
        "rebuild",
        help="rebuild the index from every session's review-annotations.json "
        "sidecar (additive, idempotent)",
    )
    cat_rebuild.add_argument(
        "--state-root",
        help="live session-state root (default: this host's ~/.copilot/session-state)",
    )
    cat_rebuild.set_defaults(func=_cmd_catalog_rebuild)
    cat_query = cat_sub.add_parser(
        "query",
        help="(repo, pr_number) -> resolvable sessions -- the cross-repo read "
        "side fallback tier (e.g. a downstream review-link fallback chain)",
    )
    cat_query.add_argument("--repo", required=True, help="e.g. owner/name")
    cat_query.add_argument("--pr-number", required=True, type=int)
    cat_query.add_argument(
        "--since", help="ISO-8601 lower bound on the annotation's recorded_at"
    )
    cat_query.add_argument(
        "--until", help="ISO-8601 upper bound on the annotation's recorded_at"
    )
    cat_query.set_defaults(func=_cmd_catalog_query)

    p_origin = sub.add_parser(
        "origin", help="session origin sidecars -- backfill/tag existing sessions"
    )
    o_sub = p_origin.add_subparsers(dest="origin_command", required=True)
    o_local = o_sub.add_parser(
        "backfill-local",
        help="tag the LOCAL session store (~/.copilot) with derived origins",
    )
    o_local.add_argument(
        "--source", help="session store root (default: configured sync source)"
    )
    o_local.add_argument("--machine", help="machine name (default: configured/auto)")
    o_local.add_argument(
        "--harness-repo", action="append", metavar="REPO",
        help="harness repo name (repeatable; default: configured sync.harness_repos)",
    )
    o_local.add_argument(
        "--dry-run", action="store_true", help="derive + count without writing"
    )
    o_local.set_defaults(func=_cmd_origin_backfill_local)
    o_corpus = o_sub.add_parser(
        "backfill-corpus",
        help="tag a multi-machine synced corpus (<root>/<machine>/session-state/)",
    )
    o_corpus.add_argument("--root", required=True, help="corpus root (e.g. the NAS)")
    o_corpus.add_argument(
        "--harness-repo", action="append", metavar="REPO",
        help="harness repo name (repeatable; default: configured sync.harness_repos)",
    )
    o_corpus.add_argument(
        "--dry-run", action="store_true", help="derive + count without writing"
    )
    o_corpus.set_defaults(func=_cmd_origin_backfill_corpus)

    p_chronicle = sub.add_parser(
        "chronicle", help="background chronicling -- the orchestrator daemon"
    )
    c_sub = p_chronicle.add_subparsers(dest="chronicle_command", required=True)
    c_status = c_sub.add_parser("status", help="show resolved chronicle config")
    c_status.set_defaults(func=_cmd_chronicle_status)
    c_scan = c_sub.add_parser(
        "scan", help="dry-run: list the daily digests that would be produced"
    )
    c_scan.set_defaults(func=_cmd_chronicle_scan)
    c_tick = c_sub.add_parser("tick", help="run one chronicle pass (the scheduled job)")
    c_tick.add_argument(
        "--force", action="store_true", help="run even when chronicle.enabled is false"
    )
    c_tick.set_defaults(func=_cmd_chronicle_tick)

    p_tenants = sub.add_parser(
        "tenants",
        help="multi-tenant orchestration -- adopted-repo source/sink daemons",
    )
    t_sub = p_tenants.add_subparsers(dest="tenants_command", required=True)
    t_list = t_sub.add_parser(
        "list", help="show the adopted-repo tenants resolved for this machine"
    )
    t_list.add_argument("--machine", help="machine name (default: auto-detected)")
    t_list.set_defaults(func=_cmd_tenants_list)
    t_sync = t_sub.add_parser(
        "sync", help="run one sync pass per adopted source tenant (fan-out tick)"
    )
    t_sync.add_argument("--machine", help="machine name (default: auto-detected)")
    t_sync.add_argument(
        "--dry-run", action="store_true", help="resolve + report without syncing"
    )
    t_sync.add_argument(
        "--prune", action="store_true", help="prune destinations after each push"
    )
    t_sync.set_defaults(func=_cmd_tenants_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        return _cmd_version(args)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except RepositoryConfigError as exc:
        print(f"agent-logger: invalid repository configuration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
