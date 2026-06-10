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
from datetime import datetime, timezone
from typing import Any

from . import __version__


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


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the agent-bridge server."""
    import uvicorn

    from .config import load_config, load_or_create_auth_token, write_default_config

    cfg = load_config()
    write_default_config(cfg)
    token = load_or_create_auth_token()

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


def _cmd_version(_args: argparse.Namespace) -> None:
    print(f"agent-bridge {__version__}")


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

    If *target* matches a registered agent name, starts a new session
    and sends the prompt. If it matches an existing session ID, sends
    to that session. Otherwise errors.
    """
    client = _get_client()
    target = args.target
    prompt = args.prompt

    # Resolve: try agent name first, then session ID
    force_new = getattr(args, "new", False)
    session_id = _resolve_target(client, target, force_new=force_new)

    # Get current session state to know where events start
    session_info = client.get_session(session_id)
    # We'll skip events from before the prompt by tracking turn_index
    pre_turn_count = session_info.get("turn_count", 0)

    # Submit prompt
    result = client.submit_prompt(session_id, prompt)
    turn_index = result.get("turn_index", 0)

    if args.json:
        _json_out({"session_id": session_id, **result})
        return

    print(f"[>] Session {session_id} -- turn {turn_index}")

    if args.no_wait:
        print("[>] Prompt submitted (--no-wait)")
        return

    # Stream SSE events until turn completes
    _stream_until_complete(client, session_id, turn_index)


