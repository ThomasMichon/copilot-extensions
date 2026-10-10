"""Standalone entry point; help, version and configuration never import the core."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import __version__
from .config import ConfigurationError, load_config


def _timeout(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-index-service")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    release = commands.add_parser("release", help="describe or verify a pinned wheel bundle")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    describe = release_commands.add_parser("describe")
    describe.add_argument("--bundle", required=True, type=Path)
    describe.add_argument("--source-commit", required=True)
    verify = release_commands.add_parser("verify")
    verify.add_argument("--descriptor", required=True, type=Path)
    verify.add_argument("--expected-source-commit", required=True)
    for name in ("serve", "start", "status", "deploy", "config"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", required=True, type=Path, help="explicit host YAML file")
        if name in ("serve", "start"):
            sub.add_argument("--passive", action="store_true")
        if name == "deploy":
            sub.add_argument("--health-timeout", type=_timeout, default=60.0)
            sub.add_argument("--drain-timeout", type=_timeout, default=300.0)
            sub.add_argument("--force", action="store_true")
            sub.add_argument("--recover", action="store_true")
            sub.set_defaults(json=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "release":
            from .release import build_descriptor, verify_descriptor

            if args.release_command == "describe":
                payload = build_descriptor(args.bundle, source_commit=args.source_commit)
            else:
                payload = {
                    "valid": True,
                    "release": verify_descriptor(
                        args.descriptor, expected_source_commit=args.expected_source_commit,
                    ),
                }
            print(json.dumps(payload, sort_keys=True))
            return 0
        config = load_config(args.config)
        if args.command == "config":
            print(json.dumps({
                "valid": True, "schema_version": 1, "home": str(config.home),
                "data": str(config.data), "routing": str(config.routing),
                "sources": [source["name"] for source in config.sources],
                "runtime_version": __version__,
            }, sort_keys=True))
            return 0
        from .composition import core_environment, load_core, require_installed_core

        with core_environment(config):
            core = load_core(native=args.command in {"serve", "start", "deploy"})
            if args.command in {"serve", "start"}:
                from agent_index.config import Config

                core.serve(Config(host="127.0.0.1", port=config.port), passive=args.passive)
                return 0
            if args.command == "deploy":
                require_installed_core()
                config.home.mkdir(parents=True, exist_ok=True)
                return core.cmd_deploy(args)
            payload = core._status_payload()
            payload["distribution"] = "agent-index-service"
            payload["invoked_version"] = __version__
            print(json.dumps(payload, sort_keys=True))
            return 0 if payload.get("running") is True else 1
    except (ConfigurationError, RuntimeError, ImportError, OSError, ValueError) as exc:
        print(f"agent-index-service: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
