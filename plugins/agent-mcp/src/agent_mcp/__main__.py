"""CLI entry point for agent-mcp.

Subcommands:
  bridge <name|--config FILE>   Run the stdio MCP bridge from a config file.
  validate <name|FILE>          Parse + schema-check a bridge config (no run).
  status                        Show prerequisites and available bridges.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys

from . import __version__
from .bridge import Bridge
from .config import BRIDGES_DIR, ConfigError, load_config


def _configure_logging(level: str) -> None:
    # Logs go to stderr -- stdout is the JSON-RPC channel and must stay clean.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
        format="agent-mcp: %(name)s: %(message)s",
    )


def _cmd_bridge(args: argparse.Namespace) -> int:
    target = args.config or args.name
    if not target:
        print("agent-mcp bridge: a bridge name or --config FILE is required", file=sys.stderr)
        return 2
    try:
        cfg = load_config(target)
    except ConfigError as exc:
        print(f"agent-mcp: {exc}", file=sys.stderr)
        return 1
    return asyncio.run(Bridge(cfg).run())


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.name)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    where = cfg.source_path or args.name
    auth_desc = "+".join(a.kind for a in cfg.auths)
    print(f"OK: {where} -- {cfg.server.type} -> "
          f"{cfg.server.url or ' '.join(cfg.server.command)} (auth: {auth_desc})")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    print(f"agent-mcp {__version__}")
    print("prerequisites:")
    for tool in ("python", "az", "gh", "git"):
        found = shutil.which(tool)
        print(f"  [{'OK ' if found else '-- '}] {tool}: {found or 'not found'}")
    print(f"bridges dir: {BRIDGES_DIR}")
    if BRIDGES_DIR.is_dir():
        files = sorted(
            p for p in BRIDGES_DIR.iterdir()
            if p.suffix.lower() in (".yaml", ".yml", ".json")
        )
        if files:
            for p in files:
                print(f"  - {p.stem} ({p.name})")
        else:
            print("  (no bridge config files)")
    else:
        print("  (directory does not exist yet)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"agent-mcp {__version__}")
    parser.add_argument("--log-level", default="info",
                        help="logging level (debug/info/warning/error)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bridge = sub.add_parser("bridge", help="run the stdio MCP bridge")
    p_bridge.add_argument("name", nargs="?", help="bridge name under ~/.agent-mcp/bridges/")
    p_bridge.add_argument("--config", help="explicit path to a bridge config file")
    p_bridge.set_defaults(func=_cmd_bridge)

    p_validate = sub.add_parser("validate", help="validate a bridge config")
    p_validate.add_argument("name", help="bridge name or path to a config file")
    p_validate.set_defaults(func=_cmd_validate)

    p_status = sub.add_parser("status", help="show prerequisites and bridges")
    p_status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
