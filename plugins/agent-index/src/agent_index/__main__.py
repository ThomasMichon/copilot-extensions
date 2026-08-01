"""CLI entry point for the agent-index service and query surface."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any

import httpx

from . import __version__
from .client import AgentIndexClient
from .config import Config, client_url, discovered_endpoint, load_config, routing_dir, run_dir
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


def _config_from_args(args: argparse.Namespace) -> Config:
    cfg = load_config()
    host = getattr(args, "host", None) or cfg.host
    port = getattr(args, "port", None)
    return Config(host=host, port=cfg.port if port is None else int(port))


def cmd_start(args: argparse.Namespace) -> int:
    serve(_config_from_args(args), passive=bool(getattr(args, "passive", False)))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    return _emit(_status_payload())


def cmd_version(_args: argparse.Namespace) -> int:
    payload = _status_payload()
    print(payload.get("version") or __version__)
    return 0


def cmd_mcp(_args: argparse.Namespace) -> int:
    from agent_index.mcp_app import serve_stdio

    serve_stdio()
    return 0


def _routing_endpoint():
    try:
        from zdd.routing import read_active_endpoint

        return read_active_endpoint(routing_dir(), verify_listener=False)
    except Exception:
        return None


def cmd_stop(_args: argparse.Namespace) -> int:
    routed = _routing_endpoint()
    url = client_url()
    if url:
        try:
            AgentIndexClient(url, timeout=5.0).shutdown()
            pid = getattr(routed, "pid", None)
            if pid and pid != os.getpid():
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        return _emit({"stopped": True, "pid": pid})
                    time.sleep(0.2)
                return _emit({"stopped": False, "reason": "still-running", "pid": pid})
            return _emit({"stopped": True})
        except Exception:
            url = None

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


def cmd_clusters(args: argparse.Namespace) -> int:
    try:
        from agent_index.index_config import IndexConfig
        from agent_index.store.cluster_store import ClusterStore
        from agent_index.store.clustering import source_bucket

        from .query_surface import stored_cluster_to_dict

        bucket = args.bucket
        if args.source and not bucket:
            bucket = source_bucket(args.source)
        config = IndexConfig()
        store = ClusterStore(config.clusters_db)
        stored = store.list_clusters(
            bucket=bucket,
            model_id=args.model,
            has_exact_dupes=True if args.exact_dupes_only else None,
            limit=args.limit,
            offset=0,
        )
        return _emit([stored_cluster_to_dict(c) for c in stored])
    except Exception as exc:
        return _emit_error(exc)


def cmd_deploy(args: argparse.Namespace) -> int:
    from zdd import breadcrumb
    from zdd.cutover import CutoverOrchestrator

    cfg = load_config()
    wildcard_v4 = ".".join(("0", "0", "0", "0"))
    host = cfg.host if cfg.host not in (wildcard_v4, "", "::") else "127.0.0.1"
    if cfg.host == "::":
        host = "::1"

    def pick_free_port() -> int:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def spawn_passive(port: int):
        cmd = [
            sys.executable,
            "-m",
            "agent_index",
            "start",
            "--host",
            cfg.host,
            "--port",
            str(port),
            "--passive",
        ]
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            )
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(cmd, **kwargs)  # noqa: S603

    def health_check(check_host: str, port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://{check_host}:{port}/health", timeout=2) as resp:
                if resp.status != 200:
                    return False
                payload = json.loads(resp.read().decode("utf-8"))
                return payload.get("status") != "draining"
        except Exception:
            return False

    def make_client(base_url: str) -> AgentIndexClient:
        return AgentIndexClient(base_url, timeout=float(args.drain_timeout) + 60.0)

    recovery = breadcrumb.recover_stale_cutover(
        routing_dir(), make_client, health_check=health_check
    )
    if getattr(args, "recover", False):
        if args.json:
            _emit(recovery)
        elif recovery.get("recovered"):
            print(f"[OK] {recovery.get('reason')}")
        else:
            print(f"[>] {recovery.get('reason')}")
        return 0
    if recovery.get("recovered") and not args.json:
        print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}")

    orch = CutoverOrchestrator(
        routing_dir(),
        bind=cfg.host,
        version=__version__,
        spawn_passive=spawn_passive,
        health_check=health_check,
        make_client=make_client,
        pick_free_port=pick_free_port,
    )
    result = orch.run(
        health_timeout=args.health_timeout,
        drain_timeout=args.drain_timeout,
        force=args.force,
    )

    if args.json:
        _emit(result.to_dict())
    else:
        for step in result.steps:
            print(f"  - {step}")
        if result.ok:
            print(f"Cutover complete: active daemon now on port {result.new_port}.")
        elif result.rolled_back:
            print(f"[WARN] Cutover rolled back: {result.error}", file=sys.stderr)
        else:
            print(f"[FAIL] Cutover failed: {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-index")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    def add_start_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", help="bind host (defaults to AGENT_INDEX_HOST or 127.0.0.1)")
        p.add_argument("--port", type=int, help="bind port (defaults to AGENT_INDEX_PORT or 0)")
        p.add_argument(
            "--passive",
            action="store_true",
            help="start as a passive cutover instance",
        )

    p_start = sub.add_parser("start", help="run the local service shell")
    add_start_args(p_start)
    p_start.set_defaults(func=cmd_start)
    p_serve = sub.add_parser("serve", help="alias for start")
    add_start_args(p_serve)
    p_serve.set_defaults(func=cmd_start)
    p_status = sub.add_parser("status", help="print service status as JSON")
    p_status.set_defaults(func=cmd_status)
    p_version = sub.add_parser("version", help="print the running or local version")
    p_version.set_defaults(func=cmd_version)
    p_mcp = sub.add_parser("mcp", help="run the discoverable MCP toolset over stdio")
    p_mcp.set_defaults(func=cmd_mcp)
    p_stop = sub.add_parser("stop", help="stop the active service process")
    p_stop.set_defaults(func=cmd_stop)

    p_deploy = sub.add_parser("deploy", help="zero-downtime active/passive cutover")
    p_deploy.add_argument("--health-timeout", type=float, default=60.0)
    p_deploy.add_argument("--drain-timeout", type=float, default=300.0)
    p_deploy.add_argument("--force", action="store_true")
    p_deploy.add_argument("--recover", action="store_true")
    p_deploy.add_argument("--json", action="store_true")
    p_deploy.set_defaults(func=cmd_deploy)

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
        "--json",
        action="store_true",
        help="emit JSON even when stdout is a TTY",
    )
    p_search.set_defaults(func=cmd_search)

    p_similar = sub.add_parser("similar", help="find chunks similar to an indexed chunk")
    p_similar.add_argument("chunk_id")
    p_similar.add_argument("--limit", type=int, default=10, help="maximum hits to return")
    p_similar.add_argument("--source", help="filter by source")
    p_similar.set_defaults(func=cmd_similar)

    p_clusters = sub.add_parser(
        "clusters", help="list similarity clusters of near-duplicate items"
    )
    p_clusters.add_argument("--source", help="scope to a source (collapsed to its bucket)")
    p_clusters.add_argument("--bucket", help="explicit bucket (e.g. git, gitea:issues)")
    p_clusters.add_argument("--model", help="embedding space (code or prose)")
    p_clusters.add_argument(
        "--exact-dupes-only",
        dest="exact_dupes_only",
        action="store_true",
        help="only clusters that contain a byte-identical pair",
    )
    p_clusters.add_argument("--limit", type=int, default=50, help="maximum clusters to return")
    p_clusters.set_defaults(func=cmd_clusters)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args = parser.parse_args(["status"])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
