"""Agent, machine, and live-session inventory commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


def _core():
    from . import __main__ as core

    return core


def _cmd_agent_show(args: argparse.Namespace) -> None:
    core = _core()
    from .client import BridgeClientError

    client = core._get_client()
    try:
        agent = client.get_agent(
            args.name,
            include_unaddressable=bool(getattr(args, "include_unaddressable", False)),
        )
    except BridgeClientError as exc:
        if exc.status == 404:
            agent = {}
        else:
            raise
    if not agent:
        if args.json:
            core._json_out(None)
        else:
            print(f"(no such agent: {args.name!r})")
        raise SystemExit(1)
    if args.json:
        core._json_out(agent)
    else:
        display = agent.get("display_name", "") or agent.get("name", "")
        print(display)
        if agent.get("name") and agent.get("name") != display:
            print(f"  Name:     {agent['name']}")
        aliases = agent.get("aliases") or []
        if aliases:
            print(f"  Aliases:  {', '.join(aliases)}")
        target_type = agent.get("target_type", "")
        if target_type:
            print(f"  Type:     {target_type}")
        host = agent.get("host", "")
        if host:
            print(f"  Host:     {host}")
        if agent.get("managed"):
            print(f"  Managed:  {agent['managed']}")


def _report_topology_errors(errors: list[str]) -> None:
    if not errors:
        return
    print(f"[FAIL] {len(errors)} topology profile error(s):", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(2)


def _listing_project(args: argparse.Namespace) -> str | None:
    core = _core()
    if args.all_projects:
        if core._PROJECT_OVERRIDE and not core._PROJECT_ROUTED:
            print("[FAIL] --project and --all-projects are mutually exclusive", file=sys.stderr)
            sys.exit(2)
        return None
    if args.json and not core._PROJECT_OVERRIDE:
        return None
    return core._sender_repo()


def _cmd_agents(args: argparse.Namespace) -> None:
    core = _core()
    client = core._get_client()
    agents, topology_errors = client.list_agents_with_diagnostics()
    project = _listing_project(args)
    total = len(agents)
    if project:
        project_key = project.casefold()
        agents = [
            agent
            for agent in agents
            if agent.get("project") is None or str(agent.get("project")).casefold() == project_key
        ]
    if args.json:
        core._json_out(agents)
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
            heading = display or name
            print(heading)
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
        print(f"\n({total - len(agents)} other-project agent(s) hidden; use --all-projects)")
    _report_topology_errors(topology_errors)


def _live_session_summary_line(s: dict[str, Any]) -> str:
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


def _launch_cli_mode_session(
    client: Any,
    worktree_id: str,
    *,
    ttl_seconds: float = 300.0,
    driver: str | None = "cli-mode",
    seed: str | None = None,
    verify_timeout: float = 30.0,
    embody_bin: str = "agent-worktrees",
    run: Any = None,
) -> dict[str, Any]:
    reservation = client.create_cli_mode_reservation(worktree_id, ttl_seconds=ttl_seconds)
    argv = [
        embody_bin,
        "embody",
        "--worktree-id",
        worktree_id,
        "--json",
        "--verify-timeout",
        str(verify_timeout),
        "--ensure-mux",
    ]
    if driver:
        argv += ["--driver", driver]
    if seed:
        argv += ["--seed", seed]
    runner = run or subprocess.run
    result = runner(argv, capture_output=True, text=True)
    embody_out: dict[str, Any] = {}
    stdout = getattr(result, "stdout", None)
    if stdout:
        try:
            embody_out = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            embody_out = {}
    final = client.get_cli_mode_reservation(worktree_id) or reservation
    return {
        "reservation": final,
        "embody": embody_out,
        "session": embody_out.get("session"),
        "exit_code": getattr(result, "returncode", None),
    }


def _cmd_live_sessions(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
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
            core._json_out(session or {})
            return
        if not session:
            print(f"(no live session for handle {args.handle!r})")
            return
        print(_live_session_summary_line(session))
        return

    if action == "deregister":
        result = client.deregister_live_session(args.session_id)
        if args.json:
            core._json_out(result)
            return
        print(f"deregistered {args.session_id}")
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
            core._json_out(session or {})
            return
        lp = (session or {}).get("latest_progress") or {}
        print(f"progress recorded: {lp.get('phase', '')} {lp.get('summary', '')}".strip())
        return

    if action == "cli-mode":
        cli_mode_action = getattr(args, "cli_mode_action", None)
        worktree_id = getattr(args, "worktree_id", None)
        if cli_mode_action == "reserve":
            venue = None
            raw_venue = getattr(args, "venue_json", None)
            if raw_venue:
                try:
                    venue = json.loads(raw_venue)
                except ValueError as exc:
                    print(f"[FAIL] --venue-json is not valid JSON: {exc}", file=sys.stderr)
                    sys.exit(2)
            try:
                reservation = client.create_cli_mode_reservation(
                    worktree_id, ttl_seconds=getattr(args, "ttl_seconds", 300.0),
                    venue=venue,
                )
            except BridgeClientError as exc:
                if exc.status == 409:
                    print(f"(worktree {worktree_id!r} already has an active CLI-mode reservation)", file=sys.stderr)
                    if args.json:
                        core._json_out({"error": "reservation_active"})
                    sys.exit(1)
                raise
            if args.json:
                core._json_out(reservation)
                return
            print(f"reserved {worktree_id}: reservation_id={reservation.get('reservation_id')} expires_at={reservation.get('expires_at')}")
            return
        if cli_mode_action == "status":
            reservation = client.get_cli_mode_reservation(worktree_id)
            if args.json:
                core._json_out(reservation)
                return
            if not reservation:
                print(f"(no CLI-mode reservation for worktree {worktree_id!r})")
                return
            claimed = reservation.get("claimed_by_session_id")
            state = f"claimed by {claimed}" if claimed else "pending, unclaimed"
            print(f"{worktree_id}: reservation_id={reservation.get('reservation_id')} ({state}), expires_at={reservation.get('expires_at')}")
            return
        if cli_mode_action == "release":
            removed = client.release_cli_mode_reservation(
                worktree_id, reservation_id=getattr(args, "reservation_id", None),
            )
            if args.json:
                core._json_out({"removed": removed})
                return
            print(f"released {removed} reservation(s) for {worktree_id}")
            return
        if cli_mode_action == "launch":
            try:
                outcome = _launch_cli_mode_session(
                    client,
                    worktree_id,
                    ttl_seconds=getattr(args, "ttl_seconds", 300.0),
                    driver=getattr(args, "driver", None) or "cli-mode",
                    seed=getattr(args, "seed", None),
                    verify_timeout=getattr(args, "verify_timeout", 30.0),
                )
            except BridgeClientError as exc:
                if exc.status == 409:
                    print(f"(worktree {worktree_id!r} already has an active CLI-mode reservation)", file=sys.stderr)
                    if args.json:
                        core._json_out({"error": "reservation_active"})
                    sys.exit(1)
                raise
            if args.json:
                core._json_out(outcome)
                return
            reservation = outcome.get("reservation") or {}
            claimed = reservation.get("claimed_by_session_id")
            state = f"claimed by {claimed}" if claimed else "pending, unclaimed"
            session_name = outcome.get("session") or "?"
            print(f"CLI-mode session for {worktree_id}: mux session {session_name!r} (reservation {state}). Attach with tmux/psmux attach-session -t {session_name}.")
            return
        print("usage: agent-bridge live-sessions cli-mode {reserve,status,release,launch}", file=sys.stderr)
        sys.exit(2)

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
        core._json_out(sessions)
        return
    if not sessions:
        print("(no live interactive sessions registered)")
        return
    for s in sessions:
        print(_live_session_summary_line(s))


def _cmd_machines(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
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
            if (agent.get("project") is None or str(agent.get("project")).casefold() == project_key)
            and agent.get("machine_key")
        }
        machines = [machine for machine in machines if machine.get("key") in project_hosts]
    if args.json:
        core._json_out(machines)
    else:
        core._table(
            machines,
            [
                ("key", "MACHINE", 20),
                ("display_name", "NAME", 24),
                ("environment", "ENV", 16),
                ("role", "ROLE", 30),
                ("ssh_ready", "SSH", 5),
            ],
        )
        if project and total > len(machines):
            print(f"\n({total - len(machines)} other-project machine(s) hidden; use --all-projects)")
    _report_topology_errors(topology_errors)


def register_inventory_commands(sub: argparse._SubParsersAction) -> None:
    agent_show_p = sub.add_parser(
        "agent-show",
        help="Show one registered agent by name (fast path -- static/topology lookup only, never enumerates namespace/CodeSpace/container providers)",
    )
    agent_show_p.add_argument("name", help="Agent name to look up")
    agent_show_p.add_argument("--include-unaddressable", action="store_true", help=argparse.SUPPRESS)
    agent_show_p.set_defaults(func=_cmd_agent_show)

    agents_p = sub.add_parser("agents", help="List registered agents")
    agents_p.add_argument("--all-projects", action="store_true", help="Show the fleet-wide catalog instead of the cwd/--project scope")
    agents_p.set_defaults(func=_cmd_agents)

    machines_p = sub.add_parser("machines", help="List topology machines")
    machines_p.add_argument("--all-projects", action="store_true", help="Show every topology machine instead of the cwd/--project scope")
    machines_p.set_defaults(func=_cmd_machines)

    live_p = sub.add_parser("live-sessions", help="List/resolve registered live interactive CLI sessions")
    live_sub = live_p.add_subparsers(dest="live_action")
    live_list_p = live_sub.add_parser("list", help="List registered live interactive CLI sessions")
    live_list_p.add_argument("--worktree-id", help="Filter by worktree id")
    live_list_p.add_argument("--all", dest="include_dead", action="store_true", help="include dead (expired / taken-over) rows, normally hidden")
    live_list_p.set_defaults(func=_cmd_live_sessions)
    live_resolve_p = live_sub.add_parser("resolve", help="Resolve a session id OR worktree handle to its live session")
    live_resolve_p.add_argument("--handle", required=True, help="Exact session id OR worktree handle")
    live_resolve_p.set_defaults(func=_cmd_live_sessions)
    live_deregister_p = live_sub.add_parser("deregister", help="Remove a live session whose process is known to be gone (exact session id; idempotent)")
    live_deregister_p.add_argument("--session-id", required=True, help="Exact session id (never a worktree handle)")
    live_deregister_p.set_defaults(func=_cmd_live_sessions)
    live_progress_p = live_sub.add_parser("progress", help="Record an operator-driven session's progress beat (Phase 7 7c)")
    live_progress_p.add_argument("--handle", required=True, help="Exact session id OR worktree handle")
    live_progress_p.add_argument("--summary", required=True, help="one-line status toward the goal (hard-capped; keep it a line)")
    live_progress_p.add_argument("--phase", default="", help="short phase label")
    live_progress_p.add_argument("--blocker", help="a real blocker, if any")
    live_progress_p.add_argument("--pr", help="the PR/ref this beat corresponds to")
    live_progress_p.set_defaults(func=_cmd_live_sessions)
    live_cli_mode_p = live_sub.add_parser(
        "cli-mode",
        help="Allocate/inspect/release a worktree's CLI-mode Session Host reservation (explicit, per-request; never ambient)",
    )
    live_cli_mode_sub = live_cli_mode_p.add_subparsers(dest="cli_mode_action")
    live_cli_mode_reserve_p = live_cli_mode_sub.add_parser("reserve", help="Reserve a worktree's next CLI-mode session before its muxed CLI process starts")
    live_cli_mode_reserve_p.add_argument("--worktree-id", required=True)
    live_cli_mode_reserve_p.add_argument("--ttl-seconds", type=float, default=300.0, help="Reservation lifetime before it's reclaimable (default 300)")
    live_cli_mode_reserve_p.add_argument("--venue-json", dest="venue_json", default=None, metavar="JSON", help='Venue descriptor the claiming session inherits into its live_sessions.venue, e.g. \'{"kind":"codespace","target":"<name>","mux_session_name":"wt-<id>"}\'')
    live_cli_mode_reserve_p.set_defaults(func=_cmd_live_sessions)
    live_cli_mode_status_p = live_cli_mode_sub.add_parser("status", help="Show a worktree's current CLI-mode reservation, if any")
    live_cli_mode_status_p.add_argument("--worktree-id", required=True)
    live_cli_mode_status_p.set_defaults(func=_cmd_live_sessions)
    live_cli_mode_release_p = live_cli_mode_sub.add_parser("release", help="Release a worktree's CLI-mode reservation")
    live_cli_mode_release_p.add_argument("--worktree-id", required=True)
    live_cli_mode_release_p.add_argument("--reservation-id", dest="reservation_id", default=None, help="Release only this exact reservation (compare-and-delete); a newer reservation made since is left alone")
    live_cli_mode_release_p.set_defaults(func=_cmd_live_sessions)
    live_cli_mode_launch_p = live_cli_mode_sub.add_parser(
        "launch",
        help="Reserve, then hand off to `agent-worktrees embody` -- a DETACHED, mux-wrapped (tmux/psmux), reattachable interactive `copilot` bound to this worktree -- proving the CLI-mode mechanism end-to-end with genuine reattach (Phase 3)",
    )
    live_cli_mode_launch_p.add_argument("--worktree-id", required=True)
    live_cli_mode_launch_p.add_argument("--ttl-seconds", type=float, default=300.0, help="Reservation lifetime before it's reclaimable (default 300)")
    live_cli_mode_launch_p.add_argument("--driver", default="cli-mode", help="Forwarded to `embody --driver` (stamps the 'driven by' banner; default 'cli-mode')")
    live_cli_mode_launch_p.add_argument("--seed", help="Forwarded to `embody --seed` (first interactive turn)")
    live_cli_mode_launch_p.add_argument("--verify-timeout", type=float, default=30.0, help="Forwarded to `embody --verify-timeout` (default 30)")
    live_cli_mode_launch_p.set_defaults(func=_cmd_live_sessions)
    live_cli_mode_p.set_defaults(func=_cmd_live_sessions)
    live_p.set_defaults(func=_cmd_live_sessions)
