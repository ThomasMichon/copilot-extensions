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
import sys
import urllib.error
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import __version__

if TYPE_CHECKING:
    from .client import BridgeClientError


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _json_out(data: Any) -> None:
    """Print JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


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


def _get_client():
    """Build a BridgeClient from config. Exits on failure."""
    from .client import BridgeClient
    return BridgeClient.from_config()


def _add_stream_args(p: argparse.ArgumentParser) -> None:
    """Add the streaming/collapse flags shared by send / wait / read."""
    p.add_argument(
        "--caller", metavar="ID",
        help="Caller identity keying the delivery cursor (defaults to "
             "$WORKTREE_ID, else a shared per-session cursor)",
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
        port = elevated.ELEVATED_PORT
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


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the agent-bridge server."""
    import os

    import uvicorn

    from .config import (
        config_dir,
        load_config,
        load_or_create_auth_token,
        write_default_config,
    )

    cfg = load_config()
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
    # A normal start self-publishes the routing table once it is listening so
    # CLI clients discover it; a passive instance stays silent until the deploy
    # orchestrator flips the table after a health check.
    app.state.publish_on_ready = not passive

    print(f"[agent-bridge] Starting on {cfg.bind}:{cfg.port}")
    print(f"[agent-bridge] Auth token: {token[:8]}...")
    print(f"[agent-bridge] DB: {cfg.db_path}")
    if cfg.idle_shutdown_seconds and cfg.idle_shutdown_seconds > 0:
        print(f"[agent-bridge] Idle shutdown after {cfg.idle_shutdown_seconds}s")

    # Use an explicit Server (not uvicorn.run) so the idle-shutdown monitor in
    # the lifespan can request a graceful stop via server.should_exit.
    config = uvicorn.Config(
        app,
        host=cfg.bind,
        port=cfg.port,
        log_level=cfg.log_level,
        # Pure-Python WebSocket protocol (wsproto) for the ACP-over-WS
        # transport. Explicit so we never silently fall back to "none" (which
        # would 403 every /acp WebSocket upgrade) on a host without it.
        ws="wsproto",
    )
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    try:
        server.run()
    finally:
        singleton.release()


def _cmd_status(args: argparse.Namespace) -> None:
    """Check if agent-bridge is running, or show a session's compact status.

    With a ``session_id`` argument, render that dispatch's one-screen status
    (state, in-flight tool + elapsed, cursor lag) instead of the service health
    check (#46.1).
    """
    if getattr(args, "session_id", None):
        _cmd_session_status(args)
        return
    client = _get_client()
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


def _cmd_session_status(args: argparse.Namespace) -> None:
    """Render a single session's compact, low-context dispatch status."""
    from .client import BridgeClientError

    client = _get_client()
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

    if args.json:
        _json_out(st)
        return

    print(f"  {sid}  ({st.get('name', '')})  [{st.get('status', '')}]")
    print(f"    Agent:   {st.get('agent_name') or '(none)'}")
    if st.get("caller_id"):
        print(f"    Caller:  {st['caller_id']}")
    print(
        f"    Turns:   {st.get('turn_count', 0)}"
        f"    Updated: {_short_dt(st.get('updated_at'))}"
    )
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

    # Last K collapsed steps (cursor-neutral tail read; --steps 0 disables).
    k = getattr(args, "steps", 0) or 0
    if k > 0 and head:
        events = client.read_range(sid, start=max(1, head - k + 1), end=head)
        out = _make_renderer(args).render_events(events)
        if out and out.strip():
            print("    Recent:")
            for line in out.rstrip().splitlines():
                print(f"      {line}")


def _cmd_version(_args: argparse.Namespace) -> None:
    print(f"agent-bridge {__version__}")


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


