"""``agent-logger`` top-level CLI.

Subcommands are added as the plugin grows. It exposes version, configuration,
and repository organization introspection; the segmenter ships its own scripts
(``collate-session`` etc.).
"""

from __future__ import annotations

import argparse
import json
import sys

from agent_logger._build_info import BUILT_AT, COMMIT, __version__
from agent_logger.config import RepositoryConfigError, load_config


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"agent-logger {__version__} (commit {COMMIT}, built {BUILT_AT})")
    return 0


def _cmd_config(_args: argparse.Namespace) -> int:
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
    block = cfg.chronicle
    summary = {
        "enabled": cfg.chronicle_enabled,
        "settle_seconds": cfg.chronicle_settle_seconds,
        "corpus_root": str(cfg.chronicle_corpus_root),
        "db_path": str(cfg.chronicle_db_path),
        "manifests_dir": str(cfg.chronicle_manifests_dir),
        "default_sink": block.get("default_sink"),
        "routes": block.get("routes", []),
        "sinks": sorted((block.get("sinks", {}) or {}).keys()),
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
    p_config.set_defaults(func=_cmd_config)

    p_organization = sub.add_parser(
        "organization",
        help="show repository organization config as manifest-ready JSON",
    )
    p_organization.set_defaults(func=_cmd_organization)

    p_migrate = sub.add_parser(
        "config-migrate", help="migrate machine-local config.yaml schema (idempotent)"
    )
    p_migrate.set_defaults(func=_cmd_config_migrate)

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
