"""CLI entry point -- ``agent-bridge start``, ``agent-bridge status``."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the agent-bridge server."""
    import uvicorn

    from .config import load_config, load_or_create_auth_token, write_default_config

    cfg = load_config()
    write_default_config(cfg)
    token = load_or_create_auth_token()

    # Apply CLI overrides
    if args.port:
        cfg.port = args.port
    if args.bind:
        cfg.bind = args.bind

    from .app import create_app

    app = create_app(config=cfg, token=token)

    print(f"[agent-bridge] Starting on {cfg.bind}:{cfg.port}")
    print(f"[agent-bridge] Auth token: {token[:8]}...")
    print(f"[agent-bridge] DB: {cfg.db_path}")

    uvicorn.run(
        app,
        host=cfg.bind,
        port=cfg.port,
        log_level=cfg.log_level,
    )


def _cmd_status(args: argparse.Namespace) -> None:
    """Check if agent-bridge is running."""
    import urllib.request
    import urllib.error

    from .config import load_config, load_or_create_auth_token

    cfg = load_config()
    token = load_or_create_auth_token()
    url = f"http://{cfg.bind}:{cfg.port}/health"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            print(f"[OK] agent-bridge is running on {cfg.bind}:{cfg.port}")
    except urllib.error.URLError:
        print(f"[FAIL] agent-bridge is not running on {cfg.bind}:{cfg.port}")
        sys.exit(1)


def _cmd_version(args: argparse.Namespace) -> None:
    print(f"agent-bridge {__version__}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent-bridge",
        description="Persistent inter-agent communication service",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command")

    # start
    start_p = sub.add_parser("start", help="Start the agent-bridge server")
    start_p.add_argument("--port", type=int, help="Port to listen on")
    start_p.add_argument("--bind", type=str, help="Address to bind to")
    start_p.set_defaults(func=_cmd_start)

    # status
    status_p = sub.add_parser("status", help="Check if agent-bridge is running")
    status_p.set_defaults(func=_cmd_status)

    # version
    ver_p = sub.add_parser("version", help="Print version")
    ver_p.set_defaults(func=_cmd_version)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