def _service_port() -> int:
    """Resolved bridge port from config, else platform default."""
    from .models import default_port

    cfg_path = os.path.join(_INSTALL_DIR, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            import yaml

            data = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
            return int(data.get("port", default_port()))
        except Exception:
            pass
    return default_port()


def _service_is_running() -> bool:
    """Quiet health probe -- direct GET, no client error spam."""
    import urllib.request

    url = f"http://127.0.0.1:{_service_port()}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _read_pid_file() -> int | None:
    try:
        with open(_PID_FILE, encoding="utf-8") as fh:
            return int((fh.read() or "").strip())
    except (OSError, ValueError):
        return None


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


def _systemd_available() -> bool:
    import shutil

    unit = os.path.expanduser(f"~/.config/systemd/user/{_SYSTEMD_UNIT}")
    return (
        sys.platform != "win32"
        and shutil.which("systemctl") is not None
        and os.path.exists(unit)
    )


def _win_task_exists() -> bool:
    import subprocess as sp

    try:
        out = sp.run(
            ["schtasks", "/Query", "/TN", _WIN_TASK_NAME],
            capture_output=True, text=True, timeout=15,
        )
        return out.returncode == 0
    except (OSError, sp.TimeoutExpired):
        return False


def _service_start() -> None:
    import subprocess as sp

    if _service_is_running():
        print(f"[OK] agent-bridge already running (port {_service_port()})")
        return

    if _systemd_available():
        sp.run(["systemctl", "--user", "start", _SYSTEMD_UNIT])
    elif sys.platform == "win32" and _win_task_exists():
        sp.run(["schtasks", "/Run", "/TN", _WIN_TASK_NAME],
               capture_output=True, text=True)
    else:
        # Fallback (no systemd unit / scheduled task): spawn the foreground
        # `agent-bridge start` as a detached background process.
        import subprocess as _sp

        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            flags = 0x00000008 | 0x00000200
            popen_kwargs: dict[str, Any] = {"creationflags": flags}
        else:
            popen_kwargs = {"start_new_session": True}

        logf = open(os.path.join(_INSTALL_DIR, "agent-bridge.log"), "ab")
        errf = open(os.path.join(_INSTALL_DIR, "agent-bridge-err.log"), "ab")
        _sp.Popen(
            ["agent-bridge", "start"],
            stdout=logf, stderr=errf, stdin=_sp.DEVNULL, **popen_kwargs,
        )

    # Wait for health
    import time

    for _ in range(15):
        time.sleep(1)
        if _service_is_running():
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
    # terminate the process by pid file / port binding.
    pid = _read_pid_file() or _pid_on_port(_service_port())
    if pid:
        _kill_pid(pid)
        stopped_any = True
        try:
            os.remove(_PID_FILE)
        except OSError:
            pass

    if not stopped_any:
        print("[SKIP] agent-bridge does not appear to be running")
        return

    # Confirm the port is released (TimeWait can linger briefly).
    for _ in range(10):
        if not _service_is_running():
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
        pid = _read_pid_file() or _pid_on_port(_service_port())
        if pid:
            print(f"  PID:  {pid}")
        print(f"  Port: {_service_port()}")
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
    agents = client.list_agents()
    if args.json:
        _json_out(agents)
        return
    if not agents:
        print("(no agents registered)")
        return
    for i, a in enumerate(agents):
        name = a.get("name", "")
        display = a.get("display_name", "")
        target_type = a.get("target_type", "")
        host = a.get("host", "")
        managed = a.get("managed", False)
        # Use display name as heading when available, otherwise raw name
        heading = display or name
        print(heading)
        # Show raw name when it differs from display (e.g. codespace agents)
        if display and name != display:
            print(f"  Name:     {name}")
        if target_type:
            print(f"  Type:     {target_type}")
        if host:
            print(f"  Host:     {host}")
        if managed:
            print(f"  Managed:  {managed}")
        if i < len(agents) - 1:
            print()


def _cmd_machines(args: argparse.Namespace) -> None:
    """List topology machines."""
    from .client import BridgeClientError

    client = _get_client()
    try:
        machines = client.list_machines()
    except BridgeClientError as exc:
        if exc.status == 404:
            print("[>] Machines endpoint not available (service may need restart)")
            return
        raise
    if args.json:
        _json_out(machines)
        return
    _table(machines, [
        ("key", "MACHINE", 20),
        ("display_name", "NAME", 24),
        ("environment", "ENV", 16),
        ("role", "ROLE", 30),
        ("ssh_ready", "SSH", 5),
    ])


def _cmd_drain(args: argparse.Namespace) -> None:
    """Stop accepting new work and wait for in-flight sessions to settle.

    The zero-downtime pre-swap step: refuses new sessions/turns, then blocks
    until no session is streaming a turn or hosting background sub-agents.
    Exit 0 when fully drained, 2 on timeout (unless --force)."""
    from .client import BridgeClientError, BridgeConnectionError

    client = _get_client()
    try:
        res = client.drain(
            timeout=args.timeout, poll=args.poll, force=args.force
        )
    except (BridgeClientError, BridgeConnectionError) as exc:
        detail = getattr(exc, "detail", str(exc))
        print(f"[FAIL] {detail}", file=sys.stderr)
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
    from .client import BridgeClientError, BridgeConnectionError

    client = _get_client()
    try:
        client.undrain()
    except (BridgeClientError, BridgeConnectionError) as exc:
        detail = getattr(exc, "detail", str(exc))
        print(f"[FAIL] {detail}", file=sys.stderr)
        sys.exit(1)
    print("Drain gate released; accepting new work.")


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
        # passive instance, detached so it outlives this deploy process.
        cmd = [sys.executable, "-m", "agent_bridge", "start",
               "--port", str(port), "--passive"]
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            )
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(cmd, **kwargs)

    def health_check(h: str, port: int) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{h}:{port}/health", timeout=2
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def make_client(base_url: str) -> BridgeClient:
        return BridgeClient(base_url, token,
                            timeout=int(args.drain_timeout) + 60)

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
    from .client import BridgeClientError, BridgeConnectionError

    client = _get_client()
    try:
        res = client.gc()
    except (BridgeClientError, BridgeConnectionError) as exc:
        detail = getattr(exc, "detail", str(exc))
        print(f"[FAIL] {detail}", file=sys.stderr)
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
        if caller:
            print(f"    Caller:  {caller}")
        if context:
            print(f"    Context: {context}")
        print(f"    Turns:   {turns}    Updated: {updated}")


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
    prompt = args.prompt
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