def _resolve_target(client, target: str, *, force_new: bool = False) -> str:
    """Resolve a target string to a session ID.

    Resolution order:
    1. Existing session ID (exact match)
    2. Registered agent name (exact match, e.g. ``codespace:my-cs``)
    3. Namespace-prefixed fallback -- if *target* has no ``:`` and no
       exact agent match, try ``<prefix>:<target>`` for each registered
       namespace resolver.  This lets users type bare codespace names
       instead of ``codespace:<name>``.
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
                print(
                    f"[FAIL] Session {target} is {status} -- cannot send prompt",
                    file=sys.stderr,
                )
                sys.exit(1)
    except BridgeClientError as exc:
        if exc.status != 404:
            raise

    # Try as agent name -- start a new session
    try:
        agents = client.list_agents()
        agent_names = [a.get("name", "") for a in agents]
        if target in agent_names:
            return _start_agent_session(client, target, force_new=force_new)
    except BridgeClientError:
        pass

    # Namespace fallback: if target has no ":" prefix, try each
    # registered namespace (e.g. "codespace:<target>").
    if ":" not in target:
        try:
            agents = client.list_agents()
            agent_names = [a.get("name", "") for a in agents]
            # Collect unique namespace prefixes from agent names
            prefixes = sorted({
                n.split(":")[0]
                for n in agent_names
                if ":" in n
            })
            for prefix in prefixes:
                candidate = f"{prefix}:{target}"
                if candidate in agent_names:
                    print(
                        f"[>] Resolved '{target}' as '{candidate}'",
                    )
                    return _start_agent_session(client, candidate, force_new=force_new)
            # No exact match even with prefix -- try start_session
            # directly and let the server's resolve_async try namespace
            # resolvers (which may do on-demand lookup).
            for prefix in prefixes:
                candidate = f"{prefix}:{target}"
                try:
                    print(
                        f"[>] Trying '{candidate}'...",
                    )
                    return _start_agent_session(client, candidate, force_new=force_new)
                except (BridgeClientError, SystemExit):
                    continue
        except BridgeClientError:
            pass

    print(
        f"[FAIL] '{target}' is not a known agent name or session ID",
        file=sys.stderr,
    )
    sys.exit(1)


def _get_caller_id() -> str | None:
    """Read caller identity from the environment.

    Uses WORKTREE_ID (set by agent-worktrees) so that each worktree
    gets its own session affinity with remote agents.  Falls back to
    None if not running inside a worktree session.
    """
    return os.environ.get("WORKTREE_ID")


def _start_agent_session(client, agent_name: str, *, force_new: bool = False) -> str:
    """Start or reuse a session for a named agent.

    Checks for an existing idle session with matching (agent_name,
    caller_id) first.  If found, reuses it instead of creating a new
    one (avoids session pollution).  Different worktrees calling the
    same agent get separate sessions because their caller_id differs.

    Pass ``force_new=True`` (``--new`` flag) to skip reuse and always
    create a fresh session.
    """
    from .client import BridgeClientError

    caller_id = _get_caller_id()

    if not force_new:
        try:
            sessions = client.list_sessions(status="idle")
            for s in sessions:
                if (
                    s.get("agent_name") == agent_name
                    and s.get("caller_id") == caller_id
                ):
                    sid = s.get("session_id", "")
                    name = s.get("name", "")
                    turns = s.get("turn_count", 0)
                    print(
                        f"[>] Reusing session {sid} ({name}) "
                        f"for '{agent_name}' ({turns} prior turn(s))",
                    )
                    return sid
        except Exception:
            pass  # Fall through to create new

    print(f"[>] Starting session for agent '{agent_name}'...")
    try:
        resp = client.start_session(agent=agent_name, caller_id=caller_id)
    except BridgeClientError as exc:
        # Server-side concurrency guard: this agent (e.g. a CodeSpace)
        # already has an active session. Reuse it instead of failing.
        existing_sid = _conflict_session_id(exc)
        if existing_sid:
            print(
                f"[>] Agent '{agent_name}' already has an active session "
                f"{existing_sid} -- reusing it",
            )
            return existing_sid
        raise
    sid = resp.get("session_id", "")
    name = resp.get("name", "")
    print(f"[>] Session {sid} ({name}) created")
    _wait_for_idle(client, sid)
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


def _stream_until_complete(
    client, session_id: str, turn_index: int
) -> None:
    """Stream SSE events, printing output until the turn completes.

    Maintains a cursor (last seen event ID) and reconnects
    automatically on timeout or disconnect.  Only fetches events
    after the cursor, so output is never duplicated.
    """
    from .client import BridgeClientError

    cursor = 0
    in_our_turn = turn_index == -1  # -1 means "any running turn" (wait)
    max_retries = 120  # ~1 hour at 30s heartbeat interval

    for attempt in range(max_retries):
        try:
            for evt in client.stream_events(session_id, after=cursor):
                # Advance cursor so reconnects resume from here
                evt_id = evt.get("id", "")
                if evt_id:
                    try:
                        cursor = int(evt_id)
                    except (ValueError, TypeError):
                        pass

                event_type = evt.get("event", "")
                data = evt.get("data", {})

                # Wait for our turn to start before printing anything
                if event_type == "session_state_changed":
                    status = data.get("status", "")
                    ti = data.get("turn_index")
                    if status == "running" and (
                        turn_index == -1 or ti == turn_index
                    ):
                        in_our_turn = True
                    continue

                if not in_our_turn:
                    continue

                if event_type == "agent_message":
                    text = data.get("text", "")
                    if text:
                        print(text, end="", flush=True)

                elif event_type == "agent_thought":
                    text = data.get("text", "")
                    if text:
                        print(f"\033[2m{text}\033[0m", end="", flush=True)

                elif event_type == "tool_call_start":
                    title = data.get("title", "")
                    if title:
                        print(f"\n  >> {title}", flush=True)

                elif event_type == "tool_call_update":
                    status = data.get("status", "")
                    if status and status not in ("pending", "running"):
                        print(f"     [{status}]", flush=True)

                elif event_type == "turn_complete":
                    print()  # Final newline
                    stop = data.get("stop_reason", "")
                    if stop:
                        print(f"[<] Turn complete ({stop})")
                    else:
                        print("[<] Turn complete")
                    return

                elif event_type == "error":
                    msg = data.get("message", "Unknown error")
                    print(f"\n[FAIL] {msg}", file=sys.stderr)
                    return

        except (OSError, urllib.error.URLError):
            # Connection dropped -- reconnect from cursor
            pass
        except BridgeClientError as exc:
            if exc.status == 404:
                print(f"\n[FAIL] Session {session_id} not found", file=sys.stderr)
                return
            # Transient server error -- retry
            pass
        except KeyboardInterrupt:
            print("\n[>] Interrupted -- session still running")
            return

        # Check if session is still running before reconnecting
        try:
            session = client.get_session(session_id)
            status = session.get("status", "")
            if status in ("idle", "ended", "stopped", "failed"):
                # Drain remaining events from the last cursor
                try:
                    for evt in client.stream_events(session_id, after=cursor):
                        evt_id = evt.get("id", "")
                        if evt_id:
                            try:
                                cursor = int(evt_id)
                            except (ValueError, TypeError):
                                pass
                        event_type = evt.get("event", "")
                        data = evt.get("data", {})
                        if event_type == "turn_complete":
                            print()
                            stop = data.get("stop_reason", "")
                            if stop:
                                print(f"[<] Turn complete ({stop})")
                            else:
                                print("[<] Turn complete")
                            return
                except Exception:
                    pass
                print(f"\n[<] Session is {status}")
                return
        except Exception:
            pass  # Can't check -- try reconnecting anyway

        import time
        time.sleep(1)

    print("\n[FAIL] Gave up reconnecting after too many retries", file=sys.stderr)


def _cmd_wait(args: argparse.Namespace) -> None:
    """Wait for the current turn on a session to complete."""
    client = _get_client()
    session = client.get_session(args.session_id)
    status = session.get("status", "")

    if status == "idle":
        print(f"[OK] Session {args.session_id} is already idle")
        return
    if status not in ("running", "starting"):
        print(f"[>] Session {args.session_id} is {status}")
        return

    print(f"[>] Waiting for session {args.session_id}...")
    _stream_until_complete(client, args.session_id, turn_index=-1)


def _cmd_stop(args: argparse.Namespace) -> None:
    """Stop a session."""
    client = _get_client()
    client.stop_session(args.session_id)
    print(f"[OK] Session {args.session_id} stopped")


def _cmd_end(args: argparse.Namespace) -> None:
    """End (delete) a session."""
    client = _get_client()
    client.end_session(args.session_id)
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
    start_p.set_defaults(func=_cmd_start)

    status_p = sub.add_parser("status", help="Check if agent-bridge is running")
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

    # -- Client commands --

    agents_p = sub.add_parser("agents", help="List registered agents")
    agents_p.set_defaults(func=_cmd_agents)

    machines_p = sub.add_parser("machines", help="List topology machines")
    machines_p.set_defaults(func=_cmd_machines)

    sessions_p = sub.add_parser("sessions", help="List sessions")
    sessions_p.add_argument("--status", help="Filter by status")
    sessions_p.set_defaults(func=_cmd_sessions)

    send_p = sub.add_parser(
        "send", help="Send a prompt to an agent or session"
    )
    send_p.add_argument("target", help="Agent name or session ID")
    send_p.add_argument("prompt", help="Prompt text to send")
    send_p.add_argument(
        "--no-wait", action="store_true",
        help="Return immediately without waiting for response",
    )
    send_p.add_argument(
        "--new", action="store_true",
        help="Force a new session even if an idle one exists for this agent",
    )
    send_p.set_defaults(func=_cmd_send)

    wait_p = sub.add_parser(
        "wait", help="Wait for current turn to complete"
    )
    wait_p.add_argument("session_id", help="Session ID")
    wait_p.set_defaults(func=_cmd_wait)

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
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
