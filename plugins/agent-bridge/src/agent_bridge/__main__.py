"""CLI entry point for agent-bridge.

Server commands:  start, status, version
Client commands:  agents, machines, sessions, session-usage, send, wait, stop, end, resume
Agent mode:       agent (run as ACP agent on stdio)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agent_procutil import detached_kwargs, no_window_kwargs, windowless_python

from . import __version__
from .parity_harness import (
    FAILED_ACP_HANDSHAKE_FAULT,
    CONTAINER_RECREATE_FAULT,
    FRONTEND_RESTART_HOSTINDEX_LOSS,
    RELAY_INTERRUPTION,
)

if TYPE_CHECKING:
    from .client import BridgeClientError


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _json_out(data: Any) -> None:
    """Print JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def _exit_bridge_outage(exc: Exception) -> None:
    """Report a sustained daemon outage without declaring sessions lost."""
    print(
        "[RETRY] agent-bridge is unavailable after the restart grace; it may "
        "still be restarting or updating.\n"
        "        This command did not complete. Hosted sessions are preserved "
        "and resumable; re-run it shortly.\n"
        f"        Detail: {exc}",
        file=sys.stderr,
    )
    sys.exit(1)


def _connection_identity(client, session_id: str) -> dict[str, Any]:
    """The (repo × venue) identity a session connected to, for emit-back.

    On an initial connection the host that dispatched work needs to know
    *exactly* where it landed -- which agent/repo, on which venue (machine /
    codespace / container), plus the remote worktree and session ids. This is
    especially important once a bare venue can default its repo (venue default
    or the sender's repo): the resolved ``agent`` confirms the choice. Returns a
    best-effort dict; ``worktree_id`` may be null until the first turn's remote
    launch has created the worktree.
    """
    ident: dict[str, Any] = {"session_id": session_id}
    try:
        s = client.get_session(session_id)
    except Exception:
        return ident
    venue_type = s.get("target_type") or "local"
    host = s.get("target_host") or ""
    ident["agent"] = s.get("agent_name")
    ident["repo"] = s.get("project")
    ident["venue"] = f"{venue_type}:{host}" if host else venue_type
    ident["worktree_id"] = s.get("worktree_id")
    return ident


def _print_connection_identity(ident: dict[str, Any]) -> None:
    """Print a concise one-line emit-back of :func:`_connection_identity`."""
    parts = [f"session={ident.get('session_id')}"]
    if ident.get("agent"):
        parts.append(f"agent={ident['agent']}")
    if ident.get("repo"):
        parts.append(f"repo={ident['repo']}")
    if ident.get("venue"):
        parts.append(f"venue={ident['venue']}")
    if ident.get("worktree_id"):
        parts.append(f"worktree={ident['worktree_id']}")
    print(f"[i] Connected: {'  '.join(parts)}")


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str, int]]) -> None:
    """Print a simple text table.

    *columns* is a list of (key, header, min_width) tuples.  Column widths
    auto-expand to fit the longest value so nothing is truncated.
    """
    if not rows:
        print("(none)")
        return

    # Compute effective widths: max of min_width, header length, and longest value
    widths = []
    for key, hdr, min_w in columns:
        data_w = max((len(str(row.get(key, ""))) for row in rows), default=0)
        widths.append(max(min_w, len(hdr), data_w))

    header = "  ".join(h.ljust(w) for (_, h, _), w in zip(columns, widths))
    print(header)
    print("-" * len(header))
    for row in rows:
        parts = []
        for (key, _, _), width in zip(columns, widths):
            val = str(row.get(key, ""))
            parts.append(val.ljust(width))
        print("  ".join(parts))