def _submit_and_stream(
    client,
    args: argparse.Namespace,
    session_id: str,
    prompt: str,
    *,
    caller_id: str | None,
) -> None:
    """Submit *prompt* to *session_id* and stream the turn (shared by send/create)."""
    result = client.submit_prompt(session_id, prompt)
    turn_index = result.get("turn_index", 0)

    if args.json:
        _json_out({"session_id": session_id, **result})
        return

    print(f"[>] Session {session_id} -- turn {turn_index}")

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
                client.resume_session(target)
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
    for a in agents:
        name = a.get("name", "")
        if not name:
            continue
        forms = {name, *(a.get("aliases") or [])}
        if target in forms:
            if name not in matches:
                matches.append(name)
            continue
        if bare:
            # Modifier namespaces (e.g. admin:) mirror an existing agent's base
            # name to wrap it; they are opt-in and must not match a bare name,
            # or every local agent collides with its own elevated twin.
            if a.get("bare_addressable", True) is False:
                continue
            bare_forms = {f.split(":", 1)[1] for f in forms if ":" in f}
            if target in bare_forms and name not in matches:
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

    prompt = getattr(args, "prompt", None)
    if not prompt:
        if args.json:
            _json_out({"session_id": session_id})
        else:
            print(
                f"[OK] Session {session_id} created -- send work with: "
                f"agent-bridge send {session_id} \"<prompt>\""
            )
        return

    _submit_and_stream(client, args, session_id, prompt, caller_id=caller_id)


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


def _get_caller_id() -> str | None:
    """Read caller identity from the environment.

    Uses WORKTREE_ID (set by agent-worktrees) so that each worktree
    gets its own session affinity with remote agents.  Falls back to
    None if not running inside a worktree session.
    """
    return os.environ.get("WORKTREE_ID")


