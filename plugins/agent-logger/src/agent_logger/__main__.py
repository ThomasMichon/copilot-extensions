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

    p_migrate = sub.add_parser(
        "config-migrate", help="migrate machine-local config.yaml schema (idempotent)"
    )
    p_migrate.set_defaults(func=_cmd_config_migrate)

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
