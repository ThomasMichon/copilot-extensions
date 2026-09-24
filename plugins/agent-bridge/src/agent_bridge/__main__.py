"""CLI entry point for agent-bridge.

Server commands:  start, status, version
Client commands:  agents, machines, sessions, session-usage, send, wait, stop, end, resume, handoff-request
Agent mode:       agent (run as ACP agent on stdio)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from agent_procutil import (
    no_window_kwargs as _no_window_kwargs,
    windowless_daemon_kwargs as _windowless_daemon_kwargs,
)

from . import _peer_launch as _peer_launch_module
from . import __version__
from .install_paths import (
    effective_config_dir,
    install_dir as _install_dir,
    scheduled_task_name,
    systemd_unit_name,
)

no_window_kwargs = _no_window_kwargs
windowless_daemon_kwargs = _windowless_daemon_kwargs
_peer_launch = _peer_launch_module
install_dir = _install_dir

if __name__ == "__main__":
    sys.modules.setdefault("agent_bridge.__main__", sys.modules[__name__])


def _json_out(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _exit_bridge_outage(exc: Exception) -> None:
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
    if not rows:
        print("(none)")
        return
    widths = []
    for key, hdr, min_w in columns:
        data_w = max((len(str(row.get(key, ""))) for row in rows), default=0)
        widths.append(max(min_w, len(hdr), data_w))
    header = "  ".join(h.ljust(w) for (_, h, _), w in zip(columns, widths))
    print(header)
    print("-" * len(header))
    for row in rows:
        print("  ".join(str(row.get(key, "")).ljust(width) for (key, _, _), width in zip(columns, widths)))


def _short_dt(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        return str(iso)[:19]


def _age_str(iso: str | None) -> str:
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
    from .client import BridgeClient, BridgeClientError, BridgeConnectionError

    if ensure:
        _ensure_daemon()
    client = BridgeClient.from_config()
    try:
        client.assert_client_supported()
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(3)
    except BridgeConnectionError:
        pass
    return client


def _add_stream_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--caller",
        metavar="ID",
        help="Caller identity keying the delivery cursor (defaults to the current worktree via `agent-worktrees get worktree-dir`, else a shared per-session cursor)",
    )
    p.add_argument(
        "--expand",
        action="append",
        choices=["thoughts", "tools", "all"],
        help="Expand collapsed content in the feed (repeatable). By default chain-of-thought and tool calls collapse to one-line markers.",
    )
    p.add_argument("--no-color", action="store_true", help="Disable ANSI color/dim in the rendered feed")


def _worktrees_get(key: str) -> str | None:
    exe = shutil.which("agent-worktrees")  # marketplace-isolation: allow agent-worktrees-management
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "get", key], capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    val = (r.stdout or "").strip()
    return val or None


_PROJECT_OVERRIDE: str | None = None
_PROJECT_ROUTED = False


def _set_project_override(project: str | None) -> None:
    global _PROJECT_OVERRIDE
    _PROJECT_OVERRIDE = project.strip() if project and project.strip() else None


_PROJECT_CONSUMING_VERBS = frozenset({"agents", "create", "machines", "send"})


def _guard_project_scope(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    global _PROJECT_ROUTED
    routed = os.environ.pop("AGENT_WORKTREES_PROJECT_ROUTED", None) == "1"
    _PROJECT_ROUTED = routed
    if getattr(args, "project", None) is None or routed:
        return
    command = getattr(args, "command", None)
    if command in _PROJECT_CONSUMING_VERBS:
        return
    parser.error(
        f"--project {args.project!r} is not meaningful for '{command or '(no command)'}': "
        f"it scopes only the project-addressed verbs ({'/'.join(sorted(_PROJECT_CONSUMING_VERBS))}). Remove --project, or use one of those verbs."
    )


def _get_caller_id() -> str | None:
    return _worktrees_get("worktree-dir")


def _sender_repo() -> str | None:
    if _PROJECT_OVERRIDE:
        return _PROJECT_OVERRIDE
    return _worktrees_get("project")


def _caller_id_for(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "caller", None)
    if explicit:
        return explicit
    return _get_caller_id()


def _phased_timeouts():
    from .models import PhasedTimeouts

    try:
        from .config import load_config

        return load_config().timeouts
    except Exception:
        return PhasedTimeouts()


def _startup_request_timeout(*, resume: bool = False, fresh_fallback: bool = False) -> float:
    from .session_manager import _MAX_RESUME_ROUNDS

    timeouts = _phased_timeouts()
    one_round = max(timeouts.codespace_boot, timeouts.ssh_connect, timeouts.session_host_ready) + timeouts.session_start + timeouts.session_new + 30.0
    rounds = _MAX_RESUME_ROUNDS if resume else 1
    if fresh_fallback:
        rounds += 1
    return one_round * rounds


def _make_renderer(args: argparse.Namespace):
    from .render import StreamRenderer

    expand = set(getattr(args, "expand", None) or [])
    color = sys.stdout.isatty() and not getattr(args, "no_color", False)
    return StreamRenderer(
        expand_thoughts=("thoughts" in expand or "all" in expand),
        expand_tools=("tools" in expand or "all" in expand),
        color=color,
    )


_INSTALL_DIR = os.fspath(effective_config_dir())
_PID_FILE = os.path.join(_INSTALL_DIR, "agent-bridge.pid")
_WIN_TASK_NAME = scheduled_task_name()
_SYSTEMD_UNIT = systemd_unit_name()
_SERVICE_START_TIMEOUT_S = 120
_SERVICE_LAUNCH_GRACE_S = 15
_ENSURE_BACKOFF_S = 30.0
_ENSURE_LOCK = os.path.join(_INSTALL_DIR, ".ensure.lock")
_ENSURE_MARKER = os.path.join(_INSTALL_DIR, ".ensure-attempt")
_PROGRESS_INTERVAL = 20.0
_RECONNECT_BACKOFF = 1.0
_STREAM_404_GRACE_S = 30.0
_SEND_BUSY_EXIT = 75
_REUSABLE_SESSION_STATES = ("created", "starting", "running", "idle", "stopped")
_COMING_UP_STATES = ("created", "starting")
_COMING_UP_SETTLE_TIMEOUT = 180.0
_COMING_UP_POLL_INTERVAL = 2.0


from .config_cli import (  # noqa: F401
    _cmd_config_adopt,
    _cmd_config_migrate,
    _cmd_config_remove,
    _cmd_config_show,
    _cmd_config_validate,
    _cmd_doctor,
    _print_registry_findings,
    register_config_commands,
)
from .inventory_cli import (  # noqa: F401
    _cmd_agent_show,
    _cmd_agents,
    _cmd_live_sessions,
    _cmd_machines,
    _launch_cli_mode_session,
    _listing_project,
    _live_session_summary_line,
    _report_topology_errors,
    register_inventory_commands,
)
from .service_process_cli import (  # noqa: F401
    _BrokeredProcessHandle,
    _acquire_ensure_lock,
    _cmd_service,
    _daemon_launch_argv,
    _ensure_daemon,
    _ensure_retired_daemon_exited,
    _force_kill_agent_bridge_tree,
    _kill_pid,
    _pid_from_lock,
    _pid_is_agent_bridge,
    _pid_on_port,
    _print_reconcile_status,
    _reap_abandoned_passive,
    _reconcile_service_marker,
    _release_ensure_lock,
    _service_start,
    _service_stop,
    _spawn_detached_argv,
    _spawn_detached_daemon,
    _spawn_via_wmi_broker,
    _spawn_via_wmi_broker_pid,
    _spawn_watchdog_replacement,
    _systemd_available,
    _wait_for_ensure_owner,
    _watchdog_dead,
    _win_task_exists,
    register_service_control_commands,
)
from .service_process_state import (  # noqa: F401
    _active_endpoint,
    _active_endpoint_port,
    _listening_ports_for_pid,
    _matching_routed_version,
    _read_pid_file,
    _reconcile_live_dynamic_daemon,
    _same_endpoint,
    _service_health_on_port,
    _service_is_running,
    _service_pid,
    _service_port,
    _service_process_is_live,
    _undrain_service_at,
    _wait_for_service_start,
)
from .service_start_cli import (  # noqa: F401
    _ask_user_fields,
    _bind_listen_socket,
    _cmd_acp_connect,
    _cmd_carrier,
    _cmd_elevated,
    _cmd_installer_readiness,
    _cmd_remote,
    _cmd_session_host_agent,
    _cmd_session_status,
    _cmd_start,
    _cmd_status,
    _cmd_token,
    _cmd_version,
    _dynamic_bind_requested,
    register_service_start_commands,
)
from .session_lifecycle_cli import (  # noqa: F401
    _agent_bridge_owner_root,
    _agent_worktrees_launch_prefix,
    _cmd_agent,
    _cmd_answer,
    _cmd_end,
    _cmd_handoff,
    _cmd_handoff_check,
    _cmd_handoff_request,
    _cmd_resume,
    _cmd_stop,
    register_session_lifecycle_commands,
)
from .session_maintenance_cli import (  # noqa: F401
    _cmd_drain,
    _cmd_gc,
    _cmd_peek,
    _cmd_session_usage,
    _cmd_sessions,
    _cmd_undrain,
    _peek_iso,
    register_session_maintenance_commands,
)
from .session_start_cli import (  # noqa: F401
    _await_coming_up_settled,
    _conflict_session_id,
    _find_caller_session,
    _render_head_guard_refusal,
    _report_unavailable_read_target,
    _resolve_read_target,
    _resolve_read_worktree_session,
    _reuse_existing,
    _session_failure_detail,
    _session_status_name,
    _start_agent_session,
    _wait_for_idle,
    _wait_for_worktree_read_target,
)
from .session_streaming_cli import (  # noqa: F401
    _cmd_attention_wait,
    _cmd_read,
    _cmd_result,
    _cmd_wait,
    _contract_changed_result,
    _render_attention_result,
    _stream_feed,
    _turn_settled,
    register_session_streaming_commands,
)
from .session_targeting_cli import (  # noqa: F401
    _AgentSessionConflict,
    _busy_session_message,
    _caller_worktree_handle,
    _cmd_create,
    _cmd_send,
    _deliver_to_live_session,
    _live_message_delivery,
    _live_message_kind,
    _live_reply_to,
    _live_sender_label,
    _mark_resume_if_behind,
    _match_agents,
    _read_prompt_from_file,
    _resolve_prompt,
    _resolve_target,
    _submit_and_stream,
    _write_session_id_file,
    register_session_targeting_commands,
)
from .venue_cli import (  # noqa: F401
    _cmd_deploy,
    _cmd_parity,
    _fault_frontend_restart_hostindex_loss,
    _passive_daemon_creationflags,
    _passive_daemon_stdio_kwargs,
    register_venue_commands,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-bridge", description="Persistent inter-agent communication service")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--json", action="store_true", default=False, help="Output in JSON format")
    parser.add_argument(
        "--project",
        "-p",
        dest="project",
        default=None,
        metavar="REPO",
        help="Scope project-addressed verbs to REPO instead of the caller's cwd project. For send/create the remote worktree resolve targets REPO; for agents/machines the displayed catalog is filtered to REPO. Injected by the `<repo> <slug>` router.",
    )
    sub = parser.add_subparsers(dest="command")
    register_service_start_commands(sub)
    register_config_commands(sub)
    register_venue_commands(sub)
    register_service_control_commands(sub)
    register_inventory_commands(sub)
    register_session_maintenance_commands(sub)
    register_session_targeting_commands(sub)
    register_session_streaming_commands(sub)
    register_session_lifecycle_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _set_project_override(getattr(args, "project", None))
    _guard_project_scope(parser, args)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if hasattr(args, "func"):
        from .client import BridgeClientError, BridgeConnectionError

        try:
            args.func(args)
        except BridgeConnectionError as exc:
            _exit_bridge_outage(exc)
        except BridgeClientError as exc:
            print(f"[FAIL] agent-bridge request failed ({exc}).", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