def _short_dt(iso: str | None) -> str:
    """Format an ISO datetime string to a compact local time."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        return str(iso)[:19]


def _age_str(iso: str | None) -> str:
    """Compact age of an ISO timestamp relative to now (e.g. ``12m``, ``45s``)."""
    if not iso:
        return "?"
    try:
        from datetime import timezone as _tz
        dt = datetime.fromisoformat(iso)
        secs = (datetime.now(_tz.utc) - dt.astimezone(_tz.utc)).total_seconds()
    except Exception:
        return "?"
    secs = max(0, int(secs))
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _liveness_line(s: dict) -> str | None:
    """One-line liveness summary for a RUNNING session, or None.

    Surfaces the real event-stream liveness (#145) so a silent mid-turn stall is
    visible -- the turn-boundary ``Updated`` cannot show it (a hard-working long
    turn and a wedge both leave ``Updated`` frozen).
    """
    liveness = s.get("liveness")
    if not liveness:
        return None
    out_age = _age_str(s.get("last_output_at"))
    if liveness == "active":
        return f"active - last output {out_age} ago"
    if liveness == "stalled":
        return f"STALLED - no output for {out_age} (channel alive)"
    if liveness == "disconnected":
        return "DISCONNECTED - transport down"
    return liveness


def _get_client(*, ensure: bool = True):
    """Build a BridgeClient from config. Exits on failure.

    ``ensure`` (default True): when the daemon is down, best-effort **boot it**
    first so a daemon-touching command self-heals across a crash / idle-exit /
    missing restart task (dotfiles#1713 Slice 3) rather than failing. Pure
    reporters that must reflect reality (``status``, session ``status``) pass
    ``ensure=False`` -- they must never boot what they report on. Set
    ``AGENT_BRIDGE_NO_ENSURE=1`` to disable the boot globally.
    """
    from .client import BridgeClient, BridgeClientError, BridgeConnectionError
    if ensure:
        _ensure_daemon()
    client = BridgeClient.from_config()
    # #632: fail fast if THIS client predates the daemon's advertised support
    # floor (min_protocol_version) -- a genuine past-the-support-window
    # incompatibility where the tolerant-reader contract can no longer carry
    # correctness. Degrade-safe: an unreachable/unversioned daemon advertises
    # min == 0, so this is a no-op there; a mid-restart daemon raises
    # BridgeConnectionError which we swallow so the command's own path (e.g. the
    # streaming engine's reconnect) handles it exactly as before. Latent while
    # HTTP_PROTOCOL_MIN_SUPPORTED == 1; activates automatically when it is raised.
    try:
        client.assert_client_supported()
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(3)
    except BridgeConnectionError:
        pass
    return client


def _add_stream_args(p: argparse.ArgumentParser) -> None:
    """Add the streaming/collapse flags shared by send / wait / read."""
    p.add_argument(
        "--caller", metavar="ID",
        help="Caller identity keying the delivery cursor (defaults to the "
             "current worktree via `agent-worktrees get worktree-dir`, else a "
             "shared per-session cursor)",
    )
    p.add_argument(
        "--expand", action="append", choices=["thoughts", "tools", "all"],
        help="Expand collapsed content in the feed (repeatable). By default "
             "chain-of-thought and tool calls collapse to one-line markers.",
    )
    p.add_argument(
        "--no-color", action="store_true",
        help="Disable ANSI color/dim in the rendered feed",
    )


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


def _cmd_acp_connect(args: argparse.Namespace) -> None:
    """Relay stdio <-> a remote bridge's ACP-over-WebSocket endpoint."""
    from .acp_connect import cmd_acp_connect

    cmd_acp_connect(args)


def _cmd_elevated(args: argparse.Namespace) -> None:
    """Manage the elevated sub-daemon (Windows)."""
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
    """Resolve an agent locally and run it inside a Session Host (far-side runner).

    The program every boundary Spawner launches on the far side of its boundary
    (elevated scheduled task / ssh / CodeSpace bootstrap). Resolution runs here
    so elevation/worktree/enlistment context is native. The connect nonce is read
    from ``AGENT_BRIDGE_SESSION_HOST_NONCE`` by ``run_host``.
    """
    import asyncio

    from .session_host.agent_runner import run_agent_session_host

    try:
        asyncio.run(run_agent_session_host(
            args.agent,
            port=args.port,
            state_file=args.state_file,
            cwd=args.cwd,
        ))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Far-side runner failed for agent '{args.agent}': {exc}")
        sys.exit(1)


def _dynamic_bind_requested(cfg_port: int, explicit_port: bool) -> bool:
    """Whether the daemon should bind an OS-assigned ephemeral port.

    Dynamic bind is now the **default** (dotfiles #694): a primary daemon with no
    pinned port binds ephemeral and advertises it via ``active.json``, so nothing
    well-known (9280/9281) is reserved. A port is *pinned* -- and binds fixed --
    when set via ``--port`` (``explicit_port``) or a positive ``port`` in
    ``config.yaml`` (``cfg_port > 0``); an existing deployment that pins 9280
    therefore keeps it until its config drops the port. ``AGENT_BRIDGE_DYNAMIC_PORT``
    forces the decision either way (``0``/``false`` -> legacy fixed
    ``default_port()`` for rollback; ``1``/``true`` -> dynamic even if pinned).
    """
    env = os.environ.get("AGENT_BRIDGE_DYNAMIC_PORT", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return not (explicit_port or cfg_port > 0)


def _bind_listen_socket(host: str, port: int) -> socket.socket:
    """Bind and return a listening TCP socket for ``host:port``.

    With ``port == 0`` the OS assigns an ephemeral port, read back via
    ``getsockname``. Uses ``getaddrinfo`` so an IPv4 or IPv6 bind host both work.
    Mirrors agent-dispatch's proven Stage-C bind helper.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    family, socktype, proto, _canon, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(sockaddr)
        # Begin accepting TCP handshakes immediately. Uvicorn attaches its
        # protocol handler after lifespan startup; until then the kernel backlog
        # keeps early clients connected rather than rejecting them while the
        # daemon finishes topology/relay initialization.
        sock.listen(socket.SOMAXCONN)
    except OSError:
        sock.close()
        raise
    return sock


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the agent-bridge server."""
    import os

    import uvicorn

    from .config import (
        config_dir,
        load_config,
        load_or_create_auth_token,
        migrate_config,
        write_default_config,
    )

    cfg = load_config()
    cfg = migrate_config(cfg)  # one-time: adopt Session-Host-on default (#177)
    write_default_config(cfg)
    token = load_or_create_auth_token()

    # #89: chdir to a neutral dir so spawned children never inherit (and pin)
    # the daemon's launch cwd -- which, when started from a binstub, is the
    # installed-plugins plugin dir and blocked `copilot plugin update` (EBUSY).
    try:
        neutral = config_dir()
        neutral.mkdir(parents=True, exist_ok=True)
        os.chdir(neutral)
    except OSError:
        pass
    logging.getLogger("agent-bridge").info(
        "Daemon working directory: %s", os.getcwd()
    )

    # #90: place the daemon in a kill-on-close Job Object so spawned agent
    # children (e.g. an `agent-codespaces ssh --stdio` tree) die with the daemon
    # even on a crash / hard kill, instead of orphaning for days.
    from .winjob import setup_kill_on_close_job
    setup_kill_on_close_job()

    explicit_port = bool(args.port)
    if args.port:
        cfg.port = args.port
    if args.bind:
        cfg.bind = args.bind
    idle = getattr(args, "idle_shutdown", None)
    if idle is not None:
        cfg.idle_shutdown_seconds = idle

    # A passive cutover instance never binds the shared credential relay (9857)
    # -- the active daemon owns it until the flip completes -- mirroring the
    # elevated sub-daemon's relay-reuse rule.
    passive = bool(getattr(args, "passive", False))
    if passive:
        cfg.enable_credential_relay = False

    # Single-instance guard: refuse to start a duplicate daemon for this config
    # dir + port. Acquired BEFORE binding the port so a racing/duplicate start
    # exits cleanly instead of half-spawning a zombie that re-binds the relay/
    # service port and defeats restarts (#129). Keying on the port (not just the
    # config dir) lets an active and a passive daemon coexist on one config dir
    # during a zero-downtime cutover. The kernel frees this lock automatically
    # if we die, so there is never a stale lock to reclaim. Keep `singleton`
    # referenced for the daemon's whole lifetime (GC would release the lock).
    from .singleton import AlreadyRunningError, SingleInstance

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
        logging.getLogger("agent-bridge").info(
            "Singleton guard: %s -- exiting", exc
        )
        return

    from .app import create_app

    app = create_app(config=cfg, token=token)
    app.state.single_instance = singleton
    app.state.background_readiness = True
    # A normal start self-publishes the routing table once it is listening so
    # CLI clients discover it; a passive instance stays silent until the deploy
    # orchestrator flips the table after a health check.
    app.state.publish_on_ready = not passive

    # Reserve the serving socket before lifespan starts, for both dynamic and
    # pinned ports. Lifespan publishes this already-owned endpoint immediately;
    # delaying a fixed-port bind until after that publication could demote a
    # healthy predecessor and only then discover that another process owns the
    # requested port.
    from .models import default_port

    dynamic_port = _dynamic_bind_requested(cfg.port, explicit_port)
    requested_port = 0 if dynamic_port else (
        cfg.port if cfg.port > 0 else default_port()
    )
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
    # The port every downstream reader (routing-table publish, /status) advertises
    # -- the *actually bound* port (ephemeral or the pinned/fallback fixed port).
    app.state.bound_port = bound_port

    print(f"[agent-bridge] Starting on {cfg.bind}:{bound_port}")
    print(f"[agent-bridge] Auth token: {token[:8]}...")
    print(f"[agent-bridge] DB: {cfg.db_path}")
    if cfg.idle_shutdown_seconds and cfg.idle_shutdown_seconds > 0:
        print(f"[agent-bridge] Idle shutdown after {cfg.idle_shutdown_seconds}s")

    # Use an explicit Server (not uvicorn.run) so the idle-shutdown monitor in
    # the lifespan can request a graceful stop via server.should_exit. Uvicorn
    # always serves the socket reserved above, so endpoint publication cannot
    # race a later bind failure.
    config_kwargs: dict[str, Any] = {
        "log_level": cfg.log_level,
        # Pure-Python WebSocket protocol (wsproto) for the ACP-over-WS
        # transport. Explicit so we never silently fall back to "none" (which
        # would 403 every /acp WebSocket upgrade) on a host without it.
        "ws": "wsproto",
    }
    if sys.platform == "win32":
        from .windows_proactor import resilient_loop_factory

        # Uvicorn 0.52 supplies its own explicit Windows loop factory, bypassing
        # the process event-loop policy. Pass ours directly so transient
        # AcceptEx client resets cannot permanently close the main listener.
        config_kwargs["loop"] = resilient_loop_factory
    config = uvicorn.Config(app, **config_kwargs)
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server

    # Self-watchdog (#166): a plain OS thread that force-exits the process if it
    # wedges "alive but not serving" -- so the kernel frees the singleton lock
    # and the next start succeeds instead of refusing against a zombie. Armed
    # before serving so it also catches a startup that hangs after the lock is
    # taken. A graceful shutdown (server.should_exit) is never treated as a wedge.
    from .watchdog import (
        _WINDOWS_INTERVAL,
        _WINDOWS_SERVING_GRACE,
        arm_serving_watchdog,
    )

    watchdog_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        watchdog_kwargs = {
            "interval": _WINDOWS_INTERVAL,
            "serving_grace": _WINDOWS_SERVING_GRACE,
            "on_dead": lambda reason: _watchdog_dead(
                reason,
                active_port=(
                    bound_port
                    if _active_endpoint_port() == bound_port
                    else None
                ),
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
    """Check if agent-bridge is running, or show a session's compact status.

    With a ``session_id`` argument, render that dispatch's one-screen status
    (state, in-flight tool + elapsed, cursor lag) instead of the service health
    check (#46.1).
    """
    if getattr(args, "session_id", None):
        _cmd_session_status(args)
        return
    client = _get_client(ensure=False)
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
    """Emit read-only service readiness without starting or restarting it."""
    from .installer_readiness import emit, evaluate

    exit_code = emit(evaluate(_service_is_running()))
    if exit_code:
        sys.exit(exit_code)


def _cmd_session_status(args: argparse.Namespace) -> None:
    """Render a single session's compact, low-context dispatch status."""
    from .client import BridgeClientError

    client = _get_client(ensure=False)
    caller_id = _caller_id_for(args)
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
        _json_out(st)
        return

    print(f"  {sid}  ({st.get('name', '')})  [{st.get('status', '')}]")
    print(f"    Agent:   {st.get('agent_name') or '(none)'}")
    if st.get("usage_model"):
        # The model the dispatched agent is running (applied via
        # session/set_config_option, surfaced here so it is verifiable at a
        # glance -- a silent downgrade is otherwise invisible; dotfiles#790/#1274).
        print(f"    Model:   {st['usage_model']}")
    if st.get("caller_id"):
        print(f"    Caller:  {st['caller_id']}")
    print(
        f"    Turns:   {st.get('turn_count', 0)}"
        f"    Updated: {_short_dt(st.get('updated_at'))}"
    )
    live = _liveness_line(st)
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
        print(
            f"    Cursor:  {acked}/{head}  ({behind} new -- "
            f"`read {sid} --tail {hint}` to view, `read {sid}` to consume)"
        )
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

    # The dispatched agent is blocked on a question for the host to answer
    # (the elicitation backstop, dotfiles#1275) -- surface it loudly so the turn
    # doesn't sit parked unnoticed, with the exact command to unblock it.
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
            f"             answer: `agent-bridge answer {sid} "
            f"--field <key>=<value> …`"
            + (f" (--tool-call-id {tcid})" if len(pending) > 1 else "")
        )

    # Last K collapsed steps (cursor-neutral tail read; --steps 0 disables).
    k = getattr(args, "steps", 0) or 0
    if k > 0 and head:
        events = client.read_range(sid, start=max(1, head - k + 1), end=head)
        out = _make_renderer(args).render_events(events)
        if out and out.strip():
            print("    Recent:")
            for line in out.rstrip().splitlines():
                print(f"      {line}")


def _ask_user_fields(schema: Any) -> str:
    """Compact one-line summary of an ask_user form schema's fields.

    Renders ``key(type)`` per property, appending ``=a|b`` for enum choices, so
    the host can see what values an ``ask_user`` question expects before
    answering. Best-effort: returns ``""`` for an unreadable schema.
    """
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
    """Run the Agent Bridge-owned remote carrier endpoint."""
    if not args.stdio:
        raise SystemExit("carrier currently requires --stdio")
    from .carrier import cmd_carrier_stdio

    try:
        cmd_carrier_stdio()
    except KeyboardInterrupt:
        pass


def _cmd_remote(args: argparse.Namespace) -> None:
    """Use the authenticated local daemon as the remote Bridge carrier owner."""
    import time

    from .client import BridgeClientError, BridgeConnectionError

    client = _get_client(ensure=False)
    try:
        if args.remote_action == "status":
            result = client.get_remote_session_status(
                args.host,
                args.session_id,
                caller_id=args.caller_id,
            )
            if getattr(args, "json", False):
                _json_out(result)
            else:
                print(
                    f"{result.get('session_id', args.session_id)} "
                    f"[{result.get('status', 'unknown')}] "
                    f"cursor={result.get('last_acked_id', 0)}/"
                    f"{result.get('head_id', 0)}"
                )
            return
        if args.remote_action == "live-session":
            result = client.resolve_remote_live_session(
                args.host, args.session_id
            )
            if getattr(args, "json", False):
                _json_out(result)
            else:
                print(
                    f"{result.get('session_id', args.session_id)} "
                    f"[{result.get('status', 'unknown')}]"
                )
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
                continuity_id = (
                    continuity_id
                    or getattr(stream, "headers", {}).get(
                        "X-Agent-Bridge-Continuity"
                    )
                )
                for event in stream:
                    event_name = str(event.get("event") or "")
                    if event_name in {"_heartbeat", "tool_progress"}:
                        continue
                    if event_name == "bridge_control":
                        _json_out({"control": event.get("data") or {}})
                        raise SystemExit(2)
                    event_id = int(event.get("id") or 0)
                    event_continuity = event.get("continuity_id")
                    if isinstance(event_continuity, str):
                        continuity_id = event_continuity
                    if getattr(args, "json", False):
                        _json_out(event)
                    else:
                        print(
                            f"{event_id} {event_name} "
                            f"{json.dumps(event.get('data') or {}, separators=(',', ':'))}"
                        )
                    sys.stdout.flush()
                    if event_id:
                        if not continuity_id:
                            _json_out(
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
                        except (
                            BridgeConnectionError,
                            OSError,
                            urllib.error.URLError,
                        ):
                            # The hosting Bridge may have committed the ACK
                            # before the local cutover lost its response. Let
                            # its durable cursor choose the resume position.
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
                                _json_out({"control": control})
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
                    _json_out({"control": control})
                    raise SystemExit(2) from exc
                if exc.status not in {503, 504}:
                    raise
            except BrokenPipeError:
                raise
            except (
                BridgeConnectionError,
                OSError,
                urllib.error.URLError,
            ):
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
            _json_out({"error": detail})
        else:
            print(f"[FAIL] {detail}", file=sys.stderr)
        raise SystemExit(1) from exc


def _cmd_token(args: argparse.Namespace) -> None:
    """Print the bearer token external ACP clients (e.g. acp-ui) authenticate with.

    Reads ``~/.agent-bridge/auth.yaml`` (generating one on first run, matching
    the daemon). Plain output is the bare token so it can be piped; ``-v`` adds
    the source path and the status-UX / ACP-WebSocket URLs.
    """
    from .config import config_dir, load_or_create_auth_token

    token = load_or_create_auth_token()
    if getattr(args, "verbose", False):
        port = _service_port()
        print(f"Token:     {token}")
        print(f"Source:    {config_dir() / 'auth.yaml'}")
        print(f"Status UX: http://127.0.0.1:{port}/ui")
        print(f"ACP WS:    ws://127.0.0.1:{port}/acp/<agent>")
        print("Header:    Authorization: Bearer <token>")
    else:
        print(token)


# ---------------------------------------------------------------------------
# Service lifecycle (control the installer-managed daemon)
# ---------------------------------------------------------------------------

_INSTALL_DIR = os.path.expanduser(
    os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
)
_PID_FILE = os.path.join(_INSTALL_DIR, "agent-bridge.pid")
_WIN_TASK_NAME = "Agent Bridge"
_SYSTEMD_UNIT = "agent-bridge.service"
_SERVICE_START_TIMEOUT_S = 120
_SERVICE_LAUNCH_GRACE_S = 15


def _active_endpoint():
    """The daemon endpoint recorded in ``active.json``, or ``None``.

    Post-#694 the daemon binds an OS-assigned **dynamic** port and advertises it
    via ``active.json`` -- the config's ``port`` is 0 (the dynamic sentinel), so
    it cannot tell you where the daemon actually listens. Any liveness/port
    resolver that reads only ``config.yaml`` therefore probes the legacy 9280 and
    falsely reports the daemon down while it is healthy on a dynamic port (the
    #1713 liveness bug: ``service status`` FAILing on 9280, and ``service start``
    warning "health check did not pass" against a daemon that did come up). Read
    the routing table first -- ``verify_listener=False`` so a mid-startup port is
    still reported (the caller does its own health probe).
    """
    try:
        from zdd.routing import read_active_endpoint

        return read_active_endpoint(_INSTALL_DIR, verify_listener=False)
    except Exception:
        return None


def _active_endpoint_port() -> int | None:
    """The routed daemon port, or ``None`` when no route has been published."""
    endpoint = _active_endpoint()
    if endpoint is not None and endpoint.port:
        return int(endpoint.port)
    return None


def _service_port() -> int:
    """Resolved bridge port: live routing table > config > platform default.

    Prefers the live/last port advertised in ``active.json`` so a dynamic-port
    daemon (#694) is probed where it actually listens, not the legacy 9280
    fallback (#1713). Falls back to a config-pinned fixed port, then the default.
    """
    from .models import default_port

    live = _active_endpoint_port()
    if live:
        return live

    cfg_path = os.path.join(_INSTALL_DIR, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            import yaml

            data = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
            # Port 0 (the dynamic sentinel) means "no fixed port" -> use the
            # legacy fallback; the routing table is the real resolver (#694).
            return int(data.get("port") or default_port())
        except Exception:
            pass
    return default_port()


def _service_is_running() -> bool:
    """Quiet health probe -- direct GET, no client error spam."""
    return _service_health_on_port(_service_port()) is not None


def _service_health_on_port(
    port: int, *, timeout: float = 2.0
) -> dict[str, Any] | None:
    """Return one verified agent-bridge health payload for *port*."""
    import http.client
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8"))
    except (
        OSError,
        ValueError,
        UnicodeError,
        http.client.HTTPException,
        urllib.error.URLError,
    ):
        return None
    if (
        not isinstance(body, dict)
        or body.get("status") != "ok"
        or body.get("service") != "agent-bridge"
        or body.get("ready") is not True
    ):
        return None
    return body


def _listening_ports_for_pid(pid: int) -> list[int]:
    """Best-effort listener census for one daemon process."""
    import re
    import subprocess as sp

    if pid <= 0:
        return []
    if sys.platform == "win32":
        command = (
            "@(Get-NetTCPConnection -State Listen -OwningProcess "
            f"{pid} -ErrorAction SilentlyContinue).LocalPort | "
            "Sort-Object -Unique"
        )
        try:
            result = sp.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, sp.TimeoutExpired):
            return []
        return sorted(
            {
                int(line.strip())
                for line in (result.stdout or "").splitlines()
                if line.strip().isdigit()
            }
        )

    try:
        result = sp.run(
            ["ss", "-lptnH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, sp.TimeoutExpired):
        return []
    ports: set[int] = set()
    for line in (result.stdout or "").splitlines():
        if f"pid={pid}," not in line:
            continue
        match = re.search(r":(\d+)\s", line)
        if match:
            ports.add(int(match.group(1)))
    return sorted(ports)


def _undrain_service_at(port: int) -> bool:
    """Release one directly addressed daemon's drain gate."""
    from .client import (
        BridgeClient,
        BridgeClientError,
        BridgeConnectionError,
    )
    from .config import load_or_create_auth_token

    client = BridgeClient(
        f"http://127.0.0.1:{port}",
        load_or_create_auth_token(),
        timeout=10,
    )
    try:
        client.undrain()
    except (
        BridgeClientError,
        BridgeConnectionError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        return False
    return True


def _matching_routed_version(pid: int, port: int) -> str | None:
    """Version from a route entry that names this exact daemon."""
    from zdd.routing import Endpoint, read_table

    table = read_table(_INSTALL_DIR) or {}
    for key in ("active", "previous"):
        raw = table.get(key)
        endpoint = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if (
            endpoint is not None
            and endpoint.pid == pid
            and endpoint.port == port
        ):
            return endpoint.version
    return None


def _same_endpoint(left, right) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.bind,
        left.port,
        left.pid,
        left.generation,
    ) == (
        right.bind,
        right.port,
        right.pid,
        right.generation,
    )


def _reconcile_live_dynamic_daemon() -> bool:
    """Adopt a healthy port-0 singleton stranded behind stale routing.

    A classic update/start can leave ``active.json`` naming a retired dynamic
    endpoint while the real daemon still holds ``agent-bridge.0.lock`` and
    listens elsewhere. Discover that exact singleton holder, verify its health,
    undrain it before publication, then atomically repoint routing and service
    markers. No process is started or killed here.
    """
    stale = _active_endpoint()
    pid = _pid_from_lock(0)
    if not pid:
        return False
    candidate: tuple[int, dict[str, Any]] | None = None
    for port in _listening_ports_for_pid(pid):
        health = _service_health_on_port(port)
        if health is not None:
            candidate = (port, health)
            break
    if candidate is None:
        return False
    port, _health = candidate

    from zdd import routing

    with routing._routing_lock(_INSTALL_DIR):
        table = routing.read_table(_INSTALL_DIR) or {}
        active_raw = table.get("active")
        current = (
            routing.Endpoint.from_dict(active_raw)
            if isinstance(active_raw, dict)
            else None
        )
        if not _same_endpoint(current, stale):
            return False
        if (
            current is not None
            and _service_health_on_port(current.port, timeout=0.25) is not None
        ):
            return False
        health = _service_health_on_port(port)
        if health is None:
            return False
        if health.get("draining") is True:
            if not _undrain_service_at(port):
                return False
            health = _service_health_on_port(port)
            if health is None or health.get("draining") is True:
                return False
        version = health.get("version") or _matching_routed_version(pid, port)
        active, _previous = routing._publish_active_unlocked(
            _INSTALL_DIR,
            bind=stale.bind if stale is not None else "127.0.0.1",
            port=port,
            pid=pid,
            version=version,
        )
    _reconcile_service_marker(pid, active.version)
    return True


def _read_pid_file() -> int | None:
    try:
        with open(_PID_FILE, encoding="utf-8") as fh:
            return int((fh.read() or "").strip())
    except (OSError, ValueError):
        return None


def _service_pid() -> int | None:
    """Resolve the daemon PID from routing before stale service side files."""
    endpoint = _active_endpoint()
    if endpoint is not None and endpoint.pid:
        return int(endpoint.pid)
    port_pid = _pid_on_port(_service_port())
    if port_pid:
        return port_pid
    return _read_pid_file()


def _service_process_is_live(*, probe_timeout: float = 1.0) -> bool:
    """Whether routing or service markers identify a live bridge process."""
    endpoint = _active_endpoint()
    candidates = {
        endpoint.pid if endpoint is not None else None,
        _read_pid_file(),
        _pid_from_lock(0),
    }
    candidates.discard(None)
    return any(
        _pid_is_agent_bridge(int(pid), probe_timeout) for pid in candidates
    )


def _wait_for_service_start(
    *,
    timeout: int = _SERVICE_START_TIMEOUT_S,
    launch_grace: int = _SERVICE_LAUNCH_GRACE_S,
) -> bool:
    """Wait for liveness while a confirmed daemon process is still starting.

    Older builds publish their dynamic route only after slow initialization, so
    a fixed 15-second health wait can warn and launch a duplicate while the
    first daemon is healthy-but-not-ready. Wait up to the startup watchdog
    budget once either the routed PID or launcher pid-file identifies a live
    bridge process; fall back promptly when no process appears within the
    platform-manager launch grace.
    """
    import time

    timeout = max(1, timeout)
    launch_grace = max(1, launch_grace)
    deadline = time.monotonic() + timeout
    no_process_deadline = time.monotonic() + launch_grace
    saw_live_process = False
    while time.monotonic() < deadline:
        if _service_is_running():
            return True
        probe_timeout = max(
            0.1, min(1.0, deadline - time.monotonic())
        )
        live_process = _service_process_is_live(
            probe_timeout=probe_timeout
        )
        if live_process:
            saw_live_process = True
            no_process_deadline = time.monotonic() + launch_grace
        elif saw_live_process:
            if time.monotonic() >= no_process_deadline:
                return False
        elif time.monotonic() >= no_process_deadline:
            return False
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1.0, remaining))
    return _service_is_running()


def _print_reconcile_status() -> None:
    """Surface the last session-start auto-reconcile attempt, if recorded (#167).

    bootstrap-check writes ``reconcile-status.json`` on every reconcile it
    launches and tees the installer output to ``reconcile.log``; showing the last
    attempt here makes a silent/failed background update discoverable from
    ``service status`` instead of only by reading log files.
    """
    import json

    status_path = os.path.join(_INSTALL_DIR, "reconcile-status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return
    at = st.get("at", "?")
    frm = st.get("from", "?")
    to = st.get("to", "?")
    log = st.get("log", os.path.join(_INSTALL_DIR, "reconcile.log"))
    print(f"  Last auto-reconcile: {at}  {frm} -> {to}")
    print(f"    log: {log}")


def _reconcile_service_marker(pid: int, version: str | None) -> None:
    """Point the service-management side files at the post-cutover active daemon.

    ``agent-bridge deploy`` cuts over to a **new** daemon on a fresh (ephemeral)
    port, detached from the scheduled-task launcher that started the retired one.
    Two markers are then left describing the *dead* old daemon:

    * ``agent-bridge.pid`` -- still the retired daemon's pid, so ``service
      stop``/``status`` and the launcher's already-running guard miss the live
      daemon (and the launcher can double-spawn a second daemon on the canonical
      port, orphaning the cut-over one).
    * ``running-version.json`` -- the cut-over daemon is a relay-disabled passive
      that never writes its own marker, so the reconciler loses the running
      version signal (falls back to on-disk) until the next ``service restart``.

    Rewriting both to the new active daemon converges the service surface with
    the live process (dotfiles #533 caveat #1). Best-effort: a failure only
    degrades these signals, never the just-completed cutover.
    """
    from .runtime_version import write_running_version

    try:
        with open(_PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass
    if version:
        write_running_version(pid=pid, version=version)


def _pid_on_port(port: int) -> int | None:
    """Best-effort: find the PID listening on *port* (cross-platform)."""
    import subprocess as sp

    if sys.platform == "win32":
        ps = (
            "(Get-NetTCPConnection -LocalPort {0} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -First 1)"
            ".OwningProcess".format(port)
        )
        try:
            out = sp.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=15,
            )
            val = (out.stdout or "").strip()
            return int(val) if val.isdigit() else None
        except (OSError, sp.TimeoutExpired, ValueError):
            return None
    # POSIX
    for cmd in (["ss", "-lptnH", f"sport = :{port}"], ["lsof", "-ti", f"tcp:{port}"]):
        try:
            out = sp.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, sp.TimeoutExpired):
            continue
        text = out.stdout or ""
        if cmd[0] == "lsof":
            line = text.strip().splitlines()
            if line and line[0].isdigit():
                return int(line[0])
        else:
            import re

            m = re.search(r"pid=(\d+)", text)
            if m:
                return int(m.group(1))
    return None


def _kill_pid(pid: int) -> None:
    import signal as _signal
    import subprocess as sp

    if sys.platform == "win32":
        sp.run(["taskkill", "/PID", str(pid), "/F", "/T"],
               capture_output=True, text=True)
    else:
        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass


def _force_kill_agent_bridge_tree(pid: int) -> None:
    """Force-kill a verified retired daemon and its process group/tree."""
    import signal as _signal

    if sys.platform == "win32":
        _kill_pid(pid)  # taskkill /F /T
        return
    from .procgroup import safe_killpg

    if not safe_killpg(pid, _signal.SIGKILL):
        try:
            os.kill(pid, _signal.SIGKILL)
        except OSError:
            pass


def _ensure_retired_daemon_exited(
    pid: int,
    *,
    graceful_timeout: float = 15.0,
    forced_timeout: float = 10.0,
) -> tuple[bool, bool]:
    """Wait for a retired bridge, then force-reap its verified process tree.

    The cutover's HTTP ``/shutdown`` is the graceful owner. This is the
    post-commit backstop: a predecessor that remains alive can keep serving and
    writing the shared SQLite DB, causing split-brain ``database is locked``
    failures. Returns ``(exited, forced)``.
    """
    import time

    if pid <= 0 or not _pid_is_agent_bridge(pid):
        return True, False
    deadline = time.monotonic() + max(0.0, graceful_timeout)
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if not _pid_is_agent_bridge(pid):
            return True, False

    _force_kill_agent_bridge_tree(pid)
    deadline = time.monotonic() + max(0.0, forced_timeout)
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if not _pid_is_agent_bridge(pid):
            return True, True
    return not _pid_is_agent_bridge(pid), True


def _pid_is_agent_bridge(pid: int, timeout: float = 15.0) -> bool:
    """True if *pid* is a live process running the ``agent_bridge`` module.

    Confirms a lock-recorded pid really is a (possibly wedged) daemon before we
    kill it, so a pid the OS recycled for an unrelated process after the daemon
    died is never mistaken for the daemon and killed.
    """
    if pid <= 0:
        return False
    import subprocess as sp

    try:
        if sys.platform == "win32":
            out = sp.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter "
                 f"'ProcessId={pid}' -ErrorAction SilentlyContinue).CommandLine"],
                capture_output=True, text=True, timeout=timeout,
            )
            return "agent_bridge" in (out.stdout or "")
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return b"agent_bridge" in fh.read()
        except OSError:
            out = sp.run(["ps", "-p", str(pid), "-o", "command="],
                         capture_output=True, text=True, timeout=timeout)
            return "agent_bridge" in (out.stdout or "")
    except (OSError, sp.TimeoutExpired, ValueError):
        return False


def _pid_from_lock(port: int) -> int | None:
    """Holder pid of the singleton lock, if it is a live agent-bridge daemon.

    Catches a *wedged* daemon: one alive and still holding the OS singleton lock
    -- so it blocks a fresh ``service start`` via the duplicate-start guard
    (#129) -- but no longer LISTENing or answering ``/health``. Such a daemon is
    invisible to both :func:`_read_pid_file` (stale/empty pid file) and
    :func:`_pid_on_port` (nothing listening), yet must be killed for a restart to
    succeed. An identity check guards against a recycled/reused pid.
    """
    from pathlib import Path

    from .singleton import _read_holder_pid

    lock_path = Path(_INSTALL_DIR) / f"agent-bridge.{port}.lock"
    pid = _read_holder_pid(lock_path)
    if pid and pid != os.getpid() and _pid_is_agent_bridge(pid):
        return pid
    return None


def _systemd_available() -> bool:
    import shutil

    unit = os.path.expanduser(f"~/.config/systemd/user/{_SYSTEMD_UNIT}")
    return (
        sys.platform != "win32"
        and shutil.which("systemctl") is not None
        and os.path.exists(unit)
    )


def _win_task_exists() -> bool:
    """True when the ``Agent Bridge`` scheduled task is registered.

    ``schtasks /Query`` from a **non-elevated** shell against a task registered
    with elevated/highest privileges (or an S4U boot task) returns a non-zero
    exit and prints ``ERROR: Access is denied.`` -- the task *exists*, it just
    can't be read without elevation. Treating that as "absent" (dotfiles#227 root
    cause #2) made ``_service_start`` skip the ``schtasks /Run`` path and mislead
    the caller. Classify access-denied as **exists**; only a genuine "cannot
    find / does not exist" (or a query error) counts as absent.
    """
    import subprocess as sp

    try:
        out = sp.run(
            ["schtasks", "/Query", "/TN", _WIN_TASK_NAME],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, sp.TimeoutExpired):
        return False
    if out.returncode == 0:
        return True
    blob = f"{out.stdout or ''}\n{out.stderr or ''}".casefold()
    # Elevated/S4U task readable only with elevation -> it exists.
    return "access is denied" in blob


def _daemon_launch_argv() -> list[str]:
    """Argv that starts the foreground daemon (``agent-bridge start``) without
    routing through the Windows ``.cmd`` binstub.

    The installed ``agent-bridge`` binstub on Windows is ``agent-bridge.cmd``. A
    bare ``subprocess.Popen(["agent-bridge", "start"])`` asks ``CreateProcess`` to
    resolve the *extensionless* name ``agent-bridge`` -- but PATHEXT resolution is
    a shell feature ``CreateProcess`` does not apply, so it cannot find the
    ``.cmd`` and fails with ``FileNotFoundError`` (WinError 2). Because
    ``service restart`` stops first, that failure leaves the bridge down. This is
    the same bare-name/BatBadBut class ``bridge._agent_bridge_launch_prefix``
    fixes in agent-dispatch.

    Invoking the interpreter directly (``python -m agent_bridge start``) bypasses
    the shim entirely. Prefer the running interpreter (this code already executes
    inside the agent-bridge venv), then the installed venv interpreter, then the
    ``agent-bridge`` binstub on PATH (POSIX shims are plain exec scripts and are
    unaffected). Falls back to the bare name only when nothing else resolves."""
    if sys.executable:
        return [windowless_python(sys.executable), "-m", "agent_bridge", "start"]
    venv = os.path.join(_INSTALL_DIR, "venv")
    py = (
        os.path.join(venv, "Scripts", "python.exe")
        if sys.platform == "win32"
        else os.path.join(venv, "bin", "python")
    )
    if os.path.isfile(py):
        return [windowless_python(py), "-m", "agent_bridge", "start"]
    exe = shutil.which("agent-bridge")  # marketplace-isolation: allow self-bootstrap
    if exe:
        return [exe, "start"]
    return ["agent-bridge", "start"]


def _spawn_via_wmi_broker(argv: list[str]) -> bool:
    """Launch the daemon through WMI ``Win32_Process.Create`` so it is owned by
    the WMI provider host (``WmiPrvSE``) -- fully OUTSIDE the caller's Job object
    and login/SSH session, hence never reaped when the CLI call or host SSH
    session exits.

    This is the guaranteed-escape path used ONLY when
    ``CREATE_BREAKAWAY_FROM_JOB`` is refused (a kill-on-close job without
    ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` -- e.g. some SSH-session jobs): a plain
    detached child there stays in the job and dies with the caller. WMI Create
    runs as the **current user with no elevation** (verified: it does not prompt
    UAC), and re-parents the new process to ``WmiPrvSE``.

    WMI Create cannot inherit/redirect stdio handles, and the daemon relies on
    its launcher to send stdout/stderr to the log files, so the process is
    wrapped in ``cmd /c "<py> ... >> log 2>> err"``. The command is handed to
    PowerShell via ``-EncodedCommand`` (base64 UTF-16LE) to sidestep multi-layer
    quoting. Returns True when Create reports success (ReturnValue 0); the
    caller's own health-wait then confirms the daemon actually came up.
    """
    import base64
    import subprocess as _sp

    log = os.path.join(_INSTALL_DIR, "agent-bridge.log")
    err = os.path.join(_INSTALL_DIR, "agent-bridge-err.log")
    inner = " ".join(f'"{a}"' for a in argv) + f' >> "{log}" 2>> "{err}"'
    cmdline = f'cmd.exe /c "{inner}"'
    # Single-quote for the PowerShell string literal (double any embedded quote).
    ps_cmdline = cmdline.replace("'", "''")
    ps_cwd = _INSTALL_DIR.replace("'", "''")
    ps = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{ CommandLine = '{ps_cmdline}'; "
        f"CurrentDirectory = '{ps_cwd}' }}; exit [int]$r.ReturnValue"
    )
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    try:
        out = _sp.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=30,
            **no_window_kwargs(),
        )
        return out.returncode == 0
    except (OSError, _sp.TimeoutExpired):
        return False


def _spawn_detached_argv(argv: list[str]) -> None:
    """Spawn ``argv`` as a detached, job-surviving process.

    Uses ``detached_kwargs(breakaway=True)`` so the daemon escapes the caller's
    Windows **Job object** and outlives whoever started it -- the persistence
    gotcha behind dotfiles#227/#1713: a daemon spawned as a plain child (or a
    non-breakaway detached child) dies when an SSH/CLI/agent-session caller in a
    kill-on-close job exits, leaving the bridge down with nothing to restart it.

    ``CREATE_BREAKAWAY_FROM_JOB`` fails with ``OSError`` when the caller's job
    lacks ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` (a job that forbids escape). Rather
    than fall back to a plain detached child that STAYS in the job (and is reaped
    when the CLI call / SSH session exits -- the "the CLI accidentally owns it"
    hazard), escalate on Windows to a **WMI broker** launch
    (:func:`_spawn_via_wmi_broker`) that re-parents the daemon to ``WmiPrvSE``,
    fully outside the caller's job/session, with no elevation. Only if that too
    fails do we last-resort to a plain detached spawn.
    """
    import subprocess as _sp

    try:
        logf = open(os.path.join(_INSTALL_DIR, "agent-bridge.log"), "ab")
        errf = open(os.path.join(_INSTALL_DIR, "agent-bridge-err.log"), "ab")
        _sp.Popen(
            argv, stdout=logf, stderr=errf, stdin=_sp.DEVNULL,
            **detached_kwargs(breakaway=True),
        )
        return
    except OSError:
        # Caller's job forbids breakaway. On Windows, escape via the WMI broker
        # so the daemon is not owned by (and reaped with) the caller.
        if sys.platform == "win32" and _spawn_via_wmi_broker(argv):
            return
        # Last resort: plain detached (may not survive a kill-on-close job, but
        # better than not starting at all -- a later CLI call re-boots it).
        logf = open(os.path.join(_INSTALL_DIR, "agent-bridge.log"), "ab")
        errf = open(os.path.join(_INSTALL_DIR, "agent-bridge-err.log"), "ab")
        _sp.Popen(
            argv, stdout=logf, stderr=errf, stdin=_sp.DEVNULL,
            **detached_kwargs(),
        )


def _spawn_detached_daemon() -> None:
    """Spawn ``agent-bridge start`` through the job-surviving launch path."""
    _spawn_detached_argv(_daemon_launch_argv())


def _spawn_watchdog_replacement(
    *,
    delay: float = 1.0,
    start_args: list[str] | None = None,
    active_port: int | None = None,
) -> None:
    """Schedule a fresh daemon after the wedged Windows process releases its lock.

    The helper is detached before the watchdog hard-exits. It waits briefly so
    the kernel can release the singleton, then replaces itself with the same
    ``agent-bridge start`` invocation from this versioned runtime, preserving
    flags such as ``--passive`` and ``--idle-shutdown``.
    """
    code = (
        "import os,sys,time;"
        "time.sleep(float(sys.argv[1]));"
        "os.execv(sys.executable,[sys.executable,'-m','agent_bridge',*sys.argv[2:]])"
    )
    original_args = list(sys.argv[1:] if start_args is None else start_args)
    if "--passive" in original_args and active_port is not None:
        try:
            port_index = original_args.index("--port") + 1
            serving_port = int(original_args[port_index])
        except (ValueError, IndexError):
            serving_port = None
        if serving_port == active_port:
            original_args.remove("--passive")
    argv = [
        windowless_python(sys.executable),
        "-c",
        code,
        str(delay),
        *original_args,
    ]
    _spawn_detached_argv(argv)


def _watchdog_dead(reason: str, *, active_port: int | None = None) -> None:
    """Restart the Windows frontend promptly, then hard-exit the wedged one."""
    from .watchdog import _force_exit

    try:
        _spawn_watchdog_replacement(active_port=active_port)
        logging.getLogger("agent-bridge").error(
            "Self-watchdog: scheduled a detached Windows replacement before exit"
        )
    except Exception as exc:
        logging.getLogger("agent-bridge").error(
            "Self-watchdog: could not schedule Windows replacement: %s", exc
        )
    _force_exit(reason)



# On-demand daemon ensure (dotfiles#1713 Slice 3): a daemon-touching CLI command
# self-heals a down daemon by booting it, so a crash / idle-exit / missing
# restart task no longer surfaces as a hard failure.
_ENSURE_BACKOFF_S = 30.0  # crash-loop guard: don't re-boot within this window
_ENSURE_LOCK = os.path.join(_INSTALL_DIR, ".ensure.lock")
_ENSURE_MARKER = os.path.join(_INSTALL_DIR, ".ensure-attempt")


def _acquire_ensure_lock() -> int | None:
    """Best-effort single-flight lock so concurrent CLI invocations don't each
    boot a daemon. Returns an open fd on success, else None (someone else holds
    a fresh lock). Breaks a lock older than the backoff window (a stale holder).
    """
    import time

    try:
        fd = os.open(_ENSURE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(_ENSURE_LOCK)
        except OSError:
            age = _ENSURE_BACKOFF_S + 1
        if age > _ENSURE_BACKOFF_S:
            try:
                os.unlink(_ENSURE_LOCK)
            except OSError:
                return None
            return _acquire_ensure_lock()
        return None
    except OSError:
        return None


def _release_ensure_lock(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(_ENSURE_LOCK)
    except OSError:
        pass


def _wait_for_ensure_owner() -> bool:
    """Follow a concurrent ensure from process appearance through liveness."""
    import time

    for _ in range(int(_ENSURE_BACKOFF_S)):
        if _service_is_running():
            return True
        if _service_process_is_live():
            return _wait_for_service_start()
        if not os.path.exists(_ENSURE_LOCK):
            return _wait_for_service_start()
        time.sleep(1)
    return _service_is_running()


def _ensure_daemon() -> bool:
    """Boot the daemon if it is down, so a daemon-touching command self-heals.

    Returns True when the daemon is (now) up. Best-effort and non-fatal: if the
    boot doesn't take, the caller proceeds and the client's own connect-grace +
    the top-level ``BridgeConnectionError`` handler frame it as resumable, never
    a death verdict. Guards:

    * **Kill switch** -- ``AGENT_BRIDGE_NO_ENSURE=1`` disables the boot entirely.
    * **Crash-loop backoff** -- if a boot was attempted within
      ``_ENSURE_BACKOFF_S`` and the daemon is still down, don't hammer a daemon
      that keeps dying on start; let the caller surface the resumable framing.
    * **Single-flight** -- an ensure-lock so overlapping invocations boot at most
      one daemon (the daemon's own singleton guard is the backstop); a loser
      waits briefly for the winner's daemon to come up.
    """
    if os.environ.get("AGENT_BRIDGE_NO_ENSURE") == "1":
        return _service_is_running()
    if _service_is_running():
        return True
    if _reconcile_live_dynamic_daemon():
        return True
    if _service_process_is_live() and _wait_for_service_start():
        return True

    import time

    now = time.time()
    try:
        last_attempt = os.path.getmtime(_ENSURE_MARKER)
    except OSError:
        last_attempt = 0.0
    if now - last_attempt < _ENSURE_BACKOFF_S:
        # Recently tried and still down -> a crash loop; don't hammer.
        if _service_process_is_live():
            return _wait_for_service_start()
        if os.path.exists(_ENSURE_LOCK):
            return _wait_for_ensure_owner()
        return _service_is_running()

    fd = _acquire_ensure_lock()
    if fd is None:
        return _wait_for_ensure_owner()
    lock_held = True
    try:
        if _service_is_running():
            return True
        if _service_process_is_live():
            _release_ensure_lock(fd)
            lock_held = False
            return _wait_for_service_start()
        try:
            with open(_ENSURE_MARKER, "w") as fh:
                fh.write(str(now))
        except OSError:
            pass
        _spawn_detached_daemon()
        for _ in range(20):
            time.sleep(1)
            if _service_is_running():
                return True
        return False
    finally:
        if lock_held:
            _release_ensure_lock(fd)


def _service_start() -> None:
    import subprocess as sp
    if _service_is_running():
        print(f"[OK] agent-bridge already running (port {_service_port()})")
        return
    if _reconcile_live_dynamic_daemon():
        print(
            f"[OK] agent-bridge recovered dynamic route "
            f"(port {_service_port()})"
        )
        return

    used_platform_manager = False
    if _systemd_available():
        sp.run(["systemctl", "--user", "start", _SYSTEMD_UNIT])
        used_platform_manager = True
    elif sys.platform == "win32" and _win_task_exists():
        sp.run(["schtasks", "/Run", "/TN", _WIN_TASK_NAME],
               capture_output=True, text=True)
        used_platform_manager = True
    else:
        # No systemd unit / scheduled task: spawn a detached daemon directly.
        _spawn_detached_daemon()

    if _wait_for_service_start():
        print(f"[OK] agent-bridge started (port {_service_port()})")
        return

    # The platform manager (systemd / scheduled task) issued a start but the
    # daemon never came up -- notably an **S4U / RunLevel-Limited** boot task,
    # which cannot reliably launch the daemon into the user session on an
    # on-demand ``schtasks /Run`` (observed live on cloud1: LastTaskResult
    # SCHED_S_TASK_HAS_NOT_RUN, no daemon). Fall back to a direct detached spawn
    # so a mid-session restart still works regardless of the task's logon type
    # (dotfiles#227). Skip when we already spawned directly above.
    if used_platform_manager:
        _spawn_detached_daemon()
        if _wait_for_service_start():
            print(f"[OK] agent-bridge started (port {_service_port()})")
            return

    print("[WARN] agent-bridge start issued but health check did not pass yet "
          "-- check ~/.agent-bridge/agent-bridge-err.log", file=sys.stderr)


def _service_stop() -> None:
    import subprocess as sp
    import time

    stopped_any = False

    if _systemd_available():
        sp.run(["systemctl", "--user", "stop", _SYSTEMD_UNIT])
        stopped_any = True
    elif sys.platform == "win32" and _win_task_exists():
        sp.run(["schtasks", "/End", "/TN", _WIN_TASK_NAME],
               capture_output=True, text=True)
        stopped_any = True

    # The platform manager may not kill an already-detached worker, so also
    # terminate the process by pid file / port binding / singleton-lock holder.
    # The lock holder is the crucial addition: a *wedged* daemon (alive, still
    # holding the OS singleton lock -- so it blocks the next start via the #129
    # guard -- but no longer LISTENing or answering /health) is invisible to both
    # the pid file (stale/empty) and the port probe. Without it, stop "succeeds"
    # (health already fails) while the wedged process lives on and defeats the
    # following start.
    port = _service_port()
    victims = {
        _read_pid_file(),
        _pid_on_port(port),
        _pid_from_lock(port),
        _pid_from_lock(0),
    }
    victims.discard(None)
    for victim in victims:
        _kill_pid(victim)
        stopped_any = True
    if victims:
        try:
            os.remove(_PID_FILE)
        except OSError:
            pass

    if not stopped_any:
        print("[SKIP] agent-bridge does not appear to be running")
        return

    # Confirm the port is released (TimeWait can linger briefly). "Stopped" means
    # BOTH: the port no longer answers health AND no wedged daemon still holds the
    # singleton lock -- a wedged holder answers neither, yet still blocks the next
    # start, so health alone is not a sufficient success signal.
    for _ in range(10):
        locks = {
            _pid_from_lock(_service_port()),
            _pid_from_lock(0),
        }
        locks.discard(None)
        live_victims = {
            victim
            for victim in victims
            if _pid_is_agent_bridge(victim)
        }
        if not _service_is_running() and not locks and not live_victims:
            print("[OK] agent-bridge stopped")
            return
        time.sleep(1)
    print("[WARN] agent-bridge stop issued but still responding", file=sys.stderr)


def _cmd_service(args: argparse.Namespace) -> None:
    action = getattr(args, "service_action", None)
    if action == "start":
        _service_start()
    elif action == "stop":
        _service_stop()
    elif action == "restart":
        _service_stop()
        # Give the OS a moment to release the port before rebinding.
        import time

        time.sleep(3)
        _service_start()
    elif action == "status":
        _cmd_status(args)
        pid = _service_pid()
        if pid:
            print(f"  PID:  {pid}")
        print(f"  Port: {_service_port()}")
        _print_reconcile_status()
    else:
        print(
            "Usage: agent-bridge service {start|stop|restart|status}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Client commands
# ---------------------------------------------------------------------------


def _cmd_agents(args: argparse.Namespace) -> None:
    """List registered agents."""
    client = _get_client()
    agents, topology_errors = client.list_agents_with_diagnostics()
    project = _listing_project(args)
    total = len(agents)
    if project:
        project_key = project.casefold()
        agents = [
            agent for agent in agents
            if (
                agent.get("project") is None
                or str(agent.get("project")).casefold() == project_key
            )
        ]
    if args.json:
        _json_out(agents)
    elif not agents:
        if project and total:
            print(f"(no agents in project {project!r}; use --all-projects)")
        else:
            print("(no agents registered)")
    else:
        for i, a in enumerate(agents):
            name = a.get("name", "")
            display = a.get("display_name", "")
            target_type = a.get("target_type", "")
            host = a.get("host", "")
            aliases = a.get("aliases") or []
            managed = a.get("managed", False)
            # Use display name as heading when available, otherwise raw name
            heading = display or name
            print(heading)
            # Show raw name when it differs from display (e.g. codespace agents)
            if display and name != display:
                print(f"  Name:     {name}")
            if aliases:
                print(f"  Aliases:  {', '.join(aliases)}")
            if target_type:
                print(f"  Type:     {target_type}")
            if host:
                print(f"  Host:     {host}")
            if managed:
                print(f"  Managed:  {managed}")
            if i < len(agents) - 1:
                print()
    if project and total > len(agents) and not args.json and agents:
        print(
            f"\n({total - len(agents)} other-project agent(s) hidden; "
            "use --all-projects)"
        )
    _report_topology_errors(topology_errors)


def _report_topology_errors(errors: list[str]) -> None:
    """Print invalid-profile diagnostics after any valid partial results."""
    if not errors:
        return
    print(
        f"[FAIL] {len(errors)} topology profile error(s):",
        file=sys.stderr,
    )
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(2)


def _listing_project(args: argparse.Namespace) -> str | None:
    """Resolve list scope and reject contradictory explicit flags."""
    if args.all_projects:
        if _PROJECT_OVERRIDE and not _PROJECT_ROUTED:
            print(
                "[FAIL] --project and --all-projects are mutually exclusive",
                file=sys.stderr,
            )
            sys.exit(2)
        return None
    if args.json and not _PROJECT_OVERRIDE:
        return None
    return _sender_repo()


def _live_session_summary_line(s: dict[str, Any]) -> str:
    """One-line human summary of a registered live interactive CLI session."""
    import time

    driver = s.get("driven_by")
    driven = f" driven-by={driver}" if driver else ""
    status = s.get("status") or "live"
    liveness = s.get("liveness")
    turn = f" turn={liveness}" if liveness else ""
    updated = s.get("updated_at")
    age = ""
    if isinstance(updated, (int, float)):
        secs = max(0, int(time.time() - updated))
        age = f" (heartbeat {secs}s ago)"
    lp = s.get("latest_progress") or {}
    prog = ""
    if isinstance(lp, dict) and lp.get("summary"):
        phase = f"{lp['phase']}: " if lp.get("phase") else ""
        prog = f"\n    progress: {phase}{lp['summary']}"
    return f"{s.get('session_id', '?')} [{status}]{turn}{driven}{age}{prog}"


def _cmd_live_sessions(args: argparse.Namespace) -> None:
    """List or resolve registered live interactive CLI sessions.

    The live-session registry is the source of truth for a CLI-embodied task's
    liveness; ``agent-dispatch`` joins a leased task to its session by the
    worktree in the task owner and resolves it here.
    """
    from .client import BridgeClientError

    client = _get_client()
    action = getattr(args, "live_action", None)
    if action == "resolve":
        try:
            session = client.resolve_live_session(args.handle)
        except BridgeClientError as exc:
            if exc.status == 404:
                session = {}
            else:
                raise
        if args.json:
            _json_out(session or {})
            return
        if not session:
            print(f"(no live session for handle {args.handle!r})")
            return
        print(_live_session_summary_line(session))
        return

    if action == "progress":
        try:
            session = client.record_live_progress(
                args.handle,
                summary=args.summary,
                phase=getattr(args, "phase", "") or "",
                blocker=getattr(args, "blocker", None),
                pr=getattr(args, "pr", None),
            )
        except BridgeClientError as exc:
            if exc.status == 404:
                print(f"(no live session for handle {args.handle!r})", file=sys.stderr)
                return
            raise
        if args.json:
            _json_out(session or {})
            return
        lp = (session or {}).get("latest_progress") or {}
        print(f"progress recorded: {lp.get('phase', '')} {lp.get('summary', '')}".strip())
        return

    # default: list
    try:
        sessions = client.list_live_sessions(
            worktree_id=getattr(args, "worktree_id", None),
            include_dead=getattr(args, "include_dead", False),
        )
    except BridgeClientError as exc:
        if exc.status == 404:
            print("[>] Live-sessions endpoint not available (service may need restart)")
            return
        raise
    if args.json:
        _json_out(sessions)
        return
    if not sessions:
        print("(no live interactive sessions registered)")
        return
    for s in sessions:
        print(_live_session_summary_line(s))


def _cmd_machines(args: argparse.Namespace) -> None:
    """List topology machines."""
    from .client import BridgeClientError

    client = _get_client()
    try:
        machines, topology_errors = client.list_machines_with_diagnostics()
    except BridgeClientError as exc:
        if exc.status == 404:
            print("[>] Machines endpoint not available (service may need restart)")
            return
        raise
    project = _listing_project(args)
    total = len(machines)
    if project:
        project_key = project.casefold()
        agents, agent_errors = client.list_agents_with_diagnostics()
        topology_errors = list(dict.fromkeys([*topology_errors, *agent_errors]))
        project_hosts = {
            str(agent.get("machine_key"))
            for agent in agents
            if (
                (
                    agent.get("project") is None
                    or str(agent.get("project")).casefold() == project_key
                )
                and agent.get("machine_key")
            )
        }
        machines = [
            machine for machine in machines
            if machine.get("key") in project_hosts
        ]
    if args.json:
        _json_out(machines)
    else:
        _table(machines, [
            ("key", "MACHINE", 20),
            ("display_name", "NAME", 24),
            ("environment", "ENV", 16),
            ("role", "ROLE", 30),
            ("ssh_ready", "SSH", 5),
        ])
        if project and total > len(machines):
            print(
                f"\n({total - len(machines)} other-project machine(s) hidden; "
                "use --all-projects)"
            )
    _report_topology_errors(topology_errors)


def _cmd_drain(args: argparse.Namespace) -> None:
    """Stop accepting new work and wait for in-flight sessions to settle.

    The zero-downtime pre-swap step: refuses new sessions/turns, then blocks
    until no session is streaming a turn or hosting background sub-agents.
    Exit 0 when fully drained, 2 on timeout (unless --force)."""
    from .client import BridgeClientError

    client = _get_client()
    try:
        res = client.drain(
            timeout=args.timeout, poll=args.poll, force=args.force
        )
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _json_out(res)
    else:
        busy = res.get("busy_sessions", [])
        if res.get("clean"):
            print("Drain complete: no busy sessions remain.")
        elif res.get("forced"):
            print(f"[WARN] Drain forced past {len(busy)} busy session(s): "
                  f"{', '.join(busy)}")
        else:
            print(f"[WARN] Drain timed out; {len(busy)} session(s) still busy: "
                  f"{', '.join(busy)}")
    # Non-zero exit on an unclean, non-forced drain so installer/ExecStop logic
    # can branch on it.
    if not res.get("drained"):
        sys.exit(2)


def _cmd_undrain(args: argparse.Namespace) -> None:
    """Release the drain gate -- the daemon resumes accepting new work."""
    from .client import BridgeClientError

    client = _get_client()
    try:
        client.undrain()
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print("Drain gate released; accepting new work.")


def _cmd_parity(args: argparse.Namespace) -> None:
    """Run the redacted remote-venue acceptance harness."""
    from .client import BridgeClientError
    from .parity_harness import ParityFailure, run

    if (args.ado_url or args.azure_scope) and not args.auth:
        message = "--ado-url/--azure-scope require --auth"
        if args.json:
            _json_out({"ok": False, "target": args.target, "error": message})
        else:
            print(f"[FAIL] venue parity: {message}", file=sys.stderr)
        sys.exit(2)
    if args.fault == RELAY_INTERRUPTION and not args.auth:
        message = "--fault relay-interruption requires --auth"
        if args.json:
            _json_out({"ok": False, "target": args.target, "error": message})
        else:
            print(f"[FAIL] venue parity: {message}", file=sys.stderr)
        sys.exit(2)
    if (
        args.fault == CONTAINER_RECREATE_FAULT
        and not args.target.startswith("container:")
    ):
        message = "--fault container-recreate requires a container: target"
        if args.json:
            _json_out({"ok": False, "target": args.target, "error": message})
        else:
            print(f"[FAIL] venue parity: {message}", file=sys.stderr)
        sys.exit(2)
    client = _get_client()
    fault_handler = (
        _fault_frontend_restart_hostindex_loss
        if args.fault == FRONTEND_RESTART_HOSTINDEX_LOSS
        else None
    )
    try:
        evidence = run(
            client,
            args.target,
            expected_workspace=args.expect_workspace,
            expected_capability=args.expect_capability,
            auth=args.auth,
            ado_url=args.ado_url,
            azure_scope=args.azure_scope,
            startup_timeout=args.startup_timeout,
            turn_timeout=args.turn_timeout,
            keep_session=args.keep_session,
            fault=args.fault,
            fault_handler=fault_handler,
        )
    except ParityFailure as exc:
        result = (
            exc.evidence.to_dict()
            if exc.evidence is not None
            else {"ok": False, "target": args.target}
        )
        result["ok"] = False
        result["error"] = str(exc)
        if args.json:
            _json_out(result)
        else:
            print(f"[FAIL] venue parity: {exc}", file=sys.stderr)
        sys.exit(1)
    except BridgeClientError as exc:
        result = {
            "ok": False,
            "target": args.target,
            "error": exc.detail,
            "status": exc.status,
        }
        if args.json:
            _json_out(result)
        else:
            print(
                f"[FAIL] venue parity: HTTP {exc.status}: {exc.detail}",
                file=sys.stderr,
            )
        sys.exit(1)
    result = evidence.to_dict()
    if args.json:
        _json_out(result)
        return
    print(f"[OK] venue parity: {args.target}")
    summary_pid = (
        result["resumed_child_pid"]
        if args.fault == CONTAINER_RECREATE_FAULT
        else result["initial_child_pid"]
    )
    summary_acp = (
        result["resumed_acp_session_id"]
        if args.fault == CONTAINER_RECREATE_FAULT
        else result["initial_acp_session_id"]
    )
    print(
        f"  session={result['session_id']} "
        f"pid={summary_pid} "
        f"acp={summary_acp}"
    )
    for name, ok in result["checks"].items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")


def _fault_frontend_restart_hostindex_loss(
    session_id: str,
    startup_timeout: float,
) -> dict[str, Any]:
    """Restart the frontend after dropping one harness-owned HostIndex row."""
    import contextlib
    import io
    import time

    from .config import config_dir
    from .session_host.host_index import HostIndex

    index_path = config_dir() / "hosts" / "index.json"
    initial_record = HostIndex(index_path).get(session_id)
    if initial_record is None:
        raise RuntimeError("target session has no local HostIndex record")
    if initial_record.boundary == "local":
        raise RuntimeError("fault requires a remote Session Host boundary")
    if not (initial_record.extra or {}).get("remote_authority_v2"):
        raise RuntimeError("target session lacks far-side authority v2")

    frontend_pid_before = _read_pid_file() or _pid_on_port(_service_port())
    if not frontend_pid_before:
        raise RuntimeError("could not identify the running frontend")

    def quiet_service_call(action) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            action()

    removed = False
    try:
        quiet_service_call(_service_stop)
        if _service_is_running():
            raise RuntimeError("frontend did not stop; HostIndex was not modified")
        removed = HostIndex(index_path).remove(session_id)
        if not removed:
            raise RuntimeError("target HostIndex record disappeared before fault injection")
    finally:
        if not _service_is_running():
            quiet_service_call(_service_start)

    if not _service_is_running():
        raise RuntimeError("frontend did not recover after restart")
    frontend_pid_after = _read_pid_file() or _pid_on_port(_service_port())
    if not frontend_pid_after:
        raise RuntimeError("could not identify the restarted frontend")

    deadline = time.monotonic() + startup_timeout
    recovered = None
    while time.monotonic() < deadline:
        recovered = HostIndex(index_path).get(session_id)
        if recovered is not None:
            break
        time.sleep(0.25)
    if recovered is None:
        raise RuntimeError("HostIndex record was not recovered from far-side authority")

    return {
        "frontend_pid_before": frontend_pid_before,
        "frontend_pid_after": frontend_pid_after,
        "host_index_target_removed": removed,
        "initial_host_pid": initial_record.host_pid,
        "recovered_host_pid": recovered.host_pid,
        "initial_child_pid": initial_record.child_pid,
        "recovered_child_pid": recovered.child_pid,
        "recovered_from_remote_authority": bool(
            (recovered.extra or {}).get("recovered_from_remote")
            and (recovered.extra or {}).get("remote_authority_v2")
        ),
    }


def _passive_daemon_creationflags() -> int:
    """Windows process-creation flags for the detached passive daemon.

    ``DETACHED_PROCESS`` **alone** -- the daemon gets NO console, so a DefTerm
    handoff (Windows Terminal as the default terminal app) has nothing to surface
    as a window/tab. Deliberately NOT ``CREATE_NO_WINDOW``: that flag *creates* a
    console (merely hiding its window), which DefTerm then shows anyway -- the
    headed-console bug. With no console the daemon has no inherited stdio, so the
    spawn must redirect stdout/stderr to real handles (see ``spawn_passive``), or
    uvicorn's logging writes to a broken stream and startup fails.
    """
    if sys.platform == "win32":
        # getattr fallback so this stays importable/testable on non-Windows CI,
        # where subprocess lacks DETACHED_PROCESS (Win32 value 0x00000008); the
        # win32 branch is exercised via monkeypatched sys.platform on Linux.
        return getattr(subprocess, "DETACHED_PROCESS", 0x00000008)  # headless-guard: allow: passive daemon is DETACHED-only (no console at all, not merely no-window)
    return 0


def _cmd_deploy(args: argparse.Namespace) -> None:
    """Active/passive zero-downtime cutover.

    Stands a new daemon up beside the running one on a fresh port, waits for it
    to be healthy, flips the routing table so clients follow it, drains the old
    daemon's in-flight work, then retires the old daemon. Rolls back on any
    pre-commit failure. Run *after* the new code is installed in the venv."""
    import socket
    import subprocess
    import urllib.request

    from . import __version__
    from .client import BridgeClient
    from .config import config_dir, load_config, load_or_create_auth_token
    from zdd import breadcrumb
    from zdd import routing
    from zdd.cutover import CutoverOrchestrator

    cfg = load_config()
    token = load_or_create_auth_token()
    host = cfg.bind if cfg.bind not in ("0.0.0.0", "") else "127.0.0.1"

    def pick_free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def spawn_passive(port: int):
        # Launch the *currently installed* code (this interpreter's venv) as a
        # passive instance, detached so it outlives this deploy process. Use
        # DETACHED_PROCESS (no console -> no DefTerm window) and REDIRECT stdio to
        # the daemon's log files: a detached process has no console to provide
        # stdout/stderr, so without valid handles uvicorn's logging fails at
        # startup and the cutover rolls back. Also keeps the daemon from
        # inheriting THIS deploy process's console handle (which would keep it
        # alive / visible).
        cmd = [windowless_python(sys.executable), "-m", "agent_bridge", "start",
               "--port", str(port), "--passive"]
        kwargs: dict = {}
        if sys.platform == "win32":
            log_out = open(config_dir() / "agent-bridge.log", "ab")
            log_err = open(config_dir() / "agent-bridge-err.log", "ab")
            kwargs["stdout"] = log_out
            kwargs["stderr"] = log_err
            kwargs["stdin"] = subprocess.DEVNULL
            kwargs["creationflags"] = _passive_daemon_creationflags()
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(cmd, **kwargs)

    def health_check(h: str, port: int) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{h}:{port}/health", timeout=2
            ) as resp:
                if resp.status != 200:
                    return False
                body = json.loads(resp.read().decode("utf-8"))
                return (
                    body.get("status") == "ok"
                    and body.get("ready", True) is True
                )
        except Exception:
            return False

    def make_client(base_url: str) -> BridgeClient:
        return BridgeClient(base_url, token,
                            timeout=int(args.drain_timeout) + 60)

    # Heal a prior aborted cutover before starting a new one (#1756): if a
    # stale breadcrumb marks a survivor left drained, undrain it so it is not
    # stranded closed to new work. `--recover` runs *only* this heal and exits.
    recovery = breadcrumb.recover_stale_cutover(
        config_dir(), make_client, health_check=health_check,
    )
    if getattr(args, "recover", False):
        if args.json:
            _json_out(recovery)
        elif recovery.get("recovered"):
            print(f"[OK] {recovery.get('reason')}")
        else:
            print(f"[>] {recovery.get('reason')}")
        sys.exit(0)
    if recovery.get("recovered"):
        print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}")

    orch = CutoverOrchestrator(
        config_dir(), bind=cfg.bind, version=__version__,
        spawn_passive=spawn_passive, health_check=health_check,
        make_client=make_client, pick_free_port=pick_free_port,
    )
    res = orch.run(
        health_timeout=args.health_timeout,
        drain_timeout=args.drain_timeout,
        force=args.force,
    )

    # Caveat #1 (dotfiles #533): the cutover retired the scheduled-task-managed
    # daemon and stood the new active up on a fresh, detached port. Converge the
    # service-management side files (pid-file + running-version.json) with the
    # now-active daemon, read straight from the routing table the cutover just
    # published. Gated on res.ok so a rollback (old daemon restored) is untouched.
    if res.ok:
        active = routing.read_active_endpoint(config_dir(), verify_listener=False)
        if active is not None and active.pid:
            _reconcile_service_marker(active.pid, active.version)
            res.steps.append(
                f"service marker reconciled -> pid {active.pid} "
                f"(port {active.port})"
            )
        old = res.old_endpoint
        if (
            old is not None
            and old.pid
            and (active is None or old.pid != active.pid)
        ):
            exited, forced = _ensure_retired_daemon_exited(old.pid)
            if exited:
                res.steps.append(
                    f"old daemon exited pid={old.pid}"
                    + (" (forced tree reap)" if forced else "")
                )
            else:
                res.steps.append(
                    f"FAILED: retired daemon pid={old.pid} is still alive"
                )
                res.ok = False
                res.error = (
                    f"retired daemon pid={old.pid} survived graceful and "
                    "forced process-tree retirement; split-brain is possible"
                )

    if args.json:
        _json_out(res.to_dict())
    else:
        for step in res.steps:
            print(f"  - {step}")
        if res.ok:
            print(f"Cutover complete: active daemon now on port {res.new_port}.")
        elif res.rolled_back:
            print(f"[WARN] Cutover rolled back: {res.error}", file=sys.stderr)
        else:
            print(f"[FAIL] Cutover failed: {res.error}", file=sys.stderr)
    sys.exit(0 if res.ok else 1)


def _cmd_gc(args: argparse.Namespace) -> None:
    """Run a GC sweep: prune aged terminal/disconnected sessions + compact DB."""
    from .client import BridgeClientError

    client = _get_client()
    try:
        res = client.gc()
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _json_out(res)
        return

    if not res.get("enabled", True):
        print("GC is disabled in config (retention.enabled = false).")
        return

    pruned = res.get("pruned_count", 0)
    msg = f"GC complete: pruned {pruned} session(s)"
    if res.get("vacuumed"):
        msg += f", reclaimed {res.get('reclaimed_bytes', 0) / 1e6:.1f} MB (vacuumed)"
    print(msg)


def _cmd_sessions(args: argparse.Namespace) -> None:
    """List sessions."""
    client = _get_client()
    sessions = client.list_sessions(status=args.status)
    if args.json:
        _json_out(sessions)
        return
    if not sessions:
        print("No sessions")
        return

    for i, s in enumerate(sessions):
        if i > 0:
            print()
        sid = s.get("session_id", "")
        name = s.get("name", "")
        status = s.get("status", "")
        agent = s.get("agent_name") or "(none)"
        caller = s.get("caller_id") or ""
        turns = s.get("turn_count", 0)
        updated = _short_dt(s.get("updated_at"))

        # Context usage
        ctx_size = s.get("context_size")
        ctx_used = s.get("context_used")
        if ctx_size and ctx_used is not None:
            pct = round(ctx_used / ctx_size * 100)
            context = f"{ctx_used // 1000}k/{ctx_size // 1000}k ({pct}%)"
        else:
            context = ""

        print(f"  {sid}  ({name})  [{status}]")
        print(f"    Agent:   {agent}")
        if s.get("elevated"):
            mode = "elevated (persisted)" if s.get("read_only") else "elevated"
            print(f"    Mode:    {mode}")
        if caller:
            print(f"    Caller:  {caller}")
        if context:
            print(f"    Context: {context}")
        print(f"    Turns:   {turns}    Updated: {updated}")
        live = _liveness_line(s)
        if live:
            print(f"    Liveness: {live}")


def _peek_iso(ts: object) -> str:
    """Trim an events.jsonl ISO timestamp to ``YYYY-MM-DD HH:MM:SS`` for display."""
    s = str(ts or "")
    return s[:19].replace("T", " ") if s else "-"


def _cmd_peek(args: argparse.Namespace) -> None:
    """Copilot-free peek at a target's CURRENT session transcript.

    Reads the Copilot CLI's own ``events.jsonl`` for the session agent-bridge
    tracks on the target (by ``acp_session_id``) and distills a compact snapshot
    + reuse-worthiness verdict -- WITHOUT launching ``copilot --acp`` (which can
    stall on the ACP resume race, dotfiles#1422). ``target`` is a session id or
    an agent name (e.g. ``codespace:<name>``); the newest session for an agent is
    treated as current.
    """
    from . import peek_snapshot as ps
    from . import target_exec as tx

    client = _get_client()
    target = args.target

    session = None
    try:
        session = client.get_session(target)
    except Exception:
        session = None
    if not session:
        try:
            sessions = client.list_sessions()
        except Exception:
            sessions = []
        cands = [s for s in sessions if s.get("agent_name") == target]
        if not cands and not target.startswith("codespace:"):
            alt = f"codespace:{target}"
            cands = [s for s in sessions if s.get("agent_name") == alt]
        # list_sessions is newest-first, so the first match is the current session.
        session = cands[0] if cands else None
    if not session:
        print(f"[FAIL] no session found for '{target}' "
              f"(pass a session id or agent name)", file=sys.stderr)
        sys.exit(1)

    sid = session.get("session_id") or session.get("id") or ""
    agent = session.get("agent_name") or ""
    acp = session.get("acp_session_id")
    if not acp:
        msg = (f"session {sid} ({agent}) has no acp_session_id yet -- "
               f"copilot has not written a transcript")
        if args.json:
            _json_out({"ok": False, "reason": msg, "session_id": sid, "agent": agent})
        else:
            print(f"[peek] {msg}")
        return

    try:
        if tx.target_kind(session) == "codespace":
            cmd = ps.build_peek_command(
                acp, tail_lines=args.tail, recent_messages=args.recent,
                message_chars=args.message_chars,
            )
            out = tx.exec_bash_on_target(session, cmd, timeout=float(args.timeout))
            snap = ps.parse_peek_result(out)
        else:
            snap = ps.snapshot_local(
                acp, tail_lines=args.tail, recent_messages=args.recent,
                message_chars=args.message_chars,
            )
    except tx.TargetExecError as exc:
        print(f"[FAIL] peek transport error: {exc}", file=sys.stderr)
        sys.exit(1)

    verdict, reason = ps.reuse_verdict(
        snap, stale_after_seconds=float(args.stale_hours) * 3600
    )

    if args.json:
        _json_out({
            "session_id": sid, "agent": agent, "acp_session_id": acp,
            "verdict": verdict, "verdict_reason": reason, "snapshot": snap,
        })
        return

    print(f"  {sid}  ({agent})  [{session.get('status', '')}]")
    print(f"    acp:     {acp}")
    print(f"    reuse:   {verdict.upper()} -- {reason}")
    if not snap.get("ok"):
        print(f"    (no snapshot: {snap.get('reason', '?')})")
        return
    life = snap.get("lifecycle") or {}
    usage = snap.get("usage") or {}
    print(f"    turns:   {snap.get('turns')}    model: {snap.get('model') or '?'}"
          f"    size: {(snap.get('size_bytes') or 0) // 1024}k")
    print(f"    life:    started={_peek_iso(life.get('started_at'))}  "
          f"resumed={_peek_iso(life.get('resumed_at'))}  "
          f"shutdown={(life.get('last_shutdown') or {}).get('type') or 'none'}")
    if usage:
        print(f"    usage:   premium={usage.get('premium_requests')} "
              f"nanoAiu={usage.get('nano_aiu')}")
    recent = snap.get("recent_messages") or []
    if recent:
        print("    recent:")
        for m in recent:
            role = (m.get("role") or "?")[:9].ljust(9)
            text = " ".join((m.get("text") or "").split())
            print(f"      {role} {text[:140]}")
    tools = snap.get("recent_tool_calls") or []
    if tools:
        print("    tools:   " + ", ".join(str(t.get("title", "?")) for t in tools[:6]))


def _read_prompt_from_file(path: str) -> str:
    """Read a prompt from *path*, or from stdin when *path* is ``-``.

    Lets callers pass multi-line prompts without them transiting the shell's
    argv, where embedded quotes/newlines get mangled -- notably PowerShell
    word-splitting a prompt at the first embedded double-quote (see #250).
    """
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print(f"[FAIL] --prompt-file: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _resolve_prompt(args: argparse.Namespace, *, required: bool) -> str | None:
    """Resolve the effective prompt from the positional arg or ``--prompt-file``.

    The positional prompt and ``--prompt-file`` are mutually exclusive. When
    *required* and neither is supplied, exit with guidance.
    """
    positional = getattr(args, "prompt", None)
    prompt_file = getattr(args, "prompt_file", None)
    if positional is not None and prompt_file:
        print(
            "[FAIL] Pass the prompt either as the positional argument or via "
            "--prompt-file, not both.",
            file=sys.stderr,
        )
        sys.exit(2)
    if prompt_file:
        return _read_prompt_from_file(prompt_file)
    if positional is not None:
        return positional
    if required:
        print(
            "[FAIL] No prompt given. Provide it as the positional argument or "
            "via --prompt-file <path> (or --prompt-file - to read stdin).",
            file=sys.stderr,
        )
        sys.exit(2)
    return None


def _cmd_send(args: argparse.Namespace) -> None:
    """Send a prompt to an agent or existing session.

    Streams the remote turn live by default (collapsed feed), resuming from
    and advancing the caller's delivery cursor so the host ingests exactly
    one contiguous, gap-free copy of the conversation.

    ``send`` never starts a *fresh* session over an existing one: when this
    caller already has a session for the target agent it is reused (and
    resumed if stopped). To force a brand-new session, use
    ``agent-bridge create`` instead.
    """
    if getattr(args, "new", False):
        print(
            "[FAIL] `agent-bridge send --new` has been removed. `send` always "
            "reuses (and resumes) this caller's existing session.\n"
            "       For a brand-new session, use:\n"
            f"         agent-bridge create {args.target} \"<prompt>\"",
            file=sys.stderr,
        )
        sys.exit(2)

    client = _get_client()
    target = args.target
    prompt = _resolve_prompt(args, required=True)

    # Interactive-CLI target -> deliver via the live-session message queue
    # (attributed + answerable envelope), not the ACP turn path. This is how a
    # peer/callback message reaches a human-attached session, and how an agent
    # replies to one (`agent-bridge send <reply-to> "..."`). The target may be
    # an exact session id OR a **worktree handle** (D3): the bridge resolves the
    # handle to whichever session is live now, so a reply survives a handoff.
    live = client.resolve_live_session(target)
    if live:
        expected_session_id = getattr(args, "expected_session_id", None)
        if expected_session_id and live["session_id"] != expected_session_id:
            print(
                f"[FAIL] Target {target!r} now resolves to session "
                f"{live['session_id']!r}, not expected session "
                f"{expected_session_id!r}.",
                file=sys.stderr,
            )
            sys.exit(1)
        _deliver_to_live_session(client, args, live["session_id"], prompt)
        return
    if getattr(args, "expected_session_id", None):
        print(
            f"[FAIL] Target {target!r} has no live session matching "
            f"{args.expected_session_id!r}.",
            file=sys.stderr,
        )
        sys.exit(1)

    caller_id = _caller_id_for(args)

    # Resolve: existing session id, else reuse-or-start this caller's session
    # for the named agent (never force-new -- that is `create`'s job).
    session_id = _resolve_target(client, target, force=getattr(args, "force", False))

    # Issue #25-of-bridge: don't dump a CodeSpace agent's entire prior
    # conversation onto a fresh host. If this caller has never consumed from
    # this session (cursor 0) but the session already has history, fast-forward
    # the caller's cursor to the live head and print a marker instead of
    # replaying the backlog. The host can pull history on demand with
    # `read --range`, or pass --full-history to replay it.
    if not getattr(args, "full_history", False):
        _mark_resume_if_behind(client, session_id, caller_id=caller_id)

    _submit_and_stream(client, args, session_id, prompt, caller_id=caller_id)


def _caller_worktree_handle() -> str | None:
    """This caller's **worktree handle** -- the durable, handoff-surviving id.

    An agent is a *series of sessions in one worktree*; the worktree handle
    (``basename`` of ``agent-worktrees get worktree-dir``, matching the
    ``worktree_id`` the extension registers) names the agent across all its
    sessions. Using it as ``reply-to`` means a reply routes to whichever session
    is live *now*, even after the original session was handed off. None when the
    caller is not inside a worktree (e.g. a bridge-owned agent or a one-off
    script).
    """
    import os as _os

    wt_dir = _worktrees_get("worktree-dir")
    if not wt_dir:
        return None
    return _os.path.basename(wt_dir.rstrip("/")) or None


def _live_reply_to(args: argparse.Namespace) -> str | None:
    """The routable address a reply should target -- a **worktree handle** by
    default, so the reply survives a handoff.

    Precedence:
      1. explicit ``--reply-to`` (an operator/caller override wins);
      2. the caller's **worktree handle** (durable across handoffs; the bridge
         resolves it to the currently-live session) -- this is the D3 fix for
         the dev130 bug where a bash-tool subprocess had no ``SESSION_ID`` env,
         so ``reply_to`` came back None and the reply couldn't route;
      3. else the caller's own session id from the environment
         (``AGENT_BRIDGE_SESSION_ID`` for a bridge-owned agent, ``SESSION_ID``
         for an interactive CLI session) -- an ephemeral fallback for callers
         outside any worktree.
    None when none of these resolve; the message is still delivered, just not
    round-trippable.
    """
    import os as _os

    explicit = getattr(args, "reply_to", None)
    if explicit:
        return explicit
    handle = _caller_worktree_handle()
    if handle:
        return handle
    return _os.environ.get("AGENT_BRIDGE_SESSION_ID") or _os.environ.get("SESSION_ID")


def _live_sender_label(args: argparse.Namespace) -> str:
    """A human-readable sender label for attribution (never routing)."""
    import os as _os
    import socket as _socket

    explicit = getattr(args, "sender", None)
    if explicit:
        return explicit
    return _get_caller_id() or _os.environ.get("USER") or _socket.gethostname()


def _live_message_kind(args: argparse.Namespace) -> str:
    """The typed intent of a delivered message (D2).

    ``prompt`` (default) is a work directive; ``notify``/``status-check`` ask the
    receiver only for a terse, out-of-band acknowledgement and must not be
    treated as new work. ``--notify`` / ``--status-check`` are shorthands for
    ``--kind``.
    """
    if getattr(args, "notify", False):
        return "notify"
    if getattr(args, "status_check", False):
        return "status-check"
    return getattr(args, "kind", None) or "prompt"


def _deliver_to_live_session(
    client, args: argparse.Namespace, session_id: str, prompt: str
) -> None:
    """Deliver a prompt into a live interactive session's message queue.

    The target session's extension polls the queue and injects the message as
    an attributed ``<agent-message from reply-to>`` user turn. Attribution
    (``sender``) is legibility; ``reply_to`` is the routable address the
    receiver answers with ``agent-bridge send <reply-to> "..."``.

    By default (D1) this *waits* for the receiver's reply turn and prints its
    assistant output -- the reply is the receiver's ordinary turn, read back off
    its represented stream, so no extra protocol is needed. ``--no-wait`` returns
    as soon as the message is enqueued (fire-and-forget); ``--reply-timeout``
    bounds the wait.
    """
    sender = _live_sender_label(args)
    reply_to = _live_reply_to(args)
    kind = _live_message_kind(args)
    wait = not getattr(args, "no_wait", False)
    wait_timeout = getattr(args, "reply_timeout", 120.0)
    idempotency = getattr(args, "idempotency_key", None)
    expected_session_id = getattr(args, "expected_session_id", None)
    delivery_options = {}
    if idempotency:
        delivery_options["idempotency_key"] = idempotency
    if expected_session_id:
        delivery_options["expected_session_id"] = expected_session_id
    result = client.send_live_message(
        session_id, sender=sender, body=prompt, reply_to=reply_to,
        kind=kind, wait=wait, wait_timeout=wait_timeout,
        **delivery_options,
    )
    if args.json:
        _json_out({"delivered": True, "target": session_id, **result})
        return
    mid = result.get("message_id")
    kind_note = "" if kind == "prompt" else f", kind {kind}"
    print(
        f"[>] Delivered to live session {session_id} "
        f"(message {mid}, from {sender}{kind_note})"
    )
    if reply_to:
        print(f"    reply-to: {reply_to}")
    else:
        print("    (no reply-to: this sender is not a live session; reply won't route)")
    if not wait:
        return
    if result.get("replied"):
        reply = result.get("reply")
        print(f"\n[<] Reply from {session_id}:")
        print(reply if reply else "    (turn completed with no assistant text)")
    else:
        print(
            f"\n[..] No reply within {wait_timeout:g}s "
            "(message is queued and will still be delivered)."
        )


def _submit_and_stream(
    client,
    args: argparse.Namespace,
    session_id: str,
    prompt: str,
    *,
    caller_id: str | None,
) -> None:
    """Submit *prompt* to *session_id* and stream the turn (shared by send/create)."""
    queue = getattr(args, "queue", False)
    result = client.submit_prompt(
        session_id,
        prompt,
        queue=queue,
        caller_id=caller_id,
        request_timeout=_startup_request_timeout(
            resume=True,
            fresh_fallback=True,
        ),
    )

    # Durable send-or-queue: the session was busy, so the prompt was persisted
    # for FIFO delivery on settle. There is no live turn to stream -- report the
    # queue position and return.
    if result.get("queued"):
        ident = _connection_identity(client, session_id)
        if args.json:
            _json_out({"session_id": session_id, "connection": ident, **result})
            return
        pos = result.get("position")
        qid = result.get("queue_id")
        print(
            f"[~] Session {session_id} busy -- prompt queued durably "
            f"(id {qid}, position {pos}). It sends when the current turn settles."
        )
        _print_connection_identity(ident)
        return

    turn_index = result.get("turn_index", 0)

    ident = _connection_identity(client, session_id)

    if args.json:
        _json_out({"session_id": session_id, "connection": ident, **result})
        return

    print(f"[>] Session {session_id} -- turn {turn_index}")
    _print_connection_identity(ident)

    if args.no_wait:
        print("[>] Prompt submitted (--no-wait)")
        return

    timeouts = _phased_timeouts()
    renderer = _make_renderer(args)
    _stream_feed(
        client, session_id,
        caller_id=caller_id,
        renderer=renderer,
        command_timeout=timeouts.command,
    )


class _AgentSessionConflict(Exception):
    """A force-new request hit an agent that already has an active session.

    Raised (rather than reused) when ``refuse_on_conflict`` is set -- i.e.
    from ``agent-bridge create`` -- so the caller can surface a clear
    "end it first" refusal instead of silently latching onto the existing
    session. Carries the agent name and the existing session id.
    """

    def __init__(self, agent_name: str, existing_session_id: str) -> None:
        self.agent_name = agent_name
        self.existing_session_id = existing_session_id
        super().__init__(
            f"Agent '{agent_name}' already has an active session "
            f"{existing_session_id}"
        )


# Exit code when `send` is rejected because the target's session is busy
# running a turn (the bridge cannot deliver a second prompt mid-turn). Distinct
# from generic failures (1) and arg errors (2) so a caller can react.
_SEND_BUSY_EXIT = 75


def _busy_session_message(
    client, session_id: str, agent_name: str, caller_id: str | None
) -> str:
    """An actionable, LLM-judgement-friendly message for a busy target (#21).

    Names the in-flight session (what it appears to be doing, for how long) and
    frames the decision: wait/observe the live turn (it may already be doing the
    work) versus deliberately terminating it to take over.
    """
    st: dict[str, Any] = {}
    try:
        st = client.get_session_status(session_id, caller_id=caller_id)
    except Exception:
        pass
    name = st.get("name", "")
    turns = st.get("turn_count", 0)
    behind = st.get("behind", 0)
    active = st.get("active_tool") or {}
    lines = [
        f"[BUSY] Agent '{agent_name}' session {session_id}"
        f"{f' ({name})' if name else ''} is running a turn -- the bridge cannot "
        "deliver a second prompt mid-turn.",
    ]
    if active:
        el = active.get("elapsed_s")
        elapsed = f" ({round(el)}s)" if el is not None else ""
        lines.append(f"  in flight: {active.get('title') or 'a tool call'}{elapsed}")
        if active.get("command"):
            lines.append(f"             {active['command']}")
    else:
        lines.append("  in flight: (between tool calls)")
    tail = f", {behind} new event(s) for you" if behind else ""
    lines.append(f"  turns so far: {turns}{tail}")
    lines.append("  Decide -- it may already be doing what you need:")
    lines.append(f"    - WAIT / OBSERVE:  agent-bridge wait {session_id}     "
                 "(block until the turn settles, then re-send)")
    lines.append(f"                       agent-bridge read {session_id} --tail 30   "
                 "(peek without consuming)")
    lines.append(f"    - TAKE OVER:       agent-bridge end {session_id}, then re-send "
                 "-- or re-run with --force (discards the in-flight turn's work)")
    return "\n".join(lines)


def _resolve_target(
    client,
    target: str,
    *,
    force_new: bool = False,
    refuse_on_conflict: bool = False,
    force: bool = False,
    model: str | None = None,
    effort: str | None = None,
    target_dir: str | None = None,
    worktree_id: str | None = None,
) -> str:
    """Resolve a target string to a session ID.

    Resolution order:
    1. Existing session ID (exact match)
    2. Registered agent name (exact match, e.g. ``codespace:my-cs``)
    3. Namespace-prefixed fallback -- if *target* has no ``:`` and no
       exact agent match, try ``<prefix>:<target>`` for each registered
       namespace resolver.  This lets users type bare codespace names
       instead of ``codespace:<name>``.

    ``force_new`` (``create``) skips caller-affinity reuse and always asks
    the server for a fresh session; ``refuse_on_conflict`` turns the
    one-session-per-CodeSpace guard into an ``_AgentSessionConflict`` raise
    instead of reusing the existing session.
    """
    from .client import BridgeClientError

    # Check if it's an existing session
    try:
        session = client.get_session(target)
        if session:
            status = session.get("status", "")
            if status == "idle":
                return target
            elif status == "stopped":
                print(f"[>] Resuming stopped session {target}...")
                client.resume_session(
                    target,
                    request_timeout=_startup_request_timeout(resume=True),
                )
                return target
            else:
                # Busy (running/created/starting): the bridge can't accept a
                # second prompt mid-turn. Fail fast with an actionable error
                # rather than the terse "cannot send prompt" -- or, with
                # --force, terminate the in-flight turn and start fresh for the
                # session's agent.
                agent = session.get("agent_name") or ""
                if not force:
                    print(
                        _busy_session_message(
                            client, target, agent or target,
                            session.get("caller_id"),
                        ),
                        file=sys.stderr,
                    )
                    sys.exit(_SEND_BUSY_EXIT)
                print(
                    f"[>] --force: ending busy session {target} to take over...",
                )
                try:
                    client.end_session(target)
                except Exception:
                    pass
                if agent:
                    # Restart for the agent. force=False bounds any re-conflict
                    # to a clean busy message instead of a takeover loop.
                    return _start_agent_session(client, agent, force=False)
                print(
                    f"[FAIL] Session {target} ended; no agent recorded -- "
                    "re-send to the agent name to start a fresh session.",
                    file=sys.stderr,
                )
                sys.exit(1)
    except BridgeClientError as exc:
        if exc.status != 404:
            raise

    # Try as agent name -- match against listed names AND aliases, resolving to
    # the canonical (raw) agent name so the friendly name an effort spec stores
    # (e.g. ``codespace:type-filters-adoption``, or the bare ``type-filters-
    # adoption``) works and still keys the one-session-per-CodeSpace guard by the
    # raw name. A bare name that matches more than one agent balks (#50).
    try:
        agents = client.list_agents()
    except BridgeClientError:
        agents = []

    matches = _match_agents(target, agents)
    if len(matches) > 1:
        print(
            f"[FAIL] Agent name '{target}' is ambiguous -- it matches "
            f"{len(matches)} agents: {', '.join(matches)}.\n"
            "       Qualify it with a namespace (e.g. 'codespace:<name>') or "
            "use the exact name to disambiguate.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(matches) == 1:
        return _start_agent_session(
            client, matches[0],
            force_new=force_new,
            refuse_on_conflict=refuse_on_conflict,
            force=force,
            model=model,
            effort=effort,
            target_dir=target_dir,
            worktree_id=worktree_id,
        )

    # Not in the cached agent list -- hand the target to the server as-is so its
    # resolver can do an on-demand lookup (a brand-new codespace) and apply its
    # own friendly/bare resolution + ambiguity balk.
    try:
        return _start_agent_session(
            client, target,
            force_new=force_new,
            refuse_on_conflict=refuse_on_conflict,
            force=force,
            model=model,
            effort=effort,
            target_dir=target_dir,
            worktree_id=worktree_id,
        )
    except BridgeClientError as exc:
        if exc.status != 404:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            sys.exit(1)

    print(
        f"[FAIL] '{target}' is not a known agent name or session ID",
        file=sys.stderr,
    )
    sys.exit(1)


def _match_agents(target: str, agents: list[dict]) -> list[str]:
    """Return the canonical names of agents a target matches (#50).

    An agent matches if ``target`` equals its name or any alias, or -- when
    ``target`` is bare (no namespace prefix) -- the unprefixed form of its name
    or any alias. Returns the canonical ``name`` of each distinct match so the
    caller can resolve to the raw agent name (conflict-safe) and detect
    collisions across namespaces.
    """
    matches: list[str] = []
    bare = ":" not in target
    target_key = target.casefold()
    for a in agents:
        name = a.get("name", "")
        if not name:
            continue
        forms = {name, *(a.get("aliases") or [])}
        if target_key in {form.casefold() for form in forms}:
            if name not in matches:
                matches.append(name)
            continue
        if bare:
            # Modifier namespaces (e.g. admin:) mirror an existing agent's base
            # name to wrap it; they are opt-in and must not match a bare name,
            # or every local agent collides with its own elevated twin.
            if a.get("bare_addressable", True) is False:
                continue
            bare_forms = {
                f.split(":", 1)[1].casefold() for f in forms if ":" in f
            }
            if target_key in bare_forms and name not in matches:
                matches.append(name)
    return matches


def _cmd_create(args: argparse.Namespace) -> None:
    """Create a brand-new session for an agent (optionally send a first prompt).

    Unlike ``send`` -- which reuses this caller's existing session -- ``create``
    always spawns a fresh session. For agents that allow only one session at a
    time (CodeSpaces share a single checkout), it refuses with guidance to end
    the existing session first rather than silently reusing it.
    """
    client = _get_client()
    target = args.target
    caller_id = _caller_id_for(args)

    # `create` is agent-only: an existing session id is a misuse (use `send`
    # or `resume` to continue it).
    from .client import BridgeClientError

    try:
        existing = client.get_session(target)
    except BridgeClientError as exc:
        if exc.status != 404:
            raise
        existing = None
    if existing:
        print(
            f"[FAIL] '{target}' is an existing session, not an agent. "
            f"`create` starts a fresh session.\n"
            f"       Continue it with:  agent-bridge send {target} \"<prompt>\"",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        session_id = _resolve_target(
            client, target, force_new=True, refuse_on_conflict=True,
            model=getattr(args, "model", None),
            effort=getattr(args, "effort", None),
            target_dir=getattr(args, "target_dir", None),
            worktree_id=getattr(args, "worktree_id", None),
        )
    except _AgentSessionConflict as conflict:
        sid = conflict.existing_session_id
        print(
            f"[FAIL] Agent '{conflict.agent_name}' already has an active "
            f"session {sid}. Only one session per CodeSpace is allowed.\n"
            f"       End it first:   agent-bridge end {sid}\n"
            f"       Then re-create: agent-bridge create {target} ...\n"
            f"       Or continue it: agent-bridge send {sid} \"<prompt>\"",
            file=sys.stderr,
        )
        sys.exit(1)

    session_id_file = getattr(args, "session_id_file", None)
    if session_id_file:
        try:
            _write_session_id_file(session_id_file, session_id)
        except OSError as exc:
            try:
                client.end_session(session_id, force=True)
            except Exception:
                pass
            print(
                f"[FAIL] Could not write --session-id-file "
                f"{session_id_file!r}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    prompt = _resolve_prompt(args, required=False)
    if not prompt:
        ident = _connection_identity(client, session_id)
        if args.json:
            _json_out({"session_id": session_id, "connection": ident})
        else:
            _print_connection_identity(ident)
            print(
                f"[OK] Session {session_id} created -- send work with: "
                f"agent-bridge send {session_id} \"<prompt>\""
            )
        return

    _submit_and_stream(client, args, session_id, prompt, caller_id=caller_id)


def _write_session_id_file(path_value: str, session_id: str) -> None:
    """Atomically publish the exact session created by this CLI process."""
    from pathlib import Path

    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(session_id + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _mark_resume_if_behind(
    client, session_id: str, *, caller_id: str | None
) -> bool:
    """Fast-forward a first-time caller past a session's prior history.

    When a host attaches to a session it has never consumed from (delivery
    cursor at 0) that already carries history (turns > 0 and a non-zero head),
    replaying the whole backlog is jarring -- the host did not expect the
    remote agent to be mid-conversation. Instead, advance the caller's cursor
    to the current head and emit a one-line marker so the host can opt into the
    history (``read --range``) only if it cares.

    A brand-new session the caller just started (``turn_count == 0``) is left
    untouched, so its opening turn streams normally. Returns True if a marker
    was emitted.
    """
    try:
        info = client.get_cursor_info(session_id, caller_id=caller_id)
    except Exception:
        return False
    if info.get("last_acked_id", 0) != 0:
        return False  # caller already mid-stream on this session -- continue

    try:
        session = client.get_session(session_id)
    except Exception:
        return False
    turn_count = session.get("turn_count", 0) or 0
    head = info.get("head_id", 0) or 0
    if turn_count <= 0 or head <= 0:
        return False  # nothing the caller is behind on

    # Fast-forward past the backlog so the upcoming turn streams cleanly.
    try:
        client.ack_cursor(session_id, head, caller_id=caller_id)
    except Exception:
        return False
    print(
        f"[>] Resuming existing session {session_id} "
        f"({turn_count} prior turn(s)) -- earlier conversation hidden. "
        f"Run `agent-bridge read {session_id} --range 1-{head}` to view it, "
        f"or `agent-bridge send --full-history` to replay it. For a clean "
        f"session, end this one and use `agent-bridge create`."
    )
    return True


def _worktrees_get(key: str) -> str | None:
    """Query ``agent-worktrees get <key>`` in the current working directory.

    This is the CWD-derived source of the caller's worktree identity -- it
    replaces the ``WORKTREE_ID`` env var (which agent-worktrees deliberately does
    *not* inject into bridge-dispatched sessions, so it was unreliable). Returns
    the trimmed value, or None if unavailable / empty / not inside a worktree.
    """
    exe = shutil.which("agent-worktrees")  # marketplace-isolation: allow agent-worktrees-management
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "get", key],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    val = (r.stdout or "").strip()
    return val or None


# Explicit target project set by a top-level ``--project``/`-p` (e.g. injected by
# the `<repo> <slug>` router). Overrides the caller-cwd-derived project in
# ``_sender_repo()`` for project-addressed verbs; left None for a bare
# cwd-addressed invocation outside an adopted project.
_PROJECT_OVERRIDE: str | None = None
_PROJECT_ROUTED = False


def _set_project_override(project: str | None) -> None:
    """Record the top-level ``--project`` override (see ``_sender_repo``)."""
    global _PROJECT_OVERRIDE
    _PROJECT_OVERRIDE = project.strip() if project and project.strip() else None


# Verbs that consume the top-level ``--project``. ``send``/``create`` feed it
# into remote worktree resolution; ``agents``/``machines`` use it to scope the
# displayed catalog. Other verbs remain fleet-global.
_PROJECT_CONSUMING_VERBS = frozenset({"agents", "create", "machines", "send"})


def _guard_project_scope(parser: argparse.ArgumentParser,
                         args: argparse.Namespace) -> None:
    """Fail loud on an *explicit* ``--project`` for a verb that won't use it.

    Silently swallowing an explicitly-passed ``--project`` is a foot-gun for
    agentic callers, so verbs that do not consume it bounce (#1080).

    The ``<repo> <slug>`` router marks its injected flag with
    ``AGENT_WORKTREES_PROJECT_ROUTED=1``. The marker is consumed here so it
    never leaks to child processes.
    """
    global _PROJECT_ROUTED
    routed = os.environ.pop("AGENT_WORKTREES_PROJECT_ROUTED", None) == "1"
    _PROJECT_ROUTED = routed
    if getattr(args, "project", None) is None:
        return
    if routed:
        return
    command = getattr(args, "command", None)
    if command in _PROJECT_CONSUMING_VERBS:
        return
    parser.error(
        f"--project {args.project!r} is not meaningful for "
        f"'{command or '(no command)'}': it scopes only the project-addressed "
        f"verbs ({'/'.join(sorted(_PROJECT_CONSUMING_VERBS))}). Remove "
        f"--project, or use one of those verbs."
    )


def _get_caller_id() -> str | None:
    """Read caller identity from the current worktree (CWD).

    Uses ``agent-worktrees get worktree-dir`` so that each worktree gets its own
    session affinity with remote agents -- derived from the CWD rather than the
    ``WORKTREE_ID`` env (which is not injected into bridge-dispatched sessions).
    Falls back to None when not inside a worktree.
    """
    return _worktrees_get("worktree-dir")


def _sender_repo() -> str | None:
    """The repo the caller is dispatching *from* -- an explicit top-level
    ``--project`` when given, else ``agent-worktrees get project`` in the CWD.

    This is the project fed into the remote worktree resolve on a machine venue
    (``ConnectStage.WORKTREE``). The ``<repo> <slug>`` router injects
    ``--project <repo>`` so a cross-repo dispatch (e.g. ``<repo> bridge send
    <machine>``) is pinned to the named repo instead of the caller's cwd project
    -- which otherwise resolves the *wrong* project's worktree on the target
    when the caller is inside an unrelated repo's worktree."""
    if _PROJECT_OVERRIDE:
        return _PROJECT_OVERRIDE
    return _worktrees_get("project")


def _caller_id_for(args: argparse.Namespace) -> str | None:
    """Resolve the caller identity used to key the delivery cursor.

    Precedence: explicit ``--caller`` > the current worktree
    (``agent-worktrees get worktree-dir``) > None. A None caller falls back to
    the session's shared default cursor server-side; ``--caller`` lets a host
    pin a stable cursor key.
    """
    explicit = getattr(args, "caller", None)
    if explicit:
        return explicit
    return _get_caller_id()


def _phased_timeouts():
    """Load phased timeouts from local config (defaults on any failure)."""
    from .models import PhasedTimeouts

    try:
        from .config import load_config

        return load_config().timeouts
    except Exception:
        return PhasedTimeouts()


def _startup_request_timeout(
    *,
    resume: bool = False,
    fresh_fallback: bool = False,
) -> float:
    """HTTP budget covering synchronous create/resume startup phases."""
    from .session_manager import _MAX_RESUME_ROUNDS

    timeouts = _phased_timeouts()
    one_round = (
        max(
            timeouts.codespace_boot,
            timeouts.ssh_connect,
            timeouts.session_host_ready,
        )
        + timeouts.session_start
        + timeouts.session_new
        + 30.0
    )
    rounds = _MAX_RESUME_ROUNDS if resume else 1
    if fresh_fallback:
        rounds += 1
    return one_round * rounds


def _make_renderer(args: argparse.Namespace):
    """Build a StreamRenderer honoring --expand / color settings."""
    from .render import StreamRenderer

    expand = set(getattr(args, "expand", None) or [])
    color = sys.stdout.isatty() and not getattr(args, "no_color", False)
    return StreamRenderer(
        expand_thoughts=("thoughts" in expand or "all" in expand),
        expand_tools=("tools" in expand or "all" in expand),
        color=color,
    )


# Seconds of stream silence before emitting a progress heartbeat line.
_PROGRESS_INTERVAL = 20.0
# Backoff between reconnect attempts (e.g. while the service restarts).
_RECONNECT_BACKOFF = 1.0
# A session-scoped 404 seen *mid-stream* (wait/read) is treated as transient --
# the daemon bounced and has not re-registered the session yet -- and retried for
# up to this long before it is reported. Distinguishes a genuine "unknown
# session" (still 404 after the daemon is back and settled) from the re-register
# race across a restart. The streaming path owns a longer window than one-shot
# requests because it may reconnect throughout a long-running command and does
# not route through _request's shared outage budget. (#891)
_STREAM_404_GRACE_S = 30.0


def _turn_settled(client, session_id: str, cursor: int) -> bool:
    """True when the session is idle/terminal AND no events remain past cursor.

    The drain check prevents declaring completion while backlog events are
    still in flight.
    """
    try:
        session = client.get_session(session_id)
    except Exception:
        return False
    status = session.get("status", "")
    if status not in ("idle", "stopped", "ended", "failed"):
        return False
    try:
        remaining = client.read_range(session_id, start=cursor + 1)
    except Exception:
        remaining = []
    return not remaining


_REUSABLE_SESSION_STATES = ("created", "starting", "running", "idle", "stopped")


def _reuse_existing(client, session: dict, agent_name: str) -> str:
    """Adopt an existing session, resuming it first if it is stopped.

    Returns the session id ready to receive a prompt. A stopped session is
    resumed (its ACP process re-spawns) so the upcoming ``submit_prompt``
    lands on a live agent rather than failing.
    """
    sid = session.get("session_id", "")
    name = session.get("name", "")
    turns = session.get("turn_count", 0)
    status = session.get("status", "")
    if status == "stopped":
        print(f"[>] Resuming stopped session {sid} ({name})...")
        try:
            client.resume_session(
                sid,
                request_timeout=_startup_request_timeout(resume=True),
            )
        except Exception:
            pass  # submit_prompt will surface a hard failure if it persists
    print(
        f"[>] Reusing session {sid} ({name}) for '{agent_name}' "
        f"({turns} prior turn(s))",
    )
    return sid


# A reused session caught mid-startup (a CodeSpace cold-booting, a host resuming)
# is transiently ``created``/``starting``. Submitting a prompt then races the
# bring-up and the bridge rejects it ("... is starting, not idle" -> HTTP 409,
# ce#606). Wait bounded for it to leave the coming-up state so the caller routes
# on its *settled* status (idle -> submit; running -> the busy-guard) instead of
# racing to a 409.
_COMING_UP_STATES = ("created", "starting")
_COMING_UP_SETTLE_TIMEOUT = 180.0  # covers a cold CodeSpace boot (60-120s)
_COMING_UP_POLL_INTERVAL = 2.0


def _await_coming_up_settled(client, session: dict) -> dict:
    """Return *session* refreshed once it has left the transient coming-up state.

    A reused session still ``created``/``starting`` (e.g. a CodeSpace mid
    cold-boot, or a host mid-resume) rejects a prompt with a 409 "starting, not
    idle" (ce#606). Poll (bounded) until it settles to a routable status --
    idle/running/stopped/terminal -- then let the caller route on that. On
    timeout, return the last-seen session so the caller falls through to its
    prior behavior (the pre-fix race) rather than hanging forever.
    """
    import time as _time

    if session.get("status", "") not in _COMING_UP_STATES:
        return session
    sid = session.get("session_id", "")
    name = session.get("name", "")
    print(
        f"[>] Session {sid} ({name}) is coming up; waiting for it to be ready...",
    )
    deadline = _time.monotonic() + _COMING_UP_SETTLE_TIMEOUT
    while True:
        # Refresh first (the session may already have settled between lookup and
        # now), then sleep only between subsequent polls -- so an already-idle
        # session costs no extra latency.
        try:
            refreshed = client.get_session(sid)
        except Exception:
            refreshed = None
        if refreshed:
            session = refreshed
            if session.get("status", "") not in _COMING_UP_STATES:
                return session
        if _time.monotonic() >= deadline:
            return session
        _time.sleep(_COMING_UP_POLL_INTERVAL)


def _find_caller_session(client, agent_name: str, caller_id: str | None) -> dict | None:
    """Return this caller's newest reusable session for *agent_name*, or None.

    Scans all sessions (newest-first) for a match on (agent_name, caller_id)
    in any reusable state -- crucially **including ``stopped``**, so ``send``
    resumes a caller's prior session instead of orphaning it behind a fresh
    spawn. A caller with no matching session yields None (start a new one).
    """
    try:
        sessions = client.list_sessions()
    except Exception:
        return None
    for s in sessions:
        if (
            s.get("agent_name") == agent_name
            and s.get("caller_id") == caller_id
            and s.get("status", "") in _REUSABLE_SESSION_STATES
            and (
                s.get("status", "") != "stopped"
                or bool(s.get("acp_session_id"))
            )
            and not s.get("read_only", False)
        ):
            return s
    return None


def _start_agent_session(
    client,
    agent_name: str,
    *,
    force_new: bool = False,
    refuse_on_conflict: bool = False,
    force: bool = False,
    model: str | None = None,
    effort: str | None = None,
    target_dir: str | None = None,
    worktree_id: str | None = None,
) -> str:
    """Start or reuse a session for a named agent.

    Default (``send``): reuse this caller's existing session for the agent --
    idle, running, *or* stopped (stopped sessions are resumed) -- keyed by
    (agent_name, caller_id) so different worktrees get separate sessions.
    Only when the caller has no such session is a fresh one started.

    ``force_new=True`` (``create``) skips caller reuse and asks the server for
    a brand-new session. For agents that allow only one session at a time
    (CodeSpaces), the server returns a 409 conflict; with
    ``refuse_on_conflict=True`` this raises ``_AgentSessionConflict`` (so
    ``create`` can tell the user to end the existing session first) instead of
    silently reusing it.
    """
    from .client import BridgeClientError

    caller_id = _get_caller_id()

    if not force_new:
        existing = _find_caller_session(client, agent_name, caller_id)
        if existing is not None:
            # A reused session may be transiently coming up (CodeSpace cold-boot
            # / host resume). Wait for it to settle before routing so a prompt
            # doesn't race the bring-up into a 409 "starting, not idle" (ce#606).
            existing = _await_coming_up_settled(client, existing)
            settled = existing.get("status", "")
            if settled not in _REUSABLE_SESSION_STATES:
                # The coming-up session ended in a terminal state (e.g. the boot
                # or spawn failed) -- don't hand a dead session to submit; fall
                # through and start a fresh one below (ce#606).
                pass
            # Concurrent-dispatch guard (#21): never pile a second prompt onto a
            # session that is mid-turn -- the bridge would reject it (or, worse,
            # the caller would block on an idle-wait timeout). Fail fast with an
            # actionable wait-vs-take-over message, or honor --force by ending
            # the in-flight turn and starting fresh.
            elif settled == "running":
                sid = existing.get("session_id", "")
                if not force:
                    print(
                        _busy_session_message(client, sid, agent_name, caller_id),
                        file=sys.stderr,
                    )
                    sys.exit(_SEND_BUSY_EXIT)
                print(f"[>] --force: ending busy session {sid} to take over...")
                try:
                    client.end_session(sid)
                except Exception:
                    pass
                # Fall through to start a fresh session below.
            else:
                return _reuse_existing(client, existing, agent_name)

    print(f"[>] Starting session for agent '{agent_name}'...")
    try:
        resp = client.start_session(
            agent=agent_name,
            target_dir=target_dir,
            caller_id=caller_id,
            sender_repo=_sender_repo(), force_new=force_new,
            caller_owner_ref=_worktrees_get("owner-ref"),
            worktree_id=worktree_id,
            model=model, effort=effort,
            request_timeout=_startup_request_timeout(),
        )
    except BridgeClientError as exc:
        # Session-lifecycle head guard: a create into an existing worktree whose
        # ground-layer head is still active is refused (reuse / handoff / sunset,
        # or reclaim to take over). Render the choices and stop.
        if _render_head_guard_refusal(exc):
            sys.exit(_SEND_BUSY_EXIT)
        # Server-side concurrency guard: this agent (e.g. a CodeSpace) already
        # has an active session under a different caller. CodeSpaces share one
        # checkout, so a second concurrent session is impossible.
        existing_sid = _conflict_session_id(exc)
        if existing_sid:
            if refuse_on_conflict:
                raise _AgentSessionConflict(agent_name, existing_sid) from exc
            # send path: adopt the single existing session (resume if stopped).
            try:
                session = client.get_session(existing_sid)
            except Exception:
                session = {"session_id": existing_sid}
            session.setdefault("session_id", existing_sid)
            # A conflict-adopted session may also be transiently coming up; let
            # it settle before the running-guard so we don't race to a 409
            # "starting, not idle" (ce#606).
            session = _await_coming_up_settled(client, session)
            # If the adopted session came up in a terminal state (failed/ended),
            # don't reuse a dead session -- clear it and start fresh. Ending an
            # already-terminal session is harmless and clears the conflict, so
            # the retry below is bounded (ce#606).
            if session.get("status", "") not in _REUSABLE_SESSION_STATES:
                try:
                    client.end_session(existing_sid)
                except Exception:
                    pass
                return _start_agent_session(
                    client, agent_name, force_new=force_new,
                    refuse_on_conflict=refuse_on_conflict, force=False,
                    model=model, effort=effort,
                )
            # Same #21 guard for a session held by *another* caller: if it is
            # mid-turn, don't silently adopt-and-block -- fail fast (or take
            # over with --force).
            if session.get("status", "") == "running":
                if not force:
                    print(
                        _busy_session_message(
                            client, existing_sid, agent_name, caller_id
                        ),
                        file=sys.stderr,
                    )
                    sys.exit(_SEND_BUSY_EXIT)
                print(
                    f"[>] --force: ending busy session {existing_sid} to take "
                    "over...",
                )
                try:
                    client.end_session(existing_sid)
                except Exception:
                    pass
                # Retry a fresh start now that the conflict is cleared. force=
                # False bounds any re-conflict to a clean busy message.
                return _start_agent_session(
                    client, agent_name, force_new=force_new,
                    refuse_on_conflict=refuse_on_conflict, force=False,
                    model=model, effort=effort,
                )
            return _reuse_existing(client, session, agent_name)
        raise
    sid = resp.get("session_id", "")
    name = resp.get("name", "")
    print(f"[>] Session {sid} ({name}) created")
    # Phased timeout: a codespace may need to cold-boot (much longer than a
    # local agent spawn), so use the boot timeout for codespace targets.
    timeouts = _phased_timeouts()
    if agent_name.startswith("codespace:"):
        start_timeout = timeouts.codespace_boot
    else:
        start_timeout = timeouts.session_start
    _wait_for_idle(client, sid, timeout=start_timeout)
    return sid


def _render_head_guard_refusal(exc: "BridgeClientError") -> bool:
    """Print the session-lifecycle head-guard refusal, if that's what ``exc`` is.

    The server refuses a create into an occupied worktree with a 409 active- or
    pending-head reason, choices, and the ``reclaim`` break-glass. Renders them
    for a human and returns True when handled; False when
    ``exc`` is some other error (caller keeps its normal handling).
    """
    if getattr(exc, "status", None) != 409:
        return False
    detail = getattr(exc, "detail", None)
    if (
        not isinstance(detail, dict)
        or detail.get("reason") not in (
            "worktree_head_active", "worktree_head_pending"
        )
    ):
        return False
    wt = detail.get("worktree_id", "?")
    print(f"[BLOCKED] {detail.get('message', f'Worktree {wt} is occupied.')}",
          file=sys.stderr)
    for choice in detail.get("choices", []):
        tag = " (preferred)" if choice.get("preferred") else ""
        print(f"  - {choice.get('action')}{tag}: {choice.get('description', '')}",
              file=sys.stderr)
    override = detail.get("override", "reclaim=true")
    print(f"  Break-glass: re-issue with {override} to take over the worktree.",
          file=sys.stderr)
    return True


def _conflict_session_id(exc: "BridgeClientError") -> str | None:
    """Extract the existing session id from a 409 session-conflict error.

    The server returns a structured detail dict for session conflicts:
    {"error": "session_conflict", "existing_session_id": "...", ...}.
    Returns None if this is not a session-conflict error.
    """
    if getattr(exc, "status", None) != 409:
        return None
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and detail.get("error") == "session_conflict":
        return detail.get("existing_session_id")
    return None


def _wait_for_idle(client, session_id: str, timeout: float = 30.0) -> None:
    """Poll until session status is 'idle' or error."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = client.get_session(session_id)
        status = session.get("status", "")
        if status == "idle":
            return
        if status in ("failed", "ended", "stopped"):
            detail = _session_failure_detail(client, session_id)
            suffix = f": {detail}" if detail else ""
            print(
                f"[FAIL] Session {session_id} entered {status}{suffix}",
                file=sys.stderr,
            )
            sys.exit(1)
        time.sleep(0.5)
    print(f"[FAIL] Timed out waiting for session {session_id} to become idle", file=sys.stderr)
    sys.exit(1)


def _session_failure_detail(client, session_id: str) -> str | None:
    """Return the latest durable connect/error detail for a failed session."""
    try:
        events = client.read_range(session_id)
    except Exception:
        return None
    for event in reversed(events):
        data = event.get("data") or {}
        if event.get("event") == "connect_failed":
            message = str(data.get("message") or "").strip()
            stage = data.get("stage")
            stage_name = str(data.get("stage_name") or "").strip()
            if message and stage is not None and stage_name:
                return f"[stage {stage}/{stage_name}] {message}"
            if message:
                return message
        if event.get("event") == "error":
            message = str(data.get("message") or "").strip()
            if message:
                return message
    return None


def _stream_feed(
    client,
    session_id: str,
    *,
    caller_id: str | None,
    renderer,
    command_timeout: float = 0.0,
    attention_reasons: list[str] | None = None,
    attention_position: str | None = None,
) -> str | dict[str, Any]:
    """Stream the remote conversation as a collapsed live feed.

    Resumes from the caller's last-acked delivery cursor and renders each
    event to stdout, then **acks the cursor only after the content is
    flushed**. This is what makes the cursor advance on *confirmed delivery*
    rather than server-side production: an ungraceful client death (SIGKILL)
    before a flush leaves the cursor where it was, so a later ``read`` resumes
    exactly where the host left off -- nothing skipped, nothing duplicated.

    Reconnects (resuming from the acked cursor) across transient connection
    loss -- e.g. a service restart mid-workflow. Terminates when the turn
    settles (session idle + backlog drained), the command timeout elapses,
    an error event arrives, or the user interrupts. Returns a status string.
    """
    import time

    from .client import BridgeClientError, BridgeConnectionError

    try:
        cursor = client.get_cursor(session_id, caller_id=caller_id)
    except Exception:
        cursor = 0

    start = time.monotonic()
    last_activity = start
    deadline = (start + command_timeout) if command_timeout else None
    max_attempts = 100000
    # Once the turn's terminal event has been rendered we are only waiting for
    # the session to settle -- suppress the transient "still working"/"still
    # running" liveness lines so a completed turn never looks like it is still
    # hanging with a climbing timer (#189c).
    turn_complete_seen = False
    # First timestamp of a consecutive run of mid-stream session-404s (the daemon
    # bounced and has not re-registered the session yet). Reset on any delivered
    # event or clean reconnect; only a 404 that persists past _STREAM_404_GRACE_S
    # is reported (dotfiles#1713).
    first_404_at: float | None = None
    attention_result: dict[str, Any] | None = None

    def _probe_attention() -> dict[str, Any] | None:
        if not attention_reasons:
            return None
        return client.wait_for_attention(
            session_id,
            reasons=attention_reasons,
            position=attention_position,
            timeout_seconds=0,
        )

    def _ack(up_to: int) -> None:
        # Best-effort: a failed ack just means a future read re-delivers
        # (no data loss), never a skip.
        try:
            client.ack_cursor(session_id, up_to, caller_id=caller_id)
        except Exception:
            pass

    for _attempt in range(max_attempts):
        stream = None
        try:
            attention_result = _probe_attention()
            if attention_result and attention_result.get("settled"):
                boundary = int(attention_result.get("boundary_event_id") or 0)
                if boundary <= cursor:
                    return attention_result
            if (
                attention_result
                and attention_result.get("identity", {}).get("successor_id")
            ):
                return attention_result
            stream = client.stream_events(
                session_id, after=cursor, caller_id=caller_id
            )
            for evt in stream:
                now = time.monotonic()
                etype = evt.get("event", "")

                if etype == "_heartbeat":
                    if not turn_complete_seen and now - last_activity >= _PROGRESS_INTERVAL:
                        sys.stdout.write(renderer.heartbeat_line(now - start))
                        sys.stdout.flush()
                        last_activity = now
                    if deadline and now > deadline:
                        print(
                            "\n[>] Timed out waiting for turn "
                            "(remote still running)", file=sys.stderr,
                        )
                        return "timeout"
                    if _turn_settled(client, session_id, cursor):
                        return "complete"
                    continue

                if etype == "tool_progress":
                    # Quiet-period liveness naming the in-flight tool call.
                    # Cursor-neutral (no id); throttled like the heartbeat.
                    if not turn_complete_seen and now - last_activity >= _PROGRESS_INTERVAL:
                        sys.stdout.write(
                            renderer.tool_progress_line(evt.get("data", {}))
                        )
                        sys.stdout.flush()
                        last_activity = now
                    if deadline and now > deadline:
                        print(
                            "\n[>] Timed out waiting for turn "
                            "(remote still running)", file=sys.stderr,
                        )
                        return "timeout"
                    if _turn_settled(client, session_id, cursor):
                        return "complete"
                    continue

                # Real event: render + flush BEFORE acking delivery.
                evt_id = evt.get("id", "")
                try:
                    new_id = int(evt_id) if evt_id else cursor
                except (ValueError, TypeError):
                    new_id = cursor

                text = renderer.render_event(etype, evt.get("data", {}))
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                last_activity = now

                if new_id > cursor:
                    cursor = new_id
                    _ack(cursor)
                    first_404_at = None  # delivered event -> session exists

                attention_result = _probe_attention()
                if attention_result and attention_result.get("settled"):
                    boundary = int(attention_result.get("boundary_event_id") or 0)
                    if boundary <= cursor:
                        return attention_result
                if (
                    attention_result
                    and attention_result.get("identity", {}).get("successor_id")
                ):
                    return attention_result

                # The terminal turn event: stop emitting liveness lines, and
                # return as soon as the session has settled (idle/terminal, no
                # backlog) instead of waiting for the next heartbeat to notice --
                # which is what used to print a stale "still working" (#189c).
                if etype == "turn_complete":
                    turn_complete_seen = True
                    if _turn_settled(client, session_id, cursor):
                        return "complete"

                if etype == "error":
                    return "error"
                if deadline and now > deadline:
                    print("\n[>] Timed out (remote still running)", file=sys.stderr)
                    return "timeout"

        except KeyboardInterrupt:
            # Cursor reflects exactly what was flushed + acked; a later read
            # resumes from here.
            print(
                f"\n[>] Interrupted -- delivered through event {cursor}",
                file=sys.stderr,
            )
            return "interrupted"
        except BridgeConnectionError:
            # Service unreachable (restarting?) -- follow a cutover to the new
            # dynamic port, then back off and resume. Not a session-404 run.
            first_404_at = None
            client.refresh_endpoint()
        except (OSError, urllib.error.URLError):
            first_404_at = None
            client.refresh_endpoint()
        except BridgeClientError as exc:
            if exc.status == 404:
                # A mid-stream session-404: the daemon bounced and has not
                # re-registered the session yet. Follow any port cutover and
                # retry within the grace window rather than declaring the session
                # gone; report (with resumable framing) only once the grace is
                # exhausted -- a settled 404 = genuinely unknown (dotfiles#1713).
                now = time.monotonic()
                if first_404_at is None:
                    first_404_at = now
                if now - first_404_at < _STREAM_404_GRACE_S:
                    client.refresh_endpoint()
                else:
                    print(
                        f"\n[RETRY] Session {session_id} is not currently "
                        "registered (the bridge may be mid-restart); if it "
                        "exists it is preserved and resumable -- re-run shortly.",
                        file=sys.stderr,
                    )
                    return "error"
            # non-404 transient -- retry
        finally:
            close_stream = getattr(stream, "close", None)
            if close_stream is not None:
                close_stream()

        now = time.monotonic()
        if deadline and now > deadline:
            return "timeout"
        if _turn_settled(client, session_id, cursor):
            return "complete"
        time.sleep(_RECONNECT_BACKOFF)

    return "gaveup"


def _cmd_wait(args: argparse.Namespace) -> None:
    """Wait for the current turn on a session to complete (streaming)."""
    client = _get_client()
    caller_id = _caller_id_for(args)
    selected = list(getattr(args, "attention", None) or [])
    if getattr(args, "all_attention", False):
        from .models import AttentionReason

        selected = [reason.value for reason in AttentionReason]
    if selected:
        _cmd_attention_wait(client, args, caller_id, selected)
        return

    session = client.get_session(args.session_id)
    status = session.get("status", "")

    if status == "idle":
        print(f"[OK] Session {args.session_id} is already idle")
        return
    if status not in ("running", "starting"):
        print(f"[>] Session {args.session_id} is {status}")
        return

    print(f"[>] Waiting for session {args.session_id}...")
    timeouts = _phased_timeouts()
    renderer = _make_renderer(args)
    _stream_feed(
        client, args.session_id,
        caller_id=caller_id,
        renderer=renderer,
        command_timeout=timeouts.command,
    )


def _render_attention_result(result: dict[str, Any]) -> str:
    """Render one bounded attention result without exposing event internals."""
    identity = result.get("identity") or {}
    current = identity.get("current_session_id") or identity.get(
        "observed_session_id"
    )
    if not result.get("settled"):
        return f"[>] Attention wait timed out; current session {current}"
    reason = str(result.get("reason") or "attention")
    reference = result.get("reference") or {}
    availability = reference.get("availability")
    suffix = f"; request is {availability}" if availability else ""
    line = f"[>] Attention required: {reason} on session {current}{suffix}"
    if reason == "permission_required":
        value = reference.get("value") or {}
        request_id = value.get("request_id")
        choices = [
            f"{option.get('option_id')} ({option.get('name') or option.get('kind') or 'option'})"
            for option in value.get("options") or []
        ]
        if request_id:
            line += f"\n    request: {request_id}"
        if choices:
            line += "\n    choices: " + ", ".join(choices)
    return line


def _contract_changed_result(
    prior: dict[str, Any], successor_id: str
) -> dict[str, Any]:
    identity = dict(prior.get("identity") or {})
    identity["current_session_id"] = successor_id
    identity["successor_id"] = successor_id
    return {
        "settled": True,
        "reason": "contract_changed",
        "identity": identity,
        "position": prior.get("position"),
        "boundary_event_id": None,
        "reference": {
            "kind": "successor",
            "ref": successor_id,
            "availability": "available",
        },
        "limitations": [
            "the successor daemon explicitly rejected the selected attention protocol"
        ],
    }


def _cmd_attention_wait(
    client,
    args: argparse.Namespace,
    caller_id: str | None,
    reasons: list[str],
) -> None:
    """Run explicit attention semantics in JSON or rendered attached mode."""
    import time

    from .client import BridgeConnectionError
    from .protocol import ATTENTION_WAIT_PROTOCOL_VERSION

    if not client.daemon_supports(ATTENTION_WAIT_PROTOCOL_VERSION):
        version, _minimum = client.daemon_protocol()
        print(
            "[FAIL] Explicit attention waits require agent-bridge HTTP protocol "
            f"v{ATTENTION_WAIT_PROTOCOL_VERSION}; daemon advertises v{version}.",
            file=sys.stderr,
        )
        sys.exit(3)

    timeouts = _phased_timeouts()
    deadline = time.monotonic() + timeouts.command if timeouts.command else None
    session_id = args.session_id
    position = getattr(args, "position", None)
    renderer = _make_renderer(args)
    last_result: dict[str, Any] | None = None

    while True:
        remaining = (
            deadline - time.monotonic() if deadline is not None else None
        )
        if remaining is not None and remaining <= 0:
            result = last_result or {
                "settled": False,
                "reason": None,
                "identity": {
                    "observed_session_id": session_id,
                    "current_session_id": session_id,
                    "successor_id": None,
                },
                "position": position,
                "limitations": ["the command timeout elapsed during recovery"],
            }
            if getattr(args, "json", False):
                print(json.dumps(result, sort_keys=True))
            else:
                print(_render_attention_result(result))
            return
        if getattr(args, "json", False):
            try:
                result = client.wait_for_attention(
                    session_id,
                    reasons=reasons,
                    position=position,
                    timeout_seconds=min(30.0, remaining or 30.0),
                )
            except BridgeConnectionError:
                client.refresh_endpoint()
                time.sleep(_RECONNECT_BACKOFF)
                continue
        else:
            print(f"[>] Waiting for attention from session {session_id}...")
            result = _stream_feed(
                client,
                session_id,
                caller_id=caller_id,
                renderer=renderer,
                command_timeout=max(0.0, remaining) if remaining is not None else 0,
                attention_reasons=reasons,
                attention_position=position,
            )
            if isinstance(result, str):
                if result == "timeout":
                    result = client.wait_for_attention(
                        session_id,
                        reasons=reasons,
                        position=position,
                        timeout_seconds=0,
                    )
                else:
                    return
        last_result = result

        successor_id = str(
            (result.get("identity") or {}).get("successor_id") or ""
        )
        if successor_id and not result.get("settled"):
            try:
                client.refresh_endpoint()
                version, _minimum = client.daemon_protocol(refresh=True)
                compatible = version >= ATTENTION_WAIT_PROTOCOL_VERSION
            except BridgeConnectionError:
                compatible = None
            if compatible is False:
                result = _contract_changed_result(result, successor_id)
            elif compatible is None:
                if deadline is not None and time.monotonic() >= deadline:
                    if getattr(args, "json", False):
                        print(json.dumps(result, sort_keys=True))
                    else:
                        print(_render_attention_result(result))
                    return
                time.sleep(_RECONNECT_BACKOFF)
                continue
            else:
                session_id = successor_id
                position = None
                continue

        if not result.get("settled"):
            continue
        if getattr(args, "json", False):
            print(json.dumps(result, sort_keys=True))
        else:
            print(_render_attention_result(result))
        return


def _cmd_read(args: argparse.Namespace) -> None:
    """Read the remote conversation from the caller's delivery cursor.

    Default: resume the live feed from the last-acked cursor and keep
    streaming (acking as content is delivered) until the turn settles,
    timeout, or interrupt.

    ``--no-follow``: deliver everything pending since the cursor, then exit
    (advances the cursor).

    ``--range A:B`` / ``--event N``: random-access historical read by event
    id. Does NOT move the delivery cursor -- the only way to re-read
    already-consumed content.
    """
    client = _get_client()
    caller_id = _caller_id_for(args)
    session_id = args.session_id
    renderer = _make_renderer(args)

    # Random-access historical read (does not touch the cursor). Supports
    # --event N, --tail N, --since ID, and --range A:B (precedence in that
    # order). All are cursor-neutral so a watcher can peek without disturbing
    # the live resume point (#46.2).
    rng = getattr(args, "range", None)
    evt = getattr(args, "event", None)
    tail = getattr(args, "tail", None)
    since = getattr(args, "since", None)
    if rng or evt is not None or tail is not None or since is not None:
        if evt is not None:
            start_id, end_id = evt, evt
        elif tail is not None:
            head = client.get_cursor_info(
                session_id, caller_id=caller_id
            ).get("head_id", 0)
            start_id, end_id = max(1, head - tail + 1), head
        elif since is not None:
            start_id, end_id = since + 1, None
        else:
            try:
                lo, _, hi = rng.partition(":")
                start_id = int(lo) if lo else 0
                end_id = int(hi) if hi else None
            except ValueError:
                print(f"[FAIL] Invalid --range '{rng}' (use A:B)", file=sys.stderr)
                sys.exit(1)
        events = client.read_range(session_id, start=start_id, end=end_id)
        if args.json:
            _json_out({"session_id": session_id, "events": events})
            return
        out = renderer.render_events(events)
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
        if not out:
            print("(no events in range)")
        return

    # Non-follow: drain everything pending since the cursor, advance, exit.
    if getattr(args, "no_follow", False):
        start_id = client.get_cursor(session_id, caller_id=caller_id)
        events = client.read_range(session_id, start=start_id + 1)
        if args.json:
            _json_out({"session_id": session_id, "events": events})
            return
        out = renderer.render_events(events)
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
        if events:
            last_id = events[-1].get("id", start_id)
            client.ack_cursor(session_id, last_id, caller_id=caller_id)
        else:
            print("(caught up -- nothing new)")
        return

    # Follow: resume the live feed from the cursor.
    timeouts = _phased_timeouts()
    _stream_feed(
        client, session_id,
        caller_id=caller_id,
        renderer=renderer,
        command_timeout=timeouts.command,
    )


def _cmd_result(args: argparse.Namespace) -> None:
    """Read a bounded delegated-result snapshot or expand one detail reference."""
    from .client import BridgeClientError
    from .protocol import REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION

    client = _get_client(ensure=False)
    try:
        represented = (
            client.daemon_supports(REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION)
            and bool(client.resolve_live_result_target(args.session_ref))
        )
        if args.expand:
            payload = (
                client.expand_live_result_ref(args.session_ref, args.expand)
                if represented
                else client.expand_result_ref(args.session_ref, args.expand)
            )
        else:
            method = (
                client.get_live_result_snapshot
                if represented
                else client.get_result_snapshot
            )
            payload = method(
                args.session_ref,
                position=args.position,
                max_items=args.max_items,
                max_text_chars=args.max_text_chars,
            )
    except BridgeClientError as exc:
        label = "UNAVAILABLE" if exc.status == 426 else "FAIL"
        print(f"[{label}] {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if args.json or args.expand:
        _json_out(payload)
        return

    identity = payload.get("identity") or {}
    state = payload.get("state") or {}
    fidelity = payload.get("fidelity") or {}
    latest = payload.get("latest_result") or {}
    incremental = payload.get("incremental") or {}

    print(
        f"  {identity.get('logical_delegate_id', args.session_ref)}"
        f"  [{state.get('session_status', '')}]"
        f"  fidelity={fidelity.get('level', 'unknown')}"
    )
    snapshot_sid = identity.get("snapshot_session_id")
    current_sid = identity.get("current_session_id")
    if snapshot_sid:
        print(f"    Session:   {snapshot_sid}")
    if current_sid and current_sid != snapshot_sid:
        print(f"    Successor: {current_sid}")
    attention = state.get("attention") or {}
    if attention.get("availability") == "available":
        print(f"    Attention: {attention.get('value') or 'none'}")
    else:
        print(
            f"    Attention: {attention.get('availability')}"
            f" ({attention.get('reason') or 'evidence unavailable'})"
        )
    active = state.get("active_work") or {}
    if active.get("value"):
        print(f"    Active:    {json.dumps(active['value'], ensure_ascii=False)}")
    pending = state.get("pending_input") or {}
    if pending.get("availability") != "available":
        print(
            f"    Input:     {pending.get('availability')}"
            f" ({pending.get('reason') or 'evidence unavailable'})"
        )
    elif pending.get("value"):
        print(f"    Input:     {json.dumps(pending['value'], ensure_ascii=False)}")

    print(f"    Latest:    {latest.get('availability', 'unknown')}")
    latest_value = latest.get("value") or {}
    if latest_value:
        stop = latest_value.get("stop_reason")
        print(
            f"      turn {latest_value.get('turn_index')}"
            + (f" ({stop})" if stop else "")
        )
        text = latest_value.get("text")
        if text:
            for line in str(text).splitlines() or [str(text)]:
                print(f"      {line}")
    if latest.get("detail_ref"):
        print(
            f"      expand: agent-bridge result {args.session_ref} "
            f"--expand {latest['detail_ref']}"
        )

    print("    Work:")
    for item in incremental.get("items") or []:
        summary = item.get("summary")
        status = item.get("status")
        suffix = f" [{status}]" if status else ""
        line = f"      {item.get('event_id')}: {item.get('kind')}{suffix}"
        if summary:
            line += f" -- {str(summary).replace(chr(10), ' ')}"
        print(line)
    if not incremental.get("items"):
        print(f"      ({incremental.get('availability', 'no items')})")
    if incremental.get("reason"):
        print(f"      {incremental['reason']}")
    if incremental.get("truncated_before"):
        print("      (older work omitted from the default latest window)")
    if incremental.get("has_more"):
        print("      (more work is available from this position)")
    if incremental.get("position"):
        print(f"    Position:  {incremental['position']}")


def _cmd_stop(args: argparse.Namespace) -> None:
    """Stop a session."""
    client = _get_client()
    client.stop_session(args.session_id, force=getattr(args, "force", False))
    print(f"[OK] Session {args.session_id} stopped")


def _cmd_end(args: argparse.Namespace) -> None:
    """End (delete) a session.

    Idempotent + quiet (#48): ending an already-ended/absent session is a
    no-op success, and any error prints a one-line message -- never a raw
    client traceback.
    """
    from .client import BridgeClientError

    client = _get_client()
    try:
        client.end_session(args.session_id, force=getattr(args, "force", False))
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[OK] Session {args.session_id} already ended")
            return
        print(f"[FAIL] Could not end session {args.session_id}: {exc.detail}")
        sys.exit(1)
    print(f"[OK] Session {args.session_id} ended")


def _cmd_resume(args: argparse.Namespace) -> None:
    """Resume a stopped session, or load/take-over a still-recognized worktree.

    The positional accepts either an **ACP session id** (owned by the bridge)
    or a **worktree handle**. Resolution order:

      1. Try to resume it as a bridge-owned ACP session (the historical
         behavior).
      2. On 404 (no such owned session), treat it as a **worktree handle** and
         ensure that worktree has a live owned session -- resuming its latest
         session, or, for a *dormant* worktree (its interactive CLI stopped,
         e.g. after a reboot), adopting the on-disk worktree with a fresh owned
         session. This is just a **note**: no confirmation needed.

    **Break-glass for a LIVE worktree.** If a *fresh live* interactive CLI still
    holds the worktree, taking it over would spawn a second controller on the
    same checkout, so the bridge refuses (409 ``live_cli_holds_worktree``).
    Stopping the live CLI first, then re-running with ``--force``, performs the
    affirmative take-over.
    """
    from .client import BridgeClientError

    client = _get_client()
    target = args.session_id
    reclaim = bool(getattr(args, "force", False))

    # 1. Try the historical owned-ACP-session resume first. A worktree handle
    #    is not an owned session id, so this 404s and we fall through.
    if not reclaim:
        try:
            result = client.resume_session(
                target,
                request_timeout=_startup_request_timeout(resume=True),
            )
            status = result.get("status", "")
            print(f"[OK] Session {target} resumed ({status})")
            return
        except BridgeClientError as exc:
            if exc.status != 404:
                print(
                    f"[FAIL] Could not resume session {target}: {exc.detail}",
                    file=sys.stderr,
                )
                sys.exit(1)
            # 404 -> not an owned session; fall through to worktree resolution.

    # 2. Treat the target as a worktree handle: load it (dormant = a note) or
    #    take it over (--force past a live holder = break-glass).
    try:
        result = client.resume_worktree(
            target,
            reclaim=reclaim,
            request_timeout=_startup_request_timeout(
                resume=True,
                fresh_fallback=True,
            ),
        )
    except BridgeClientError as exc:
        if exc.status == 409:
            detail = exc.detail
            reason = detail.get("reason") if isinstance(detail, dict) else None
            if reason == "live_cli_holds_worktree":
                holder = (
                    detail.get("session_id")
                    if isinstance(detail, dict)
                    else None
                )
                print(
                    f"[BREAK-GLASS] A live interactive CLI (session {holder}) "
                    f"still holds worktree {target}.\n"
                    "  Taking it over would run a second controller on the same "
                    "checkout.\n"
                    "  Stop that CLI first, then re-run with --force to take it "
                    "over.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"[FAIL] Could not resume worktree {target}: {exc.detail}",
                  file=sys.stderr)
            sys.exit(1)
        if exc.status == 404:
            print(
                f"[FAIL] {target} is neither a bridge-owned session nor a "
                "recognized worktree. Pass a worktree handle (e.g. "
                "'<machine>-<env>-<ts>-<id>') to load a dormant worktree.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[FAIL] Could not resume worktree {target}: {exc.detail}",
              file=sys.stderr)
        sys.exit(1)

    status = result.get("status", "")
    sid = result.get("session_id", "") or target
    verb = "took over" if reclaim else "loaded"
    print(f"[OK] Worktree {target} {verb} as owned session {sid} ({status})")


def _cmd_handoff(args: argparse.Namespace) -> None:
    """Hand a session (or worktree) off to a fresh successor in place.

    The positional accepts either a bridge-owned **ACP session id** or a
    **worktree handle** (mirroring ``resume``): a session id is handed off
    directly; a worktree handle resolves to that worktree's current session.
    The retiring session authors a continuation brief, a successor is spawned
    in the same worktree, a ``session_handoff`` event announces the changeover,
    and the successor resumes seeded with the brief.
    """
    from .client import BridgeClientError

    client = _get_client()
    target = args.session_id
    reason = getattr(args, "reason", None)
    seed = not getattr(args, "no_seed", False)

    def _report(result: dict, kind: str) -> None:
        sid = result.get("session_id", "") or "(unknown)"
        status = result.get("status", "")
        print(
            f"[OK] {kind} {target} handed off -> successor {sid} ({status})"
        )

    # 1. Try a bridge-owned-session handoff first. A worktree handle is not an
    #    owned session id, so this 404s and we fall through.
    try:
        result = client.handoff_session(target, reason=reason, seed=seed)
        _report(result, "Session")
        return
    except BridgeClientError as exc:
        if exc.status == 404:
            pass  # not an owned session; try worktree resolution below.
        elif exc.status == 409:
            print(f"[FAIL] Cannot hand off {target}: {exc.detail}", file=sys.stderr)
            sys.exit(1)
        else:
            print(
                f"[FAIL] Could not hand off session {target}: {exc.detail}",
                file=sys.stderr,
            )
            sys.exit(1)

    # 2. Treat the target as a worktree handle.
    try:
        result = client.handoff_worktree(target, reason=reason, seed=seed)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(
                f"[FAIL] {target} is neither a bridge-owned session nor a "
                "worktree with a current session to hand off.",
                file=sys.stderr,
            )
            sys.exit(1)
        if exc.status == 409:
            print(f"[FAIL] Cannot hand off worktree {target}: {exc.detail}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[FAIL] Could not hand off worktree {target}: {exc.detail}",
              file=sys.stderr)
        sys.exit(1)
    _report(result, "Worktree")


def _cmd_session_usage(args: argparse.Namespace) -> None:
    """Show context window usage for a session."""
    client = _get_client()
    usage = client.get_session_usage(args.session_id)
    if args.json:
        _json_out(usage)
        return

    ctx_size = usage.get("context_size")
    ctx_used = usage.get("context_used")
    ctx_pct = usage.get("context_pct")
    model = usage.get("usage_model") or "(unknown)"
    last_at = usage.get("last_usage_at") or ""
    turns = usage.get("turn_count", 0)
    status = usage.get("status", "")

    print(f"Session:  {args.session_id} ({status})")
    print(f"Model:    {model}")
    print(f"Turns:    {turns}")
    if ctx_size and ctx_used is not None:
        print(f"Context:  {ctx_used:,} / {ctx_size:,} tokens ({ctx_pct}%)")
        bar_width = 30
        filled = int(bar_width * ctx_used / ctx_size)
        bar = "#" * filled + "-" * (bar_width - filled)
        print(f"          [{bar}]")
    else:
        print("Context:  (no usage data yet)")
    if last_at:
        print(f"Updated:  {_short_dt(last_at)}")


def _cmd_answer(args: argparse.Namespace) -> None:
    """Answer a dispatched agent's parked ``ask_user`` question (backstop).

    Resolves the remote agent's blocked ``ask_user`` so its turn continues --
    the host acting as the human the dispatched agent reached for (dotfiles#1275).
    Defaults the target question to the sole pending one; requires
    ``--tool-call-id`` when several are outstanding.
    """
    from .client import BridgeClientError

    client = _get_client()
    caller_id = _caller_id_for(args)
    sid = args.session_id

    action = "accept"
    if args.decline:
        action = "decline"
    elif args.cancel:
        action = "cancel"

    content: dict[str, Any] = {}
    if action == "accept":
        if args.content_json:
            try:
                content = json.loads(args.content_json)
                if not isinstance(content, dict):
                    raise ValueError("content must be a JSON object")
            except Exception as exc:
                print(f"[FAIL] --json is not a valid JSON object: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            for pair in args.fields:
                key, sep, value = pair.partition("=")
                if not sep:
                    print(f"[FAIL] --field must be KEY=VALUE (got {pair!r})", file=sys.stderr)
                    sys.exit(1)
                content[key.strip()] = value

    # Resolve which parked question to answer. Default to the sole pending one.
    tool_call_id = args.tool_call_id
    if not tool_call_id:
        try:
            st = client.get_session_status(sid, caller_id=caller_id)
        except BridgeClientError as exc:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            sys.exit(1)
        pending = st.get("pending_ask_user") or []
        if not pending:
            print(f"[FAIL] Session {sid} has no parked ask_user to answer.", file=sys.stderr)
            sys.exit(1)
        if len(pending) > 1:
            ids = ", ".join(q.get("tool_call_id", "?") for q in pending)
            print(
                f"[FAIL] {len(pending)} questions are pending on {sid}; pass "
                f"--tool-call-id (one of: {ids}).",
                file=sys.stderr,
            )
            sys.exit(1)
        tool_call_id = pending[0].get("tool_call_id")

    try:
        client.answer_ask_user(sid, tool_call_id, content, action=action)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[FAIL] Session {sid} not found", file=sys.stderr)
        elif exc.status == 409:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
        else:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Answered ask_user ({action}) on {sid}; the agent's turn continues.")


def _cmd_agent(args: argparse.Namespace) -> None:
    """Run agent-bridge as an upstream ACP agent on stdio."""
    import asyncio
    from pathlib import Path

    from . import telemetry
    from .acp_agent import BridgeAgent
    from .agent_registry import build_resolver
    from .config import load_config
    from .db import Database
    from .session_manager import session_manager_from_config

    log = logging.getLogger("agent-bridge")

    cfg = load_config()
    if not telemetry.load_sink_from_config():
        telemetry.load_sink_from_env()

    # Initialize DB and session manager via the shared config factory so
    # ACP-agent mode wires session-host settings identically to the HTTP daemon.
    # Session Hosts are always on (dotfiles#1478): a CodeSpace bridged via
    # ACP-agent mode survives a brief SSH drop instead of dying with it
    # (#145/#177).
    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    sm = session_manager_from_config(db, cfg)

    # Load topology/resolver (includes auto-discovered local agents)
    resolver = build_resolver(cfg)
    sm.set_resolver(resolver)

    agent_name = getattr(args, "agent", None)
    if not agent_name:
        print("[FAIL] --agent is required for agent mode", file=sys.stderr)
        sys.exit(1)

    # Validate agent exists and normalize aliases to the canonical identity.
    canonical_agent = resolver.canonical_agent_name(agent_name) if resolver else None
    if resolver and canonical_agent is None:
        available = list(resolver.agents.keys())
        print(
            f"[FAIL] Agent '{agent_name}' not found. Available: {available}",
            file=sys.stderr,
        )
        sys.exit(1)
    if canonical_agent:
        agent_name = canonical_agent

    bridge_agent = BridgeAgent(
        sm, resolver=resolver, default_agent=agent_name,
    )

    log.info("Starting ACP agent mode (agent=%s)", agent_name)

    async def _run() -> None:
        from acp import run_agent

        try:
            await run_agent(bridge_agent)
        finally:
            await bridge_agent.cleanup()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------


def _cmd_config_show(args: argparse.Namespace) -> None:
    """Show current configuration."""
    from .config import config_dir, load_config

    cfg = load_config()
    cfg_path = config_dir() / "config.yaml"

    if args.json:
        _json_out(cfg.model_dump())
        return

    print(f"Config: {cfg_path}")
    print(f"  port: {cfg.port}")
    print(f"  bind: {cfg.bind}")
    print(f"  db_path: {cfg.db_path}")
    print(f"  log_level: {cfg.log_level}")
    print()
    if cfg.topologies:
        print("Topologies:")
        for name, profile in cfg.topologies.items():
            print(f"  {name}:")
            if profile.machines_yaml:
                print(f"    machines_yaml: {profile.machines_yaml}")
            if profile.agents_config:
                print(f"    agents_config: {profile.agents_config}")
    else:
        print("Topologies: (none)")


def _cmd_config_adopt(args: argparse.Namespace) -> None:
    """Add or update a topology profile for a repo."""
    from .config import adopt_topology

    try:
        cfg = adopt_topology(
            profile_name=args.profile,
            repo_path=args.repo,
            machines_yaml=getattr(args, "machines_yaml", None),
            agents_config=getattr(args, "agents_config", None),
        )
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)

    profile = cfg.topologies[args.profile]
    print(f"[OK] Topology profile '{args.profile}' configured")
    if profile.machines_yaml:
        print(f"  machines_yaml: {profile.machines_yaml}")
    if profile.agents_config:
        print(f"  agents_config: {profile.agents_config}")
    print()
    print("[>] Restart agent-bridge to load the new topology")


def _cmd_config_remove(args: argparse.Namespace) -> None:
    """Remove a topology profile."""
    from .config import remove_topology

    try:
        remove_topology(args.profile)
    except KeyError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] Topology profile '{args.profile}' removed")


def _cmd_config_validate(args: argparse.Namespace) -> None:
    """Validate the current configuration."""
    from .config import validate_config

    issues = validate_config()
    if not issues:
        print("[OK] Configuration is valid")
        return

    print(f"[WARN] {len(issues)} issue(s) found:")
    for issue in issues:
        print(f"  - {issue}")
    sys.exit(1)


def _cmd_config_migrate(args: argparse.Namespace) -> None:
    """Migrate the machine-local config.yaml schema in place (idempotent + atomic).

    Machine-local only (never touches repo config); safe no-op when the vendored
    ``config_migrate`` library is absent. Invoked once from the installer's
    install/update flow, and available on demand.
    """
    from . import config_migrations

    if not config_migrations.available():
        print("config-migrate: migration library unavailable; skipping")
        return
    print(config_migrations.summarize(config_migrations.run_migrations()))


def _cmd_doctor(args: argparse.Namespace) -> None:
    """Audit providers.d without activating provider commands."""
    from .provider_sources import scan_provider_registry

    report = scan_provider_registry()
    payload = {
        "registry": "providers.d",
        "authority": report.snapshot.authority.value,
        "active": sorted(report.manifests),
        "findings": [finding.to_dict() for finding in report.findings],
    }
    if args.json:
        _json_out(payload)
    elif not report.findings:
        print(
            f"[OK] providers.d is {report.snapshot.authority.value}; "
            f"{len(report.manifests)} provider namespace(s) active."
        )
    else:
        print(
            f"[WARN] providers.d has {len(report.findings)} finding(s); "
            "valid providers remain available:"
        )
        for finding in report.findings:
            target = f" -> {finding.target}" if finding.target else ""
            print(f"  - {finding.reason}: {finding.entry}{target}")
            if finding.detail:
                print(f"    {finding.detail}")
            if finding.remedy:
                print(f"    {finding.remedy}")
    if report.findings:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser (extracted so it is unit-testable)."""
    parser = argparse.ArgumentParser(
        prog="agent-bridge",
        description="Persistent inter-agent communication service",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--json", action="store_true", default=False,
        help="Output in JSON format",
    )
    parser.add_argument(
        "--project", "-p", dest="project", default=None, metavar="REPO",
        help="Scope project-addressed verbs to REPO instead of the caller's cwd "
             "project. For send/create the remote worktree resolve targets REPO; "
             "for agents/machines the displayed catalog is filtered to REPO. "
             "Injected by the `<repo> <slug>` router.",
    )

    sub = parser.add_subparsers(dest="command")

    # -- Server commands --

    start_p = sub.add_parser("start", help="Start the agent-bridge server")
    start_p.add_argument("--port", type=int, help="Port to listen on")
    start_p.add_argument("--bind", type=str, help="Address to bind to")
    start_p.add_argument(
        "--idle-shutdown", type=int, default=None, metavar="SECONDS",
        help="Exit after this many seconds with no active sessions "
             "(0 = never). Used by the elevated sub-daemon.",
    )
    start_p.add_argument(
        "--passive", action="store_true",
        help="Start as a passive cutover instance: do NOT self-publish the "
             "routing table (the deploy orchestrator flips it after a health "
             "check) and do NOT bind the credential relay (the active daemon "
             "owns it until cutover completes).",
    )
    start_p.set_defaults(func=_cmd_start)

    # Relay stdio <-> a remote bridge's ACP-over-WebSocket endpoint. Used as a
    # type="command" spawn target so the primary bridge can route an elevated /
    # federated agent to a sub-daemon's /acp/<agent> without spawning copilot
    # itself (see acp_connect.py).
    acp_connect_p = sub.add_parser(
        "acp-connect",
        help="Relay stdio <-> a remote bridge's ACP-over-WebSocket endpoint",
    )
    acp_connect_p.add_argument(
        "url", help="ws(s):// URL, e.g. ws://127.0.0.1:9281/acp/<agent>"
    )
    acp_connect_p.add_argument(
        "--token", default=None,
        help="Bearer token (default: this machine's bridge token)",
    )
    acp_connect_p.add_argument(
        "--no-token", action="store_true",
        help="Connect without a bearer token",
    )
    acp_connect_p.add_argument(
        "--stdio", action="store_true",
        help="Bridge over stdin/stdout (default; accepted for symmetry with "
             "'copilot --acp --stdio')",
    )
    acp_connect_p.set_defaults(func=_cmd_acp_connect)

    # Far-side runner: resolve an agent locally and host it in a Session Host.
    # The program every boundary Spawner launches on the far side (elevated
    # scheduled task / ssh / CodeSpace). Writes a pid/child_pid/port state file
    # the Spawner reads; the connect nonce arrives via env.
    sha_p = sub.add_parser(
        "session-host-agent",
        help="Resolve an agent locally and run it inside a Session Host "
             "(far-side runner for elevation / ssh / CodeSpace)",
    )
    sha_p.add_argument("agent", help="Agent name to resolve and host")
    sha_p.add_argument("--port", type=int, default=0,
                       help="Loopback port to serve on (0 = auto)")
    sha_p.add_argument("--state-file", default=None,
                       help="Path to write the pid/child_pid/port state JSON")
    sha_p.add_argument("--cwd", default=None,
                       help="Override the resolved working directory")
    sha_p.set_defaults(func=_cmd_session_host_agent)

    # Elevated sub-daemon management (Windows): a second, admin-token bridge on a
    # loopback port that the primary relays elevated agents to (Capability 2).
    elev_p = sub.add_parser(
        "elevated", help="Manage the elevated sub-daemon (Windows)"
    )
    elev_sub = elev_p.add_subparsers(dest="elevated_action")
    elev_sub.add_parser(
        "start",
        help="Start the elevated sub-daemon (one UAC on first use, then headless)",
    )
    elev_stop = elev_sub.add_parser(
        "stop", help="Stop the elevated sub-daemon (headless; keeps the task)"
    )
    elev_stop.add_argument(
        "--deregister", action="store_true",
        help="Also delete the scheduled task (one UAC) -- full teardown",
    )
    elev_sub.add_parser("status", help="Show elevated sub-daemon status")
    elev_p.set_defaults(func=_cmd_elevated)

    status_p = sub.add_parser(
        "status",
        help="Check if agent-bridge is running, or show a session's status",
    )
    status_p.add_argument(
        "session_id", nargs="?",
        help="Session ID -- show that dispatch's compact status (state, "
             "in-flight tool + elapsed, cursor lag) instead of service health",
    )
    status_p.add_argument(
        "--steps", type=int, default=0, metavar="K",
        help="Also show the last K collapsed steps (cursor-neutral; default 0)",
    )
    _add_stream_args(status_p)
    status_p.set_defaults(func=_cmd_status)

    readiness_p = sub.add_parser(
        "installer-readiness",
        help="Emit the plugin-owned installer/readiness contract state as JSON",
    )
    readiness_p.set_defaults(func=_cmd_installer_readiness)

    doctor_p = sub.add_parser(
        "doctor",
        help="Audit provider drop-ins and report exact stale-entry cleanup",
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit structured provider findings",
    )
    doctor_p.set_defaults(func=_cmd_doctor)

    parity_p = sub.add_parser(
        "parity",
        help="Run redacted launch/auth/reattach acceptance for a remote venue",
    )
    parity_p.add_argument("target", help="Remote agent target (container: or codespace:)")
    parity_p.add_argument("--expect-workspace")
    parity_p.add_argument("--expect-capability")
    parity_p.add_argument(
        "--auth",
        action="store_true",
        help="Require redacted GitHub credential and gh API checks",
    )
    parity_p.add_argument(
        "--ado-url",
        help="ADO Git URL to check with credential fill + ls-remote (values redacted)",
    )
    parity_p.add_argument(
        "--azure-scope",
        help="Azure scope to mint through azure-auth-helper (value redacted)",
    )
    parity_p.add_argument("--startup-timeout", type=float, default=600.0)
    parity_p.add_argument("--turn-timeout", type=float, default=600.0)
    parity_p.add_argument("--keep-session", action="store_true")
    parity_p.add_argument(
        "--fault",
        choices=[
            FRONTEND_RESTART_HOSTINDEX_LOSS,
            RELAY_INTERRUPTION,
            FAILED_ACP_HANDSHAKE_FAULT,
            CONTAINER_RECREATE_FAULT,
        ],
        help=(
            "Run an explicit destructive fault scenario. Faults refuse other "
            "active managed sessions and affect only the harness-created "
            "launch transaction, HostIndex, or supervised credential relay."
        ),
    )
    parity_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit structured redacted evidence",
    )
    parity_p.set_defaults(func=_cmd_parity)

    service_p = sub.add_parser(
        "service",
        help="Control the agent-bridge daemon (start/stop/restart/status)",
    )
    service_sub = service_p.add_subparsers(dest="service_action")
    for _act, _help in (
        ("start", "Start the agent-bridge daemon"),
        ("stop", "Stop the agent-bridge daemon"),
        ("restart", "Restart the agent-bridge daemon"),
        ("status", "Show daemon status, port, and PID"),
    ):
        service_sub.add_parser(_act, help=_help)
    service_p.set_defaults(func=_cmd_service)

    ver_p = sub.add_parser("version", help="Print version")
    ver_p.set_defaults(func=_cmd_version)

    carrier_p = sub.add_parser(
        "carrier",
        help="Run the framed remote Agent Bridge carrier endpoint",
    )
    carrier_p.add_argument(
        "--stdio",
        action="store_true",
        help="Serve the carrier protocol on stdin/stdout",
    )
    carrier_p.set_defaults(func=_cmd_carrier)

    remote_p = sub.add_parser(
        "remote",
        help="Proxy exact remote Bridge reads and event subscriptions",
    )
    remote_sub = remote_p.add_subparsers(dest="remote_action")
    remote_status_p = remote_sub.add_parser(
        "status", help="Read one exact remote session status"
    )
    remote_status_p.add_argument("host", help="Topology machine key or SSH alias")
    remote_status_p.add_argument("session_id", help="Exact hosting-Bridge session id")
    remote_status_p.add_argument(
        "--caller-id",
        required=True,
        help="Stable identity unique to this event consumer",
    )
    remote_status_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output in JSON format",
    )
    remote_status_p.set_defaults(func=_cmd_remote)
    remote_live_p = remote_sub.add_parser(
        "live-session",
        help="Resolve one exact represented session on the hosting Bridge",
    )
    remote_live_p.add_argument("host", help="Topology machine key or SSH alias")
    remote_live_p.add_argument("session_id", help="Exact live session id")
    remote_live_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output in JSON format",
    )
    remote_live_p.set_defaults(func=_cmd_remote)
    remote_events_p = remote_sub.add_parser(
        "events",
        help="Subscribe to exact durable remote session events",
    )
    remote_events_p.add_argument("host", help="Topology machine key or SSH alias")
    remote_events_p.add_argument("session_id", help="Exact hosting-Bridge session id")
    remote_events_p.add_argument(
        "--caller-id",
        required=True,
        help="Stable identity unique to this event consumer",
    )
    remote_events_p.add_argument(
        "--after",
        type=int,
        default=None,
        help="Expected durable caller cursor (must match the hosting Bridge)",
    )
    remote_events_p.add_argument(
        "--continuity-id",
        default=None,
        help="Expected event-log continuity returned by a prior subscription",
    )
    remote_events_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output in JSON format",
    )
    remote_events_p.set_defaults(func=_cmd_remote)

    token_p = sub.add_parser(
        "token",
        help="Print the bearer token for external ACP clients (acp-ui, /ui)",
    )
    token_p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Also print the token source path and connect URLs",
    )
    token_p.set_defaults(func=_cmd_token)

    # -- Client commands --

    agents_p = sub.add_parser("agents", help="List registered agents")
    agents_p.add_argument(
        "--all-projects", action="store_true",
        help="Show the fleet-wide catalog instead of the cwd/--project scope",
    )
    agents_p.set_defaults(func=_cmd_agents)

    machines_p = sub.add_parser("machines", help="List topology machines")
    machines_p.add_argument(
        "--all-projects", action="store_true",
        help="Show every topology machine instead of the cwd/--project scope",
    )
    machines_p.set_defaults(func=_cmd_machines)

    live_p = sub.add_parser(
        "live-sessions",
        help="List/resolve registered live interactive CLI sessions",
    )
    live_sub = live_p.add_subparsers(dest="live_action")
    live_list_p = live_sub.add_parser(
        "list", help="List registered live interactive CLI sessions"
    )
    live_list_p.add_argument("--worktree-id", help="Filter by worktree id")
    live_list_p.add_argument(
        "--all",
        dest="include_dead",
        action="store_true",
        help="include dead (expired / taken-over) rows, normally hidden",
    )
    live_list_p.set_defaults(func=_cmd_live_sessions)
    live_resolve_p = live_sub.add_parser(
        "resolve",
        help="Resolve a session id OR worktree handle to its live session",
    )
    live_resolve_p.add_argument(
        "--handle", required=True, help="Exact session id OR worktree handle"
    )
    live_resolve_p.set_defaults(func=_cmd_live_sessions)
    live_progress_p = live_sub.add_parser(
        "progress",
        help="Record an operator-driven session's progress beat (Phase 7 7c)",
    )
    live_progress_p.add_argument(
        "--handle", required=True, help="Exact session id OR worktree handle"
    )
    live_progress_p.add_argument(
        "--summary", required=True,
        help="one-line status toward the goal (hard-capped; keep it a line)",
    )
    live_progress_p.add_argument("--phase", default="", help="short phase label")
    live_progress_p.add_argument("--blocker", help="a real blocker, if any")
    live_progress_p.add_argument("--pr", help="the PR/ref this beat corresponds to")
    live_progress_p.set_defaults(func=_cmd_live_sessions)
    live_p.set_defaults(func=_cmd_live_sessions)

    sessions_p = sub.add_parser("sessions", help="List sessions")
    sessions_p.add_argument("--status", help="Filter by status")
    sessions_p.set_defaults(func=_cmd_sessions)

    peek_p = sub.add_parser(
        "peek",
        help="Copilot-free peek at a target's current session transcript "
             "(events.jsonl snapshot + reuse-worthiness) without launching ACP",
    )
    peek_p.add_argument(
        "target", help="Session ID or agent name (e.g. codespace:<name>)"
    )
    peek_p.add_argument(
        "--tail", type=int, default=400,
        help="Trailing events.jsonl lines to scan (default 400)",
    )
    peek_p.add_argument(
        "--recent", type=int, default=8,
        help="Recent user+assistant messages / tool calls to surface (default 8)",
    )
    peek_p.add_argument(
        "--message-chars", dest="message_chars", type=int, default=400,
        help="Max chars per surfaced message (default 400)",
    )
    peek_p.add_argument(
        "--timeout", type=float, default=90.0,
        help="Remote read timeout seconds for a codespace target (default 90)",
    )
    peek_p.add_argument(
        "--stale-hours", dest="stale_hours", type=float, default=6.0,
        help="Age past which a session is 'cold' in the verdict (default 6h)",
    )
    peek_p.add_argument("--json", action="store_true", help="Emit JSON.")
    peek_p.set_defaults(func=_cmd_peek)

    gc_p = sub.add_parser(
        "gc",
        help="Garbage-collect aged terminal/disconnected sessions and compact "
             "the sessions.db (reclaims freelist bloat)",
    )
    gc_p.set_defaults(func=_cmd_gc)

    drain_p = sub.add_parser(
        "drain",
        help="Stop accepting new sessions/turns and wait for in-flight work to "
             "settle (zero-downtime pre-swap step)",
    )
    drain_p.add_argument(
        "--timeout", type=float, default=300.0, metavar="SECONDS",
        help="Max seconds to wait for busy sessions to settle (default 300).",
    )
    drain_p.add_argument(
        "--poll", type=float, default=1.0, metavar="SECONDS",
        help="Poll interval while waiting (default 1.0).",
    )
    drain_p.add_argument(
        "--force", action="store_true",
        help="Proceed (exit 0) even if busy sessions remain at timeout.",
    )
    drain_p.add_argument("--json", action="store_true", help="Emit JSON.")
    drain_p.set_defaults(func=_cmd_drain)

    undrain_p = sub.add_parser(
        "undrain",
        help="Release the drain gate -- resume accepting new work (cutover "
             "rollback)",
    )
    undrain_p.set_defaults(func=_cmd_undrain)

    deploy_p = sub.add_parser(
        "deploy",
        # Demoted to an INSTALLER-INTERNAL seam (Thread B, correct-install-flows):
        # the installer's `update`/activation drives the ZDD cutover automatically
        # whenever a live daemon is running, so an operator never runs this. Kept
        # (functional) for the installer to invoke and for cutover self-recovery
        # (invariant #1: no operator deploy verb).
        help="(internal) installer-driven ZDD cutover seam -- activation runs it "
             "automatically on update; operators do not invoke it directly",
    )
    deploy_p.add_argument(
        "--health-timeout", type=float, default=60.0, metavar="SECONDS",
        help="Max seconds to wait for the new daemon to become healthy.",
    )
    deploy_p.add_argument(
        "--drain-timeout", type=float, default=300.0, metavar="SECONDS",
        help="Max seconds to wait for the old daemon's in-flight work to settle.",
    )
    deploy_p.add_argument(
        "--force", action="store_true",
        help="Proceed with cutover even if the old daemon does not fully drain.",
    )
    deploy_p.add_argument(
        "--recover", action="store_true",
        help="Only heal a prior aborted cutover: undrain a survivor left "
             "drained by a cutover that never completed, then exit. Does not "
             "start a new cutover.",
    )
    deploy_p.add_argument("--json", action="store_true", help="Emit JSON.")
    deploy_p.set_defaults(func=_cmd_deploy)

    send_p = sub.add_parser(
        "send", help="Send a prompt to an agent or session (reuses/resumes "
        "this caller's existing session)"
    )
    send_p.add_argument(
        "target",
        help="Agent name, session ID, or a live session's worktree handle "
             "(a worktree handle resolves to whichever session is live now)",
    )
    send_p.add_argument(
        "prompt", nargs="?", default=None,
        help="Prompt text to send (omit and use --prompt-file for multi-line "
             "prompts that shouldn't transit the shell's argv)",
    )
    send_p.add_argument(
        "--prompt-file", dest="prompt_file", default=None, metavar="PATH",
        help="Read the prompt from PATH (or '-' for stdin) instead of the "
             "positional argument. Use this for multi-line prompts to avoid "
             "shell argv mangling (e.g. PowerShell word-splitting a prompt at "
             "the first embedded double-quote). Mutually exclusive with the "
             "positional prompt.",
    )
    send_p.add_argument(
        "--sender", "--from", dest="sender", default=None,
        help="Attribution label when the target is a live interactive session "
             "(default: this caller's worktree/user). Legibility only, not routing.",
    )
    send_p.add_argument(
        "--reply-to", dest="reply_to", default=None,
        help="Routable handle a reply should target when delivering to a live "
             "session -- a worktree handle (survives handoff) or a session id "
             "(default: this caller's own worktree handle, else its session id "
             "from the environment). Rendered as the envelope's reply-to.",
    )
    send_p.add_argument(
        "--kind", choices=["prompt", "notify", "status-check"], default="prompt",
        help="Typed intent when delivering to a live session: 'prompt' (a work "
             "directive, default) vs 'notify'/'status-check' (asks only for a "
             "terse out-of-band ack, never treated as new work).",
    )
    send_p.add_argument(
        "--notify", action="store_true",
        help="Shorthand for --kind notify (informational; no work expected).",
    )
    send_p.add_argument(
        "--status-check", dest="status_check", action="store_true",
        help="Shorthand for --kind status-check (asks for a terse status ack).",
    )
    send_p.add_argument(
        "--no-wait", action="store_true",
        help="Return immediately without waiting for response",
    )
    send_p.add_argument(
        "--reply-timeout", type=float, default=120.0, metavar="SECONDS",
        help="When delivering to a live interactive session, how long to wait "
             "for the receiver's reply turn before returning (default 120; the "
             "message is queued and still delivered on timeout). Ignored with "
             "--no-wait.",
    )
    send_p.add_argument(
        # Retained (hidden) so the removal handler can emit a friendly redirect
        # when someone still passes it; not advertised in `send --help` (#468).
        "--new", action="store_true",
        help=argparse.SUPPRESS,
    )
    send_p.add_argument(
        "--full-history", action="store_true",
        help="When resuming an existing session, replay its prior conversation "
             "instead of fast-forwarding past it (default hides the backlog "
             "and prints a marker)",
    )
    send_p.add_argument(
        "--force", action="store_true",
        help="If the target's session is busy running a turn, terminate that "
             "in-flight turn and start a fresh session to deliver this prompt "
             "(discards the in-flight turn's work). Without --force, a busy "
             "target is rejected with guidance to wait/observe or end it.",
    )
    send_p.add_argument(
        "--queue", action="store_true",
        help="If the target's session is busy, durably queue this prompt "
             "server-side (in the bridge's pending_prompts table) for FIFO "
             "delivery when the current turn settles -- surviving a caller "
             "remount and a bridge/host restart -- instead of rejecting it. "
             "The opposite of --force: it preserves the in-flight turn.",
    )
    send_p.add_argument(
        "--idempotency-key",
        help="stable producer key; retries return the original live-message id "
             "instead of enqueuing a duplicate",
    )
    send_p.add_argument(
        "--expected-session-id",
        help="deliver only if the target still resolves to this exact live "
             "session id (checked again atomically when enqueuing)",
    )
    _add_stream_args(send_p)
    send_p.set_defaults(func=_cmd_send)

    create_p = sub.add_parser(
        "create",
        help="Create a fresh session for an agent (optionally send a first "
             "prompt). Refuses if a one-session-per-CodeSpace agent is busy.",
    )
    create_p.add_argument("target", help="Agent name (not a session ID)")
    create_p.add_argument(
        "prompt", nargs="?", default=None,
        help="Optional first prompt to send to the new session",
    )
    create_p.add_argument(
        "--prompt-file", dest="prompt_file", default=None, metavar="PATH",
        help="Read the first prompt from PATH (or '-' for stdin) instead of the "
             "positional argument. Use this for multi-line prompts to avoid "
             "shell argv mangling (e.g. PowerShell word-splitting a prompt at "
             "the first embedded double-quote). Mutually exclusive with the "
             "positional prompt.",
    )
    create_p.add_argument(
        "--no-wait", action="store_true",
        help="Return immediately without waiting for response",
    )
    create_p.add_argument(
        "--model", dest="model", default=None, metavar="MODEL",
        help="Run THIS session on MODEL (e.g. gpt-5.6-sol). Copilot ignores "
             "--model in ACP mode, so the bridge applies it per-session via "
             "session/set_config_option, at highest precedence over the daemon "
             "default. Omit to keep the daemon's default model.",
    )
    create_p.add_argument(
        "--session-id-file", dest="session_id_file", default=None,
        metavar="PATH",
        help="Atomically write this process's exact created session id before "
             "streaming the first turn.",
    )
    create_p.add_argument(
        "--effort", dest="effort", default=None, metavar="EFFORT",
        help="Reasoning-effort override for THIS session (e.g. low|medium|high), "
             "applied the same per-session way as --model.",
    )
    create_p.add_argument(
        "--target-dir", dest="target_dir", default=None, metavar="PATH",
        help="Run this agent session in an existing checkout directory.",
    )
    create_p.add_argument(
        "--worktree-id", dest="worktree_id", default=None, metavar="ID",
        help="Bind the created session to an existing agent-worktrees worktree id.",
    )
    _add_stream_args(create_p)
    create_p.set_defaults(func=_cmd_create)

    wait_p = sub.add_parser(
        "wait", help="Wait for current turn to complete"
    )
    wait_p.add_argument("session_id", help="Session ID")
    wait_p.add_argument(
        "--attention",
        action="append",
        choices=[
            "turn_complete",
            "turn_cancelled",
            "failed",
            "input_required",
            "permission_required",
            "unreachable",
            "policy_required",
            "contract_changed",
            "stopped",
            "ended",
        ],
        help="Settle on this attention reason (repeatable); omitted keeps the "
             "legacy turn-only wait",
    )
    wait_p.add_argument(
        "--all-attention",
        action="store_true",
        help="Settle on any stable attention reason",
    )
    wait_p.add_argument(
        "--position",
        help="Resume from an opaque cursor-neutral attention position",
    )
    wait_p.add_argument(
        "--json",
        action="store_true",
        help="Print one structured settlement without opening the delivery stream",
    )
    _add_stream_args(wait_p)
    wait_p.set_defaults(func=_cmd_wait)

    read_p = sub.add_parser(
        "read",
        help="Read/resume a session's conversation from the delivery cursor",
    )
    read_p.add_argument("session_id", help="Session ID")
    read_p.add_argument(
        "--no-follow", action="store_true",
        help="Deliver everything pending since the cursor, then exit "
             "(do not wait for completion)",
    )
    read_p.add_argument(
        "--range", metavar="A:B",
        help="Random-access read of event ids A..B (inclusive). Does NOT "
             "move the delivery cursor.",
    )
    read_p.add_argument(
        "--event", type=int, metavar="N",
        help="Random-access read of a single event id N. Does NOT move the "
             "delivery cursor.",
    )
    read_p.add_argument(
        "--tail", type=int, metavar="N",
        help="Random-access read of the last N events. Does NOT move the "
             "delivery cursor.",
    )
    read_p.add_argument(
        "--since", type=int, metavar="ID",
        help="Random-access read of events after event id ID (incremental "
             "only-new). Does NOT move the delivery cursor.",
    )
    _add_stream_args(read_p)
    read_p.set_defaults(func=_cmd_read)

    result_p = sub.add_parser(
        "result",
        help="Read a bounded delegated-result snapshot without moving a cursor",
    )
    result_p.add_argument(
        "session_ref",
        help="Session ID, ACP session ID, or authoritative worktree handle",
    )
    result_p.add_argument(
        "--position",
        help="Opaque position returned by a prior result read (incremental mode)",
    )
    result_p.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Maximum projected work items (server default: 20, maximum: 100)",
    )
    result_p.add_argument(
        "--max-text-chars",
        type=int,
        default=None,
        help="Maximum caller-content characters (server default: 6000)",
    )
    result_p.add_argument(
        "--expand",
        metavar="REF",
        help="Resolve an opaque event or turn detail reference",
    )
    result_p.add_argument("--json", action="store_true", help="Emit JSON")
    result_p.set_defaults(func=_cmd_result)

    stop_p = sub.add_parser("stop", help="Stop a session")
    stop_p.add_argument("session_id", help="Session ID")
    stop_p.add_argument(
        "--force", action="store_true",
        help="Tear down even with active background sub-agent tasks (kills "
             "them). Prefer waiting for them to finish.",
    )
    stop_p.set_defaults(func=_cmd_stop)

    end_p = sub.add_parser("end", help="End (delete) a session")
    end_p.add_argument("session_id", help="Session ID")
    end_p.add_argument(
        "--force", action="store_true",
        help="Tear down even with active background sub-agent tasks (kills "
             "them). Prefer waiting for them to finish.",
    )
    end_p.set_defaults(func=_cmd_end)

    resume_p = sub.add_parser(
        "resume",
        help="Resume a stopped session, or load/take-over a worktree by handle",
    )
    resume_p.add_argument(
        "session_id",
        metavar="target",
        help="Session ID (owned ACP session) or worktree handle to load",
    )
    resume_p.add_argument(
        "--force",
        "--reclaim",
        dest="force",
        action="store_true",
        help=(
            "Break-glass take-over: adopt the worktree even if a live "
            "interactive CLI holds it (stop that CLI first)"
        ),
    )
    resume_p.set_defaults(func=_cmd_resume)

    handoff_p = sub.add_parser(
        "handoff",
        help="Retire a session/worktree and continue in a fresh successor "
             "in place",
    )
    handoff_p.add_argument(
        "session_id",
        metavar="target",
        help="Session ID (owned ACP session) or worktree handle to hand off",
    )
    handoff_p.add_argument(
        "--reason",
        default=None,
        help="Free-form reason carried on the session_handoff event "
             "(default: context-pressure)",
    )
    handoff_p.add_argument(
        "--no-seed",
        action="store_true",
        help="Do not seed the successor's opening turn with the brief "
             "(the caller drives it instead)",
    )
    handoff_p.set_defaults(func=_cmd_handoff)

    usage_p = sub.add_parser(
        "session-usage", help="Show context window usage for a session"
    )
    usage_p.add_argument("session_id", help="Session ID")
    usage_p.set_defaults(func=_cmd_session_usage)

    answer_p = sub.add_parser(
        "answer",
        help="Answer a dispatched agent's parked ask_user question "
             "(the elicitation backstop) so its turn continues",
    )
    answer_p.add_argument("session_id", help="Session ID")
    answer_p.add_argument(
        "--field", dest="fields", action="append", default=[], metavar="KEY=VALUE",
        help="A form field answer (repeatable). Values are strings; use --json "
             "for numbers/booleans/complex values.",
    )
    answer_p.add_argument(
        "--json", dest="content_json", default=None,
        help="Full answer content as a JSON object (overrides --field).",
    )
    answer_p.add_argument(
        "--tool-call-id", dest="tool_call_id", default=None,
        help="Which parked question to answer (defaults to the sole pending one; "
             "required when more than one is outstanding -- see `status`).",
    )
    answer_p.add_argument(
        "--decline", action="store_true",
        help="Decline the question instead of submitting an answer.",
    )
    answer_p.add_argument(
        "--cancel", action="store_true",
        help="Cancel the question instead of submitting an answer.",
    )
    answer_p.set_defaults(func=_cmd_answer)

    # -- Agent mode --

    agent_p = sub.add_parser(
        "agent", help="Run as an ACP agent on stdio",
    )
    agent_p.add_argument(
        "--agent", required=True,
        help="Name of the downstream agent to route to",
    )
    agent_p.set_defaults(func=_cmd_agent)

    # -- Config commands --

    config_p = sub.add_parser(
        "config", help="Manage configuration and topology profiles",
    )
    config_sub = config_p.add_subparsers(dest="config_command")

    config_show_p = config_sub.add_parser("show", help="Show current config")
    config_show_p.set_defaults(func=_cmd_config_show)

    config_adopt_p = config_sub.add_parser(
        "adopt", help="Add/update a topology profile for a repo",
    )
    config_adopt_p.add_argument(
        "--repo", required=True,
        help="Path to the repo root (containing machines.yaml)",
    )
    config_adopt_p.add_argument(
        "--profile", required=True,
        help="Topology profile name (e.g. 'multi-machine system', 'my-control-harness')",
    )
    config_adopt_p.add_argument(
        "--machines-yaml",
        help="Explicit path to machines.yaml (auto-discovered if omitted)",
    )
    config_adopt_p.add_argument(
        "--agents-config",
        help="(Deprecated) Explicit path to an acp-agents.json override. The "
             "roster is derived from machines.yaml; this is no longer auto-discovered.",
    )
    config_adopt_p.set_defaults(func=_cmd_config_adopt)

    config_remove_p = config_sub.add_parser(
        "remove", help="Remove a topology profile",
    )
    config_remove_p.add_argument("profile", help="Profile name to remove")
    config_remove_p.set_defaults(func=_cmd_config_remove)

    config_validate_p = config_sub.add_parser(
        "validate", help="Validate current configuration",
    )
    config_validate_p.set_defaults(func=_cmd_config_validate)

    config_migrate_p = config_sub.add_parser(
        "migrate", help="Migrate machine-local config.yaml schema (idempotent)",
    )
    config_migrate_p.set_defaults(func=_cmd_config_migrate)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    # A top-level --project (e.g. injected by the `<repo> <slug>` router) pins
    # the target project for the project-addressed verbs; see _sender_repo().
    _set_project_override(getattr(args, "project", None))
    # Bounce an *explicit* --project on a verb that won't use it (#1080), instead
    # of silently ignoring it. Router-injected --project stays a silent no-op.
    _guard_project_scope(parser, args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if hasattr(args, "func"):
        from .client import BridgeConnectionError

        try:
            args.func(args)
        except BridgeConnectionError as exc:
            # The resilient client exhausted its bounded restart grace. Keep
            # one-shot command framing consistent; streaming commands
            # reconnect from the caller's acknowledged cursor internally.
            _exit_bridge_outage(exc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
