"""Verify detached native-session retirement before releasing its custody."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import shlex
import sys
import time
from typing import Any

from remote_login_shell import wrap_login_shell


def cmd_stop(args: argparse.Namespace, *, ssh_session: Callable[..., int]) -> int:
    """Stop through the venue owner without replaying an unknown portable mutation."""
    from venue_copilot import deregister_live_session, live_session_for, release_cli_mode

    from . import connection_owner as owner
    from .config import load_merged_config
    from .copilot_detach import (
        _codespace, plan_for, _fail, _ssh_namespace, _LAUNCH_ATTEMPTS,
        _transient, _is_transient_result, _COORDINATION_EXIT,
    )
    from .lifecycle import _SHUTDOWN_STATE
    from .worktrees import ContextRefused
    from .portable_mux_control import retained_endpoint

    codespace = _codespace(args.name)
    plan = plan_for(args, load_merged_config(), codespace)
    held = owner.get_hold(args.name)
    recorded = (held.sessions.get(plan["tenant"]) or {}).get("mux_session") if held else None
    if recorded:
        plan["mux_session"] = recorded
    row = live_session_for(plan["scope_id"])
    session_id = row.get("session_id") if (row.get("venue") or {}).get("target") == args.name else None
    try:
        endpoint = retained_endpoint(plan, held, row)
    except (TypeError, ValueError) as exc:
        return _fail(str(exc), plan)
    if endpoint is not None:
        plan["mux_session"] = endpoint.mux_session
        plan["venue"]["mux_session_name"] = endpoint.mux_session
        plan["venue"]["mux_endpoint"] = endpoint.record()

    def released(**extra: Any) -> int:
        release_cli_mode(plan["scope_id"])
        owner.release(args.name, plan["tenant"])
        deregistered = bool(session_id) and deregister_live_session(session_id)
        print(json.dumps({"ok": True, "stopped": True, **extra,
                          "deregistered": session_id if deregistered else None, **plan}, indent=2))
        return 0

    if getattr(codespace, "state", None) == _SHUTDOWN_STATE:
        return released(already_shutdown=True)
    if endpoint is not None:
        try:
            script = endpoint.command("stop")
        except ValueError as exc:
            return _fail(str(exc), plan)
    else:
        target = shlex.quote("=" + plan["mux_session"])
        script = (
            f"tmux kill-session -t {target} 2>/dev/null; sleep 1; "
            f"if tmux has-session -t {target} 2>/dev/null; then echo STILL_RUNNING; exit 3; fi; "
            "echo STOPPED"
        )
    captured: dict[str, Any] = {}

    def sink(result: Any) -> int:
        captured["result"] = result
        return int(getattr(result, "exit_code", 1))

    attempts = 1 if endpoint is not None else _LAUNCH_ATTEMPTS
    for attempt in range(attempts):
        try:
            rc = ssh_session(
                _ssh_namespace(args, wrap_login_shell(script), timeout=120.0),
                result_sink=sink,
                settle_on_disconnect=not getattr(args, "keep_claim", False),
            )
        except ContextRefused as exc:
            print(f"[BLOCKED] CodeSpace installation context refused: {exc}", file=sys.stderr)
            return _COORDINATION_EXIT
        except (RuntimeError, OSError) as exc:
            if attempt + 1 < attempts and _transient(str(exc)):
                time.sleep(5.0)
                continue
            return _fail(f"could not connect to the CodeSpace ({exc}); nothing was released", plan)
        result = captured.get("result")
        if attempt + 1 < attempts and result is not None and _is_transient_result(result):
            time.sleep(5.0)
            continue
        break
    stdout = getattr(captured.get("result"), "stdout", "") or ""
    if rc != 0 or "STOPPED" not in stdout:
        return _fail("could not verify the session stopped; nothing was released", plan, exit_code=rc)
    return released()
