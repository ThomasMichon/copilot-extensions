"""Session-listing and maintenance commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import sys


def _core():
    from . import __main__ as core

    return core


def _cmd_gc(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    try:
        res = client.gc()
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        core._json_out(res)
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
    core = _core()
    client = core._get_client()
    sessions = client.list_sessions(status=args.status)
    if args.json:
        core._json_out(sessions)
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
        updated = core._short_dt(s.get("updated_at"))

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
        live = core._liveness_line(s)
        if live:
            print(f"    Liveness: {live}")


def _peek_iso(ts: object) -> str:
    s = str(ts or "")
    return s[:19].replace("T", " ") if s else "-"


def _cmd_peek(args: argparse.Namespace) -> None:
    from . import peek_snapshot as ps
    from . import target_exec as tx

    core = _core()
    client = core._get_client()
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
        session = cands[0] if cands else None
    if not session:
        print(f"[FAIL] no session found for '{target}' (pass a session id or agent name)", file=sys.stderr)
        sys.exit(1)

    sid = session.get("session_id") or session.get("id") or ""
    agent = session.get("agent_name") or ""
    acp = session.get("acp_session_id")
    if not acp:
        msg = f"session {sid} ({agent}) has no acp_session_id yet -- copilot has not written a transcript"
        if args.json:
            core._json_out({"ok": False, "reason": msg, "session_id": sid, "agent": agent})
        else:
            print(f"[peek] {msg}")
        return

    try:
        kind = tx.target_kind(session)
        if kind == "local":
            snap = ps.snapshot_local(acp, tail_lines=args.tail, recent_messages=args.recent, message_chars=args.message_chars)
        else:
            cmd = ps.build_peek_command(acp, tail_lines=args.tail, recent_messages=args.recent, message_chars=args.message_chars)
            out = tx.exec_bash_on_target(session, cmd, timeout=float(args.timeout))
            snap = ps.parse_peek_result(out)
    except tx.TargetExecError as exc:
        print(f"[FAIL] peek transport error: {exc}", file=sys.stderr)
        sys.exit(1)

    verdict, reason = ps.reuse_verdict(snap, stale_after_seconds=float(args.stale_hours) * 3600)

    if args.json:
        core._json_out({"session_id": sid, "agent": agent, "acp_session_id": acp, "verdict": verdict, "verdict_reason": reason, "snapshot": snap})
        return

    print(f"  {sid}  ({agent})  [{session.get('status', '')}]")
    print(f"    acp:     {acp}")
    print(f"    reuse:   {verdict.upper()} -- {reason}")
    if not snap.get("ok"):
        print(f"    (no snapshot: {snap.get('reason', '?')})")
        return
    life = snap.get("lifecycle") or {}
    usage = snap.get("usage") or {}
    print(f"    turns:   {snap.get('turns')}    model: {snap.get('model') or '?'}    size: {(snap.get('size_bytes') or 0) // 1024}k")
    print(f"    life:    started={_peek_iso(life.get('started_at'))}  resumed={_peek_iso(life.get('resumed_at'))}  shutdown={(life.get('last_shutdown') or {}).get('type') or 'none'}")
    if usage:
        print(f"    usage:   premium={usage.get('premium_requests')} nanoAiu={usage.get('nano_aiu')}")
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


def _cmd_drain(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    try:
        res = client.drain(timeout=args.timeout, poll=args.poll, force=args.force)
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        core._json_out(res)
    else:
        busy = res.get("busy_sessions", [])
        if res.get("clean"):
            print("Drain complete: no busy sessions remain.")
        elif res.get("forced"):
            print(f"[WARN] Drain forced past {len(busy)} busy session(s): {', '.join(busy)}")
        else:
            print(f"[WARN] Drain timed out; {len(busy)} session(s) still busy: {', '.join(busy)}")
    if not res.get("drained"):
        sys.exit(2)


def _cmd_undrain(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    try:
        client.undrain()
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print("Drain gate released; accepting new work.")


def _cmd_session_usage(args: argparse.Namespace) -> None:
    core = _core()
    client = core._get_client()
    usage = client.get_session_usage(args.session_id)
    if args.json:
        core._json_out(usage)
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
        print(f"Updated:  {core._short_dt(last_at)}")


def register_session_maintenance_commands(sub: argparse._SubParsersAction) -> None:
    sessions_p = sub.add_parser("sessions", help="List sessions")
    sessions_p.add_argument("--status", help="Filter by status")
    sessions_p.set_defaults(func=_cmd_sessions)

    peek_p = sub.add_parser(
        "peek",
        help="Copilot-free peek at a target's current session transcript (events.jsonl snapshot + reuse-worthiness) without launching ACP",
    )
    peek_p.add_argument("target", help="Session ID or agent name (e.g. codespace:<name>)")
    peek_p.add_argument("--tail", type=int, default=400, help="Trailing events.jsonl lines to scan (default 400)")
    peek_p.add_argument("--recent", type=int, default=8, help="Recent user+assistant messages / tool calls to surface (default 8)")
    peek_p.add_argument("--message-chars", dest="message_chars", type=int, default=400, help="Max chars per surfaced message (default 400)")
    peek_p.add_argument("--timeout", type=float, default=90.0, help="Remote read timeout seconds for a codespace target (default 90)")
    peek_p.add_argument("--stale-hours", dest="stale_hours", type=float, default=6.0, help="Age past which a session is 'cold' in the verdict (default 6h)")
    peek_p.add_argument("--json", action="store_true", help="Emit JSON.")
    peek_p.set_defaults(func=_cmd_peek)

    gc_p = sub.add_parser("gc", help="Garbage-collect aged terminal/disconnected sessions and compact the sessions.db (reclaims freelist bloat)")
    gc_p.set_defaults(func=_cmd_gc)

    drain_p = sub.add_parser("drain", help="Stop accepting new sessions/turns and wait for in-flight work to settle (zero-downtime pre-swap step)")
    drain_p.add_argument("--timeout", type=float, default=300.0, metavar="SECONDS", help="Max seconds to wait for busy sessions to settle (default 300).")
    drain_p.add_argument("--poll", type=float, default=1.0, metavar="SECONDS", help="Poll interval while waiting (default 1.0).")
    drain_p.add_argument("--force", action="store_true", help="Proceed (exit 0) even if busy sessions remain at timeout.")
    drain_p.add_argument("--json", action="store_true", help="Emit JSON.")
    drain_p.set_defaults(func=_cmd_drain)

    undrain_p = sub.add_parser("undrain", help="Release the drain gate -- resume accepting new work (cutover rollback)")
    undrain_p.set_defaults(func=_cmd_undrain)

    usage_p = sub.add_parser("session-usage", help="Show context window usage for a session")
    usage_p.add_argument("session_id", help="Session ID")
    usage_p.set_defaults(func=_cmd_session_usage)
