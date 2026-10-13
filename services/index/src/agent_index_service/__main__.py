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
    container = commands.add_parser("container", help="prepare an offline generic Linux image context")
    container_commands = container.add_subparsers(dest="container_command", required=True)
    prepare = container_commands.add_parser("prepare")
    for flag in ("descriptor", "third-party-lock", "third-party-wheels", "output"):
        prepare.add_argument(f"--{flag}", required=True, type=Path)
    for flag in ("expected-source-commit", "base-image", "python-version", "platform", "glibc-version"):
        prepare.add_argument(f"--{flag}", required=True)
    prepare.add_argument("--uid", type=int, default=10001)
    prepare.add_argument("--gid", type=int, default=10001)
    build = container_commands.add_parser("build", help="verify and snapshot context before Docker")
    build.add_argument("--context", required=True, type=Path)
    build.add_argument("--expected-contract-sha256", required=True)
    build.add_argument("--docker", type=Path, help="explicit Docker executable; otherwise resolve once")
    build.add_argument("--docker-context", default="default")
    build.add_argument("--docker-config", type=Path, help="explicit client-only config; never copied")
    build.add_argument("--timeout", type=_timeout, default=600.0)
    release = commands.add_parser("release", help="describe or verify a pinned wheel bundle")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    describe = release_commands.add_parser("describe")
    describe.add_argument("--bundle", required=True, type=Path)
    describe.add_argument("--source-commit", required=True)
    verify = release_commands.add_parser("verify")
    verify.add_argument("--descriptor", required=True, type=Path)
    verify.add_argument("--expected-source-commit", required=True)
    stage = commands.add_parser("stage", help="build an immutable native candidate without activation")
    stage.add_argument("--config", required=True, type=Path)
    stage.add_argument("--install-root", required=True, type=Path)
    stage.add_argument("--descriptor", required=True, type=Path)
    stage.add_argument("--expected-source-commit", required=True)
    stage.add_argument("--python", required=True, type=Path)
    stage.add_argument("--uv", required=True, type=Path)
    stage.add_argument("--third-party-lock", required=True, type=Path)
    stage.add_argument("--uv-config", required=True, type=Path)
    stage.add_argument("--timeout", type=_timeout, default=600.0)
    candidates = commands.add_parser("candidates", help="inspect candidates without selecting a runtime")
    candidates.add_argument("--config", required=True, type=Path)
    candidates.add_argument("--install-root", required=True, type=Path)
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
        if args.command == "container":
            from .container import ContainerTarget, build_container_context, prepare_container_context

            if args.container_command == "build":
                payload = build_container_context(
                    args.context, expected_contract_sha256=args.expected_contract_sha256,
                    docker=args.docker, docker_context=args.docker_context,
                    docker_config=args.docker_config, timeout_seconds=args.timeout,
                )
            else:
                payload = prepare_container_context(
                    args.descriptor, expected_source_commit=args.expected_source_commit,
                    third_party_lock=args.third_party_lock,
                    third_party_wheels=args.third_party_wheels,
                    output=args.output, base_image=args.base_image,
                    target=ContainerTarget(args.python_version, args.platform, args.glibc_version),
                    uid=args.uid, gid=args.gid,
                )
            print(json.dumps(payload, sort_keys=True))
            return 0
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
        if args.command in {"stage", "candidates"}:
            from .staging import NativeBuildConfig, inspect_candidates, stage_candidate

            if args.command == "stage":
                payload = stage_candidate(
                    args.descriptor, expected_source_commit=args.expected_source_commit,
                    install_root=args.install_root, host_config_path=args.config,
                    build=NativeBuildConfig(
                        python=args.python, uv=args.uv, third_party_lock=args.third_party_lock,
                        uv_config=args.uv_config, timeout_seconds=args.timeout,
                    ),
                )
            else:
                payload = inspect_candidates(args.install_root, host_config_path=args.config)
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

                if config.store_layout is not None:
                    from agent_index.index_config import IndexConfig
                    from agent_index.store.source_layout import initialize_source_store

                    initialize_source_store(IndexConfig())
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
