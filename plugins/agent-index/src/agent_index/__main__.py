"""CLI entry point for the agent-index service and query surface."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import sys
import time
from typing import Any

import httpx

from . import __version__
from .config import client_url, discovered_endpoint, run_dir
from .query_surface import format_error, hit_to_dict
from .rendezvous import clear_endpoint
from .server import serve


def _emit(value: Any) -> int:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _emit_error(exc: BaseException) -> int:
    _emit({"error": format_error(exc), "hits": []})
    return 1


def _status_payload() -> dict[str, Any]:
    url = client_url()
    if not url:
        return {
            "running": False,
            "plugin": "agent-index",
            "version": __version__,
            "index": {"chunks": 0},
        }
    try:
        with httpx.Client(timeout=2.0) as client:
            payload = client.get(f"{url}/status").json()
        payload["running"] = True
        payload["endpoint"] = url
        return payload
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "running": False,
            "plugin": "agent-index",
            "version": __version__,
            "error": str(exc),
            "endpoint": url,
            "index": {"chunks": 0},
        }


def cmd_start(_args: argparse.Namespace) -> int:
    serve()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    return _emit(_status_payload())


def cmd_version(_args: argparse.Namespace) -> int:
    payload = _status_payload()
    print(payload.get("version") or __version__)
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    ep = discovered_endpoint()
    if ep is None or not ep.pid:
        return _emit({"stopped": False, "reason": "not-running"})
    if ep.pid == os.getpid():
        return _emit({"stopped": False, "reason": "refusing-to-stop-self"})
    try:
        os.kill(ep.pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_endpoint(run_dir())
        return _emit({"stopped": False, "reason": "not-running", "pid": ep.pid})
    except PermissionError as exc:
        return _emit(
            {"stopped": False, "reason": "permission-denied", "pid": ep.pid, "error": str(exc)}
        )

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(ep.pid, 0)
        except OSError:
            clear_endpoint(run_dir())
            return _emit({"stopped": True, "pid": ep.pid})
        time.sleep(0.2)
    return _emit({"stopped": False, "reason": "still-running", "pid": ep.pid})


def cmd_index(args: argparse.Namespace) -> int:
    try:
        from agent_index.indexing import engine as indexing_engine

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = indexing_engine.run_reindex(full=args.full, source=args.source)
        return _emit(result)
    except Exception as exc:
        return _emit_error(exc)


def cmd_search(args: argparse.Namespace) -> int:
    try:
        from agent_index.search import engine as search_engine

        if not args.json and sys.stdout.isatty():
            search_engine.run_search(
                query=args.query,
                limit=args.limit,
                source=args.source,
                language=args.language,
                repo=args.repo,
            )
            return 0

        engine = search_engine.create_search_engine()
        hits = engine.search(
            args.query,
            limit=args.limit,
            source=args.source,
            language=args.language,
            repo=args.repo,
        )
        return _emit([hit_to_dict(hit) for hit in hits])
    except Exception as exc:
        return _emit_error(exc)


def cmd_similar(args: argparse.Namespace) -> int:
    try:
        from agent_index.search import engine as search_engine

        engine = search_engine.create_search_engine()
        hits = engine.find_similar(args.chunk_id, limit=args.limit, source=args.source)
        return _emit([hit_to_dict(hit) for hit in hits])
    except Exception as exc:
        return _emit_error(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-index")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="run the local service shell")
    p_start.set_defaults(func=cmd_start)
    p_serve = sub.add_parser("serve", help="alias for start")
    p_serve.set_defaults(func=cmd_start)
    p_status = sub.add_parser("status", help="print service status as JSON")
    p_status.set_defaults(func=cmd_status)
    p_version = sub.add_parser("version", help="print the running or local version")
    p_version.set_defaults(func=cmd_version)
    p_stop = sub.add_parser("stop", help="stop the process advertised by rendezvous")
    p_stop.set_defaults(func=cmd_stop)

    p_index = sub.add_parser("index", help="populate or refresh the durable index")
    p_index.add_argument("--source", help="source name to index instead of configured defaults")
    p_index.add_argument("--full", action="store_true", help="run a full reindex")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="search the durable index")
    p_search.add_argument("query")
    p_search.add_argument("--source", help="filter by source")
    p_search.add_argument("--language", help="filter by language")
    p_search.add_argument("--repo", help="filter by repository metadata")
    p_search.add_argument("--limit", type=int, default=10, help="maximum hits to return")
    p_search.add_argument(
        "--json", action="store_true", help="emit JSON even when stdout is a TTY"
    )
    p_search.set_defaults(func=cmd_search)

    p_similar = sub.add_parser("similar", help="find chunks similar to an indexed chunk")
    p_similar.add_argument("chunk_id")
    p_similar.add_argument("--limit", type=int, default=10, help="maximum hits to return")
    p_similar.add_argument("--source", help="filter by source")
    p_similar.set_defaults(func=cmd_similar)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args = parser.parse_args(["status"])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
