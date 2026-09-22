"""Session lifecycle, handoff, and ACP-agent commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from . import _peer_launch


def _core():
    from . import __main__ as core

    return core


def _cmd_stop(args: argparse.Namespace) -> None:
    client = _core()._get_client()
    client.stop_session(
        args.session_id,
        force=getattr(args, "force", False),
        reap_host=getattr(args, "reap_host", False),
    )
    print(f"[OK] Session {args.session_id} stopped")


def _cmd_end(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    client = _core()._get_client()
    try:
        end_kwargs = {"force": getattr(args, "force", False)}
        if getattr(args, "if_idle", False):
            end_kwargs["if_idle"] = True
        client.end_session(args.session_id, **end_kwargs)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[OK] Session {args.session_id} already ended")
            return
        print(f"[FAIL] Could not end session {args.session_id}: {exc.detail}")
        sys.exit(1)
    print(f"[OK] Session {args.session_id} ended")


def _cmd_resume(args: argparse.Namespace) -> None:
    from .resume_handoff_cli import run_resume

    core = _core()
    run_resume(
        client=core._get_client(),
        target=args.session_id,
        reclaim=bool(getattr(args, "force", False)),
        as_json=bool(getattr(args, "json", False)),
        json_out=core._json_out,
        match_agents=core._match_agents,
        startup_request_timeout=core._startup_request_timeout,
    )


def _cmd_handoff(args: argparse.Namespace) -> None:
    from .resume_handoff_cli import run_handoff

    core = _core()
    run_handoff(
        client=core._get_client(),
        target=args.session_id,
        reason=getattr(args, "reason", None),
        seed=not getattr(args, "no_seed", False),
        match_agents=core._match_agents,
    )


def _cmd_handoff_request(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    worktree_id = args.worktree_id
    session_id = args.session_id
    try:
        successor = client.handoff_request(
            worktree_id,
            session_id=session_id,
            seed_text=args.seed,
            handoff_token=args.handoff_token,
        )
    except BridgeClientError as exc:
        if exc.status == 404:
            print(
                f"[SKIP] No current session for worktree {worktree_id} matches "
                f"{session_id}; agent-bridge left the worktree untouched.",
                file=sys.stderr,
            )
            sys.exit(1)
        if exc.status == 409:
            print(f"[FAIL] Cannot hand off worktree {worktree_id}: {exc.detail}", file=sys.stderr)
            sys.exit(1)
        print(f"[FAIL] Could not request handoff for worktree {worktree_id}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "accepted": True,
        "worktree_id": worktree_id,
        "requested_session_id": session_id,
        "handoff_token": args.handoff_token,
        "successor_session_id": successor.get("session_id"),
        "successor_acp_session_id": successor.get("acp_session_id"),
        "successor_status": successor.get("status"),
    }
    if args.json:
        core._json_out(payload)
        return
    print(
        f"[OK] agent-bridge accepted handoff request for worktree {worktree_id} "
        f"session {session_id} -> successor "
        f"{payload['successor_session_id'] or '(unknown)'} "
        f"({payload['successor_status'] or 'unknown'})"
    )


def _agent_bridge_owner_root() -> Path:
    from .install_paths import install_dir

    return install_dir()


def _agent_worktrees_launch_prefix() -> list[str] | None:
    explicit_context = os.environ.get(_peer_launch.CONTEXT_ENV, "")
    if not explicit_context:
        exe = shutil.which("agent-worktrees")
        return [exe] if exe else None
    try:
        own = _peer_launch.validate_owner("agent-bridge", _agent_bridge_owner_root(), explicit_context)
    except (OSError, ValueError, ImportError) as error:
        raise _peer_launch.ContextRefused(f"agent-bridge installation context refused: {error}") from error
    peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    if not peer_root.exists() and not peer_root.is_symlink():
        return None
    try:
        return _peer_launch.launch_prefix("agent-bridge", Path(own["pluginRoot"]), explicit_context, "agent-worktrees")
    except (OSError, ValueError, ImportError) as error:
        raise _peer_launch.ContextRefused(f"same-cell agent-worktrees resolution failed: {error}") from error


def _cmd_handoff_check(args: argparse.Namespace) -> None:
    core = _core()
    try:
        prefix = _agent_worktrees_launch_prefix()
    except _peer_launch.ContextRefused as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        sys.exit(1)
    if not prefix:
        if os.environ.get(_peer_launch.CONTEXT_ENV, ""):
            print("[FAIL] agent-worktrees is not installed in this installation cell; cannot check handoffs.", file=sys.stderr)
        else:
            print("[FAIL] agent-worktrees is not on PATH; cannot check handoffs.", file=sys.stderr)
        sys.exit(1)
    argv = [*prefix, "handoffs-check", "--json"]
    argv += ["--worktree-id", args.worktree_id] if args.worktree_id else ["--all"]
    if args.execute:
        argv.append("--execute")
    child_env = dict(os.environ)
    child_env.pop("AGENT_RT_ROOT", None)
    child_env.pop("AGENT_RT_PY", None)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=child_env, **no_window_kwargs())
    except Exception as exc:
        print(f"[FAIL] could not run agent-worktrees handoffs-check: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        payload = {"error": "unparseable agent-worktrees output", "raw": result.stdout, "stderr": result.stderr}
    if result.returncode != 0 and "findings" not in payload and not payload.get("error"):
        payload = {"error": f"agent-worktrees handoffs-check exited {result.returncode}", "stderr": result.stderr}
    if payload.get("error"):
        if args.json:
            core._json_out(payload)
        else:
            print(f"[FAIL] handoff-check: {payload['error']}", file=sys.stderr)
            if payload.get("stderr"):
                print(payload["stderr"], file=sys.stderr)
        sys.exit(1)
    if args.json:
        core._json_out(payload)
    elif not payload.get("findings"):
        print("[OK] handoff-check: no stalled predecessor retirements found.")
    else:
        for finding in payload["findings"]:
            wt = finding.get("worktree_id")
            pred = finding.get("predecessor_session_id")
            pane = finding.get("retire_pane")
            if not finding.get("executed"):
                print(f"  {wt}: predecessor {pred} still alive (pane {pane})")
            elif finding.get("retired"):
                print(f"[OK] {wt}: retired predecessor {pred} (pane {pane})")
            else:
                print(f"[FAIL] {wt}: could not retire predecessor {pred}", file=sys.stderr)
    sys.exit(result.returncode)


def _cmd_answer(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    caller_id = core._caller_id_for(args)
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
            print(f"[FAIL] {len(pending)} questions are pending on {sid}; pass --tool-call-id (one of: {ids}).", file=sys.stderr)
            sys.exit(1)
        tool_call_id = pending[0].get("tool_call_id")

    try:
        client.answer_ask_user(sid, tool_call_id, content, action=action)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[FAIL] Session {sid} not found", file=sys.stderr)
        else:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Answered ask_user ({action}) on {sid}; the agent's turn continues.")


def _cmd_agent(args: argparse.Namespace) -> None:
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
    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    sm = session_manager_from_config(db, cfg)
    resolver = build_resolver(cfg)
    sm.set_resolver(resolver)
    agent_name = getattr(args, "agent", None)
    if not agent_name:
        print("[FAIL] --agent is required for agent mode", file=sys.stderr)
        sys.exit(1)
    canonical_agent = resolver.canonical_agent_name(agent_name) if resolver else None
    if resolver and canonical_agent is None:
        available = list(resolver.agents.keys())
        print(f"[FAIL] Agent '{agent_name}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)
    if canonical_agent:
        agent_name = canonical_agent
    bridge_agent = BridgeAgent(sm, resolver=resolver, default_agent=agent_name)
    log.info("Starting ACP agent mode (agent=%s)", agent_name)

    async def _run() -> None:
        from acp import run_agent

        try:
            await run_agent(bridge_agent)
        finally:
            await bridge_agent.cleanup()

    asyncio.run(_run())


def register_session_lifecycle_commands(sub: argparse._SubParsersAction) -> None:
    stop_p = sub.add_parser("stop", help="Stop a session")
    stop_p.add_argument("session_id", help="Session ID")
    stop_p.add_argument("--force", action="store_true", help="Tear down even with active background sub-agent tasks (kills them). Prefer waiting for them to finish.")
    stop_p.add_argument("--reap-host", action="store_true", help="Also retire the owned Session Host child instead of preserving it for reattachment")
    stop_p.set_defaults(func=_cmd_stop)

    end_p = sub.add_parser("end", help="End (delete) a session")
    end_p.add_argument("session_id", help="Session ID")
    end_p.add_argument("--force", action="store_true", help="Tear down even with active background sub-agent tasks (kills them). Prefer waiting for them to finish.")
    end_p.add_argument("--if-idle", action="store_true", help="End only if the session is still idle or stopped and has no queued prompts")
    end_p.set_defaults(func=_cmd_end)

    resume_p = sub.add_parser("resume", help="Resume a stopped session, or load/take-over a worktree by handle")
    resume_p.add_argument("session_id", metavar="target", help="Session ID (owned ACP session) or worktree handle to load")
    resume_p.add_argument("--force", "--reclaim", dest="force", action="store_true", help="Break-glass take-over: adopt the worktree even if a live interactive CLI holds it (stop that CLI first)")
    resume_p.set_defaults(func=_cmd_resume)

    from . import restart_worktree_cli

    restart_worktree_cli.add_parser(sub)

    handoff_p = sub.add_parser("handoff", help="Retire a session/worktree and continue in a fresh successor in place")
    handoff_p.add_argument("session_id", metavar="target", help="Session ID (owned ACP session) or worktree handle to hand off")
    handoff_p.add_argument("--reason", default=None, help="Free-form reason carried on the session_handoff event (default: context-pressure)")
    handoff_p.add_argument("--no-seed", action="store_true", help="Do not seed the successor's opening turn with the brief (the caller drives it instead)")
    handoff_p.set_defaults(func=_cmd_handoff)

    handoff_request_p = sub.add_parser("handoff-request", help="Request an externally-seeded in-place handoff for a worktree")
    handoff_request_p.add_argument("--worktree-id", required=True, help="Worktree handle whose current session should be handed off")
    handoff_request_p.add_argument("--session-id", required=True, help="Current bridge or ACP session id expected to own the worktree")
    handoff_request_p.add_argument("--handoff-token", default=None, help="Opaque handoff correlation token carried on the event/result")
    handoff_request_p.add_argument("--seed", required=True, help="Exact opening-turn text to seed into the successor session")
    handoff_request_p.add_argument("--json", action="store_true", help="Emit JSON.")
    handoff_request_p.set_defaults(func=_cmd_handoff_request)

    handoff_check_p = sub.add_parser("handoff-check", help="Diagnose (and with --execute, finish) a stalled handoff-cutover predecessor retirement for a mux/CLI-hosted worktree")
    handoff_check_g = handoff_check_p.add_mutually_exclusive_group(required=True)
    handoff_check_g.add_argument("--worktree-id", default=None, help="Check only this worktree")
    handoff_check_g.add_argument("--all", action="store_true", help="Check every tracked worktree")
    handoff_check_p.add_argument("--execute", action="store_true", help="Retire each found stale predecessor now (default: read-only report)")
    handoff_check_p.add_argument("--json", action="store_true", help="Emit JSON.")
    handoff_check_p.set_defaults(func=_cmd_handoff_check)

    answer_p = sub.add_parser("answer", help="Answer a dispatched agent's parked ask_user question (the elicitation backstop) so its turn continues")
    answer_p.add_argument("session_id", help="Session ID")
    answer_p.add_argument("--field", dest="fields", action="append", default=[], metavar="KEY=VALUE", help="A form field answer (repeatable). Values are strings; use --json for numbers/booleans/complex values.")
    answer_p.add_argument("--json", dest="content_json", default=None, help="Full answer content as a JSON object (overrides --field).")
    answer_p.add_argument("--tool-call-id", dest="tool_call_id", default=None, help="Which parked question to answer (defaults to the sole pending one; required when more than one is outstanding -- see `status`).")
    answer_p.add_argument("--decline", action="store_true", help="Decline the question instead of submitting an answer.")
    answer_p.add_argument("--cancel", action="store_true", help="Cancel the question instead of submitting an answer.")
    answer_p.set_defaults(func=_cmd_answer)

    agent_p = sub.add_parser("agent", help="Run as an ACP agent on stdio")
    agent_p.add_argument("--agent", required=True, help="Name of the downstream agent to route to")
    agent_p.set_defaults(func=_cmd_agent)