def _caller_id_for(args: argparse.Namespace) -> str | None:
    """Resolve the caller identity used to key the delivery cursor.

    Precedence: explicit ``--caller`` > ``WORKTREE_ID`` env > None. A None
    caller falls back to the session's shared default cursor server-side.
    Because ``WORKTREE_ID`` is not always trustworthy across sessions,
    ``--caller`` lets a host pin a stable cursor key.
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
            client.resume_session(sid)
        except Exception:
            pass  # submit_prompt will surface a hard failure if it persists
    print(
        f"[>] Reusing session {sid} ({name}) for '{agent_name}' "
        f"({turns} prior turn(s))",
    )
    return sid


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
            # Concurrent-dispatch guard (#21): never pile a second prompt onto a
            # session that is mid-turn -- the bridge would reject it (or, worse,
            # the caller would block on an idle-wait timeout). Fail fast with an
            # actionable wait-vs-take-over message, or honor --force by ending
            # the in-flight turn and starting fresh.
            if existing.get("status", "") == "running":
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
            agent=agent_name, caller_id=caller_id, force_new=force_new,
        )
    except BridgeClientError as exc:
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
            print(f"[FAIL] Session {session_id} entered {status}", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)
    print(f"[FAIL] Timed out waiting for session {session_id} to become idle", file=sys.stderr)
    sys.exit(1)


def _stream_feed(
    client,
    session_id: str,
    *,
    caller_id: str | None,
    renderer,
    command_timeout: float = 0.0,
) -> str:
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

    def _ack(up_to: int) -> None:
        # Best-effort: a failed ack just means a future read re-delivers
        # (no data loss), never a skip.
        try:
            client.ack_cursor(session_id, up_to, caller_id=caller_id)
        except Exception:
            pass

    for _attempt in range(max_attempts):
        try:
            for evt in client.stream_events(
                session_id, after=cursor, caller_id=caller_id
            ):
                now = time.monotonic()
                etype = evt.get("event", "")

                if etype == "_heartbeat":
                    if now - last_activity >= _PROGRESS_INTERVAL:
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
                    if now - last_activity >= _PROGRESS_INTERVAL:
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
            pass  # service unreachable (restarting?) -- back off and resume
        except (OSError, urllib.error.URLError):
            pass
        except BridgeClientError as exc:
            if exc.status == 404:
                print(f"\n[FAIL] Session {session_id} not found", file=sys.stderr)
                return "error"
            # transient -- retry

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


def _cmd_stop(args: argparse.Namespace) -> None:
    """Stop a session."""
    client = _get_client()
    client.stop_session(args.session_id)
    print(f"[OK] Session {args.session_id} stopped")


def _cmd_end(args: argparse.Namespace) -> None:
    """End (delete) a session.

    Idempotent + quiet (#48): ending an already-ended/absent session is a
    no-op success, and any error prints a one-line message -- never a raw
    client traceback.
    """
    from .client import BridgeClientError, BridgeConnectionError

    client = _get_client()
    try:
        client.end_session(args.session_id)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[OK] Session {args.session_id} already ended")
            return
        print(f"[FAIL] Could not end session {args.session_id}: {exc.detail}")
        sys.exit(1)
    except BridgeConnectionError:
        print(f"[FAIL] agent-bridge is not reachable; could not end session {args.session_id}")
        sys.exit(1)
    print(f"[OK] Session {args.session_id} ended")


def _cmd_resume(args: argparse.Namespace) -> None:
    """Resume a stopped session."""
    client = _get_client()
    result = client.resume_session(args.session_id)
    status = result.get("status", "")
    print(f"[OK] Session {args.session_id} resumed ({status})")


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


def _cmd_agent(args: argparse.Namespace) -> None:
    """Run agent-bridge as an upstream ACP agent on stdio."""
    import asyncio
    from pathlib import Path

    from .acp_agent import BridgeAgent
    from .agent_registry import build_resolver
    from .config import load_config
    from .db import Database
    from .session_manager import SessionManager

    log = logging.getLogger("agent-bridge")

    cfg = load_config()

    # Initialize DB and session manager
    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    sm = SessionManager(db, context_thresholds=cfg.context_thresholds)

    # Load topology/resolver (includes auto-discovered local agents)
    resolver = build_resolver(cfg)

    agent_name = getattr(args, "agent", None)
    if not agent_name:
        print("[FAIL] --agent is required for agent mode", file=sys.stderr)
        sys.exit(1)

    # Validate agent exists
    if resolver and agent_name not in resolver.agents:
        available = list(resolver.agents.keys())
        print(
            f"[FAIL] Agent '{agent_name}' not found. Available: {available}",
            file=sys.stderr,
        )
        sys.exit(1)

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
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
    agents_p.set_defaults(func=_cmd_agents)

    machines_p = sub.add_parser("machines", help="List topology machines")
    machines_p.set_defaults(func=_cmd_machines)

    sessions_p = sub.add_parser("sessions", help="List sessions")
    sessions_p.add_argument("--status", help="Filter by status")
    sessions_p.set_defaults(func=_cmd_sessions)

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
        help="Zero-downtime active/passive cutover: stand up the new daemon on "
             "a fresh port, flip the routing table, drain + retire the old one",
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
    deploy_p.add_argument("--json", action="store_true", help="Emit JSON.")
    deploy_p.set_defaults(func=_cmd_deploy)

    send_p = sub.add_parser(
        "send", help="Send a prompt to an agent or session (reuses/resumes "
        "this caller's existing session)"
    )
    send_p.add_argument("target", help="Agent name or session ID")
    send_p.add_argument("prompt", help="Prompt text to send")
    send_p.add_argument(
        "--no-wait", action="store_true",
        help="Return immediately without waiting for response",
    )
    send_p.add_argument(
        "--new", action="store_true",
        help="(removed) use `agent-bridge create` for a fresh session",
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
        "--no-wait", action="store_true",
        help="Return immediately without waiting for response",
    )
    _add_stream_args(create_p)
    create_p.set_defaults(func=_cmd_create)

    wait_p = sub.add_parser(
        "wait", help="Wait for current turn to complete"
    )
    wait_p.add_argument("session_id", help="Session ID")
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

    stop_p = sub.add_parser("stop", help="Stop a session")
    stop_p.add_argument("session_id", help="Session ID")
    stop_p.set_defaults(func=_cmd_stop)

    end_p = sub.add_parser("end", help="End (delete) a session")
    end_p.add_argument("session_id", help="Session ID")
    end_p.set_defaults(func=_cmd_end)

    resume_p = sub.add_parser("resume", help="Resume a stopped session")
    resume_p.add_argument("session_id", help="Session ID")
    resume_p.set_defaults(func=_cmd_resume)

    usage_p = sub.add_parser(
        "session-usage", help="Show context window usage for a session"
    )
    usage_p.add_argument("session_id", help="Session ID")
    usage_p.set_defaults(func=_cmd_session_usage)

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
        help="Topology profile name (e.g. 'facility', 'my-control-harness')",
    )
    config_adopt_p.add_argument(
        "--machines-yaml",
        help="Explicit path to machines.yaml (auto-discovered if omitted)",
    )
    config_adopt_p.add_argument(
        "--agents-config",
        help="Explicit path to acp-agents.json (auto-discovered if omitted)",
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

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if hasattr(args, "func"):
        from .client import BridgeConnectionError

        try:
            args.func(args)
        except BridgeConnectionError as exc:
            # The service is unreachable (e.g. mid-restart) and this command
            # could not ride it out. Surface a clean one-line message instead of
            # a traceback. Streaming commands (send/read/wait) handle this
            # internally by reconnecting from the caller's acked cursor.
            print(
                f"[FAIL] {exc}\n"
                "       Is it running? Start it with: agent-bridge service start",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
