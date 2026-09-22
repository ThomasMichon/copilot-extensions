"""Server/entry commands for ``agent-bridge`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import urllib.error
from typing import Any

import uvicorn

from . import __version__


def _core():
    from . import __main__ as core

    return core


def _cmd_acp_connect(args: argparse.Namespace) -> None:
    from .acp_connect import cmd_acp_connect

    cmd_acp_connect(args)


def _cmd_elevated(args: argparse.Namespace) -> None:
    from . import elevated

    action = getattr(args, "elevated_action", None) or "status"
    if action == "start":
        try:
            tok = elevated.ensure_running()
        except Exception as exc:
            print(f"Failed to start elevated sub-daemon: {exc}")
            sys.exit(1)
        port = elevated.discovered_port()
        print(f"Elevated sub-daemon up on 127.0.0.1:{port}")
        print(f"Token:  {tok[:8]}...")
        print(f"ACP WS: ws://127.0.0.1:{port}/acp/<agent>")
    elif action == "stop":
        elevated.stop(deregister=bool(getattr(args, "deregister", False)))
        if getattr(args, "deregister", False):
            print("Elevated sub-daemon stopped and task deregistered")
        else:
            print("Elevated sub-daemon stopped (task kept for headless restart)")
    else:
        print(json.dumps(elevated.status(), indent=2))


def _cmd_session_host_agent(args: argparse.Namespace) -> None:
    from .session_host.agent_runner import run_agent_session_host

    try:
        asyncio.run(
            run_agent_session_host(
                args.agent,
                port=args.port,
                state_file=args.state_file,
                cwd=args.cwd,
            )
        )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Far-side runner failed for agent '{args.agent}': {exc}")
        sys.exit(1)


def _dynamic_bind_requested(cfg_port: int, explicit_port: bool) -> bool:
    env = os.environ.get("AGENT_BRIDGE_DYNAMIC_PORT", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return not (explicit_port or cfg_port > 0)


def _bind_listen_socket(host: str, port: int) -> socket.socket:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    family, socktype, proto, _canon, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(sockaddr)
        sock.listen(socket.SOMAXCONN)
    except OSError:
        sock.close()
        raise
    return sock


def _cmd_start(args: argparse.Namespace) -> None:
    from .app import create_app
    from .config import (
        config_dir,
        load_config,
        load_or_create_auth_token,
        migrate_config,
        write_default_config,
    )
    from .models import default_port
    from .singleton import AlreadyRunningError, SingleInstance
    from .watchdog import (
        _WINDOWS_INTERVAL,
        _WINDOWS_SERVING_GRACE,
        arm_serving_watchdog,
    )
    from .winjob import setup_kill_on_close_job

    core = _core()
    cfg = load_config()
    cfg = migrate_config(cfg)
    write_default_config(cfg)
    token = load_or_create_auth_token()

    try:
        neutral = config_dir()
        neutral.mkdir(parents=True, exist_ok=True)
        os.chdir(neutral)
    except OSError:
        pass
    logging.getLogger("agent-bridge").info("Daemon working directory: %s", os.getcwd())
    setup_kill_on_close_job()

    explicit_port = bool(args.port)
    if args.port:
        cfg.port = args.port
    if args.bind:
        cfg.bind = args.bind
    idle = getattr(args, "idle_shutdown", None)
    if idle is not None:
        cfg.idle_shutdown_seconds = idle

    passive = bool(getattr(args, "passive", False))
    if passive:
        cfg.enable_credential_relay = False

    singleton = SingleInstance(config_dir(), port=cfg.port)
    try:
        singleton.acquire()
    except AlreadyRunningError as exc:
        holder = f" (pid {exc.holder_pid})" if exc.holder_pid else ""
        print(
            f"[agent-bridge] Another daemon is already running{holder} for "
            f"{config_dir()} port {cfg.port} -- not starting a duplicate.",
            file=sys.stderr,
        )
        logging.getLogger("agent-bridge").info("Singleton guard: %s -- exiting", exc)
        return

    app = create_app(config=cfg, token=token)
    app.state.single_instance = singleton
    app.state.background_readiness = True
    app.state.publish_on_ready = not passive

    dynamic_port = _dynamic_bind_requested(cfg.port, explicit_port)
    requested_port = 0 if dynamic_port else (cfg.port if cfg.port > 0 else default_port())
    try:
        listen_sock = _bind_listen_socket(cfg.bind, requested_port)
    except OSError as exc:
        singleton.release()
        logging.getLogger("agent-bridge").error(
            "failed to reserve serving port %s:%s: %s",
            cfg.bind,
            requested_port or "dynamic",
            exc,
        )
        raise
    bound_port = listen_sock.getsockname()[1]
    app.state.bound_port = bound_port

    print(f"[agent-bridge] Starting on {cfg.bind}:{bound_port}")
    print(f"[agent-bridge] Auth token: {token[:8]}...")
    print(f"[agent-bridge] DB: {cfg.db_path}")
    if cfg.idle_shutdown_seconds and cfg.idle_shutdown_seconds > 0:
        print(f"[agent-bridge] Idle shutdown after {cfg.idle_shutdown_seconds}s")

    config_kwargs: dict[str, Any] = {"log_level": cfg.log_level, "ws": "wsproto"}
    if sys.platform == "win32":
        from .windows_proactor import resilient_loop_factory

        config_kwargs["loop"] = resilient_loop_factory
    config = uvicorn.Config(app, **config_kwargs)
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server

    watchdog_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        watchdog_kwargs = {
            "interval": _WINDOWS_INTERVAL,
            "serving_grace": _WINDOWS_SERVING_GRACE,
            "on_dead": lambda reason: core._watchdog_dead(
                reason,
                active_port=(bound_port if core._active_endpoint_port() == bound_port else None),
            ),
        }
    import threading

    server_stopped = threading.Event()
    arm_serving_watchdog(
        server,
        bind=cfg.bind,
        port=bound_port,
        is_stopped=server_stopped.is_set,
        **watchdog_kwargs,
    )
    try:
        server.run(sockets=[listen_sock])
    finally:
        server_stopped.set()
        singleton.release()
        listen_sock.close()


def _cmd_status(args: argparse.Namespace) -> None:
    core = _core()
    if getattr(args, "session_id", None):
        core._cmd_session_status(args)
        return
    client = core._get_client(ensure=False)
    base = getattr(client, "_base", "")
    try:
        info = client.health()
        svc = info.get("service", "agent-bridge")
        if base:
            print(f"[OK] agent-bridge is running -- {svc} ({base})")
        else:
            print(f"[OK] agent-bridge is running -- {svc}")
    except SystemExit:
        raise
    except Exception:
        suffix = f" at {base}" if base else ""
        print(f"[FAIL] agent-bridge is not responding{suffix}")
        sys.exit(1)


def _cmd_installer_readiness(_args: argparse.Namespace | None) -> None:
    from .installer_readiness import emit, evaluate

    core = _core()
    exit_code = emit(evaluate(core._service_is_running()))
    if exit_code:
        sys.exit(exit_code)


def _cmd_session_status(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client(ensure=False)
    caller_id = core._caller_id_for(args)
    sid = args.session_id
    try:
        st = client.get_session_status(sid, caller_id=caller_id)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[FAIL] Session {sid} not found", file=sys.stderr)
        else:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        core._json_out(st)
        return

    print(f"  {sid}  ({st.get('name', '')})  [{st.get('status', '')}]")
    print(f"    Agent:   {st.get('agent_name') or '(none)'}")
    if st.get("usage_model"):
        print(f"    Model:   {st['usage_model']}")
    if st.get("caller_id"):
        print(f"    Caller:  {st['caller_id']}")
    print(f"    Turns:   {st.get('turn_count', 0)}    Updated: {core._short_dt(st.get('updated_at'))}")
    live = core._liveness_line(st)
    if live:
        print(f"    Liveness: {live}")
    pct = st.get("context_pct")
    if pct is not None:
        print(f"    Context: {round(pct)}%")

    head = st.get("head_id", 0)
    acked = st.get("last_acked_id", 0)
    behind = st.get("behind", 0)
    if behind:
        hint = min(behind, 50)
        print(f"    Cursor:  {acked}/{head}  ({behind} new -- `read {sid} --tail {hint}` to view, `read {sid}` to consume)")
    else:
        print(f"    Cursor:  {acked}/{head}  (caught up)")

    active = st.get("active_tool")
    if active:
        elapsed = active.get("elapsed_s")
        el = f" ({round(elapsed)}s)" if elapsed is not None else ""
        print(f"    Running: {active.get('title') or 'tool'}{el}")
        if active.get("command"):
            print(f"             {active['command']}")
    else:
        print("    Running: (idle -- no tool in flight)")

    progress = st.get("progress") or {}
    if progress:
        markers = "  ".join(f"{k}={v}" for k, v in progress.items())
        print(f"    Progress: {markers}")

    pending = st.get("pending_ask_user") or []
    for q in pending:
        msg = (q.get("message") or "").strip().replace("\n", " ")
        if len(msg) > 200:
            msg = msg[:197] + "..."
        print(f"    ASK:     {msg}")
        fields = _ask_user_fields(q.get("requested_schema"))
        if fields:
            print(f"             fields: {fields}")
        tcid = q.get("tool_call_id") or ""
        print(
            f"             answer: `agent-bridge answer {sid} --field <key>=<value> …`"
            + (f" (--tool-call-id {tcid})" if len(pending) > 1 else "")
        )

    k = getattr(args, "steps", 0) or 0
    if k > 0 and head:
        events = client.read_range(sid, start=max(1, head - k + 1), end=head)
        out = core._make_renderer(args).render_events(events)
        if out and out.strip():
            print("    Recent:")
            for line in out.rstrip().splitlines():
                print(f"      {line}")


def _ask_user_fields(schema: Any) -> str:
    try:
        props = (schema or {}).get("properties") or {}
        required = set((schema or {}).get("required") or [])
    except Exception:
        return ""
    parts: list[str] = []
    for key, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        typ = spec.get("type", "string")
        star = "*" if key in required else ""
        choices = spec.get("enum")
        if not choices and isinstance(spec.get("items"), dict):
            choices = spec["items"].get("enum")
        if choices:
            parts.append(f"{key}{star}={'|'.join(str(c) for c in choices)}")
        else:
            parts.append(f"{key}{star}({typ})")
    return "  ".join(parts)


def _cmd_version(_args: argparse.Namespace) -> None:
    print(f"agent-bridge {__version__}")


def _cmd_carrier(args: argparse.Namespace) -> None:
    if not args.stdio:
        raise SystemExit("carrier currently requires --stdio")
    from .carrier import cmd_carrier_stdio

    try:
        cmd_carrier_stdio()
    except KeyboardInterrupt:
        pass


def _cmd_remote(args: argparse.Namespace) -> None:
    import time

    from .client import BridgeClientError, BridgeConnectionError

    core = _core()
    client = core._get_client(ensure=False)
    try:
        if args.remote_action == "status":
            result = client.get_remote_session_status(
                args.host, args.session_id, caller_id=args.caller_id
            )
            if getattr(args, "json", False):
                core._json_out(result)
            else:
                print(
                    f"{result.get('session_id', args.session_id)} "
                    f"[{result.get('status', 'unknown')}] "
                    f"cursor={result.get('last_acked_id', 0)}/{result.get('head_id', 0)}"
                )
            return
        if args.remote_action == "live-session":
            result = client.resolve_remote_live_session(args.host, args.session_id)
            if getattr(args, "json", False):
                core._json_out(result)
            else:
                print(f"{result.get('session_id', args.session_id)} [{result.get('status', 'unknown')}]")
            return
        if args.remote_action != "events":
            raise SystemExit("remote requires status, live-session, or events")

        after = args.after
        continuity_id = args.continuity_id
        backoff = 0.25
        while True:
            stream = None
            try:
                stream = client.stream_remote_events(
                    args.host,
                    args.session_id,
                    caller_id=args.caller_id,
                    after=after,
                    continuity_id=continuity_id,
                )
                continuity_id = continuity_id or getattr(stream, "headers", {}).get(
                    "X-Agent-Bridge-Continuity"
                )
                for event in stream:
                    event_name = str(event.get("event") or "")
                    if event_name in {"_heartbeat", "tool_progress"}:
                        continue
                    if event_name == "bridge_control":
                        core._json_out({"control": event.get("data") or {}})
                        raise SystemExit(2)
                    event_id = int(event.get("id") or 0)
                    event_continuity = event.get("continuity_id")
                    if isinstance(event_continuity, str):
                        continuity_id = event_continuity
                    if getattr(args, "json", False):
                        core._json_out(event)
                    else:
                        print(
                            f"{event_id} {event_name} "
                            f"{json.dumps(event.get('data') or {}, separators=(',', ':'))}"
                        )
                    sys.stdout.flush()
                    if event_id:
                        if not continuity_id:
                            core._json_out(
                                {
                                    "control": {
                                        "code": "cursor_invalidated",
                                        "message": "event continuity is unavailable",
                                        "action": "full_reconcile",
                                    }
                                }
                            )
                            raise SystemExit(2)
                        try:
                            after = client.ack_remote_cursor(
                                args.host,
                                args.session_id,
                                event_id,
                                caller_id=args.caller_id,
                                continuity_id=continuity_id,
                            )
                        except (BridgeConnectionError, OSError, urllib.error.URLError):
                            after = None
                            raise
                        except BridgeClientError as exc:
                            if exc.status == 409:
                                control = (
                                    exc.detail
                                    if isinstance(exc.detail, dict)
                                    else {
                                        "code": "cursor_invalidated",
                                        "message": str(exc.detail),
                                        "action": "full_reconcile",
                                    }
                                )
                                core._json_out({"control": control})
                                raise SystemExit(2) from exc
                            if exc.status not in {503, 504}:
                                raise
                            after = None
                            raise BridgeConnectionError(
                                "remote acknowledgement outcome is ambiguous"
                            ) from exc
                        backoff = 0.25
            except BridgeClientError as exc:
                if exc.status == 409:
                    control = (
                        exc.detail
                        if isinstance(exc.detail, dict)
                        else {
                            "code": "cursor_invalidated",
                            "message": str(exc.detail),
                            "action": "full_reconcile",
                        }
                    )
                    core._json_out({"control": control})
                    raise SystemExit(2) from exc
                if exc.status not in {503, 504}:
                    raise
            except BrokenPipeError:
                raise
            except (BridgeConnectionError, OSError, urllib.error.URLError):
                pass
            finally:
                if stream is not None:
                    stream.close()
            client.refresh_endpoint()
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
    except BridgeClientError as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            core._json_out({"error": detail})
        else:
            print(f"[FAIL] {detail}", file=sys.stderr)
        raise SystemExit(1) from exc


def _cmd_token(args: argparse.Namespace) -> None:
    from .config import config_dir, load_or_create_auth_token

    core = _core()
    token = load_or_create_auth_token()
    if getattr(args, "verbose", False):
        port = core._service_port()
        print(f"Token:     {token}")
        print(f"Source:    {config_dir() / 'auth.yaml'}")
        print(f"Status UX: http://127.0.0.1:{port}/ui")
        print(f"ACP WS:    ws://127.0.0.1:{port}/acp/<agent>")
        print("Header:    Authorization: ******")
    else:
        print(token)


def register_service_start_commands(sub: argparse._SubParsersAction) -> None:
    core = _core()

    start_p = sub.add_parser("start", help="Start the agent-bridge server")
    start_p.add_argument("--port", type=int, help="Port to listen on")
    start_p.add_argument("--bind", type=str, help="Address to bind to")
    start_p.add_argument(
        "--idle-shutdown",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Exit after this many seconds with no active sessions (0 = never). Used by the elevated sub-daemon.",
    )
    start_p.add_argument(
        "--passive",
        action="store_true",
        help="Start as a passive cutover instance: do NOT self-publish the routing table (the deploy orchestrator flips it after a health check) and do NOT bind the credential relay (the active daemon owns it until cutover completes).",
    )
    start_p.set_defaults(func=_cmd_start)

    acp_connect_p = sub.add_parser(
        "acp-connect",
        help="Relay stdio <-> a remote bridge's ACP-over-WebSocket endpoint",
    )
    acp_connect_p.add_argument("url", help="ws(s):// URL, e.g. ws://127.0.0.1:9281/acp/<agent>")
    acp_connect_p.add_argument("--token", default=None, help="****** (default: this machine's bridge token)")
    acp_connect_p.add_argument("--no-token", action="store_true", help="Connect without a bearer token")
    acp_connect_p.add_argument(
        "--stdio",
        action="store_true",
        help="Bridge over stdin/stdout (default; accepted for symmetry with 'copilot --acp --stdio')",
    )
    acp_connect_p.set_defaults(func=_cmd_acp_connect)

    sha_p = sub.add_parser(
        "session-host-agent",
        help="Resolve an agent locally and run it inside a Session Host (far-side runner for elevation / ssh / CodeSpace)",
    )
    sha_p.add_argument("agent", help="Agent name to resolve and host")
    sha_p.add_argument("--port", type=int, default=0, help="Loopback port to serve on (0 = auto)")
    sha_p.add_argument("--state-file", default=None, help="Path to write the pid/child_pid/port state JSON")
    sha_p.add_argument("--cwd", default=None, help="Override the resolved working directory")
    sha_p.set_defaults(func=_cmd_session_host_agent)

    elev_p = sub.add_parser("elevated", help="Manage the elevated sub-daemon (Windows)")
    elev_sub = elev_p.add_subparsers(dest="elevated_action")
    elev_sub.add_parser("start", help="Start the elevated sub-daemon (one UAC on first use, then headless)")
    elev_stop = elev_sub.add_parser("stop", help="Stop the elevated sub-daemon (headless; keeps the task)")
    elev_stop.add_argument("--deregister", action="store_true", help="Also delete the scheduled task (one UAC) -- full teardown")
    elev_sub.add_parser("status", help="Show elevated sub-daemon status")
    elev_p.set_defaults(func=_cmd_elevated)

    status_p = sub.add_parser(
        "status", help="Check if agent-bridge is running, or show a session's status"
    )
    status_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID -- show that dispatch's compact status (state, in-flight tool + elapsed, cursor lag) instead of service health",
    )
    status_p.add_argument(
        "--steps",
        type=int,
        default=0,
        metavar="K",
        help="Also show the last K collapsed steps (cursor-neutral; default 0)",
    )
    core._add_stream_args(status_p)
    status_p.set_defaults(func=_cmd_status)

    readiness_p = sub.add_parser(
        "installer-readiness",
        help="Emit the plugin-owned installer/readiness contract state as JSON",
    )
    readiness_p.set_defaults(func=_cmd_installer_readiness)

    ver_p = sub.add_parser("version", help="Print version")
    ver_p.set_defaults(func=_cmd_version)

    carrier_p = sub.add_parser("carrier", help="Run the framed remote Agent Bridge carrier endpoint")
    carrier_p.add_argument("--stdio", action="store_true", help="Serve the carrier protocol on stdin/stdout")
    carrier_p.set_defaults(func=_cmd_carrier)

    remote_p = sub.add_parser("remote", help="Proxy exact remote Bridge reads and event subscriptions")
    remote_sub = remote_p.add_subparsers(dest="remote_action")
    remote_status_p = remote_sub.add_parser("status", help="Read one exact remote session status")
    remote_status_p.add_argument("host", help="Topology machine key or SSH alias")
    remote_status_p.add_argument("session_id", help="Exact hosting-Bridge session id")
    remote_status_p.add_argument("--caller-id", required=True, help="Stable identity unique to this event consumer")
    remote_status_p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output in JSON format")
    remote_status_p.set_defaults(func=_cmd_remote)
    remote_live_p = remote_sub.add_parser("live-session", help="Resolve one exact represented session on the hosting Bridge")
    remote_live_p.add_argument("host", help="Topology machine key or SSH alias")
    remote_live_p.add_argument("session_id", help="Exact live session id")
    remote_live_p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output in JSON format")
    remote_live_p.set_defaults(func=_cmd_remote)
    remote_events_p = remote_sub.add_parser("events", help="Subscribe to exact durable remote session events")
    remote_events_p.add_argument("host", help="Topology machine key or SSH alias")
    remote_events_p.add_argument("session_id", help="Exact hosting-Bridge session id")
    remote_events_p.add_argument("--caller-id", required=True, help="Stable identity unique to this event consumer")
    remote_events_p.add_argument("--after", type=int, default=None, help="Expected durable caller cursor (must match the hosting Bridge)")
    remote_events_p.add_argument("--continuity-id", default=None, help="Expected event-log continuity returned by a prior subscription")
    remote_events_p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output in JSON format")
    remote_events_p.set_defaults(func=_cmd_remote)

    token_p = sub.add_parser(
        "token",
        help="Print the bearer token for external ACP clients (acp-ui, /ui)",
    )
    token_p.add_argument("-v", "--verbose", action="store_true", help="Also print the token source path and connect URLs")
    token_p.set_defaults(func=_cmd_token)
