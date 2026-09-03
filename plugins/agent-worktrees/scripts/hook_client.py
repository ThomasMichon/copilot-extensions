#!/usr/bin/env python3
"""Tiny Copilot hook client for the resident agent-worktrees monitor.

The hot path is one bounded loopback request. If the monitor is unavailable,
pre-tool safety guards run in-process from their deployed standalone modules;
post-tool advisory work falls back to nudge_status and bind_nudge.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import time
from functools import cache
from pathlib import Path

_CONNECT_TIMEOUT_S = 0.5
_SESSION_START_TIMEOUT_S = 6.0
_PROJECT_RESOLVE_TIMEOUT_S = 3.0
_FALLBACK_PRE_BUDGET_S = 25.0
_MAX_RESPONSE = 64 * 1024


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _request(kind: str, payload: dict, home: Path) -> dict | None:
    endpoint = _read_json(home / ".agent-worktrees" / "status-monitor.lock")
    if not endpoint:
        return None
    if endpoint.get("hook_transport") != "tcp":
        return None
    address = str(endpoint.get("hook_endpoint") or "")
    host, sep, port_text = address.rpartition(":")
    token = endpoint.get("hook_token")
    if not sep or host != "127.0.0.1" or not port_text.isdigit():
        return None
    if not isinstance(token, str) or not token:
        return None
    timeout = {
        "sessionStart": _SESSION_START_TIMEOUT_S,
        "projectResolve": _PROJECT_RESOLVE_TIMEOUT_S,
    }.get(kind, _CONNECT_TIMEOUT_S)
    request = json.dumps(
        {
            "version": 1,
            "token": token,
            "kind": kind,
            "payload": payload,
            "deadline": time.time() + timeout,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(
            (host, int(port_text)), timeout=timeout
        ) as conn:
            conn.settimeout(timeout)
            conn.sendall(request)
            chunks: list[bytes] = []
            size = 0
            while size < _MAX_RESPONSE:
                chunk = conn.recv(min(4096, _MAX_RESPONSE - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b"\n" in chunk:
                    break
    except OSError:
        return None
    raw = b"".join(chunks).split(b"\n", 1)[0]
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("fallback") is True
    ):
        return None
    result = value.get("result")
    return result if isinstance(result, dict) else {}


@cache
def _load_sibling(name: str):
    path = Path(__file__).resolve().with_name(name)
    try:
        spec = importlib.util.spec_from_file_location(
            f"_agent_worktrees_hook_{path.stem}", path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _merge_pre_decisions(current: dict, new: dict) -> dict:
    result = dict(current)
    contexts = [
        str(value)
        for value in (
            current.get("additionalContext"),
            new.get("additionalContext"),
        )
        if value
    ]
    if contexts:
        result["additionalContext"] = "\n\n".join(contexts)
    rank = {"": 0, "allow": 0, "ask": 1, "deny": 2}
    current_decision = str(current.get("permissionDecision") or "").lower()
    new_decision = str(new.get("permissionDecision") or "").lower()
    if rank.get(new_decision, 0) > rank.get(current_decision, 0):
        result["permissionDecision"] = new.get("permissionDecision")
        reason = new.get("permissionDecisionReason")
        if reason:
            result["permissionDecisionReason"] = reason
    return result


def _fallback_pre(payload: dict, home: Path) -> dict:
    deadline = time.monotonic() + _FALLBACK_PRE_BUDGET_S
    combined: dict = {}
    for name in (
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "anchor_write_guard.py",
    ):
        module = _load_sibling(name)
        if module is None:
            continue
        try:
            kwargs = (
                {"deadline": deadline - 2.0}
                if name == "cross_repo_guard.py" else {}
            )
            decision = module.decide(payload, home=home, **kwargs)
        except Exception:
            continue
        if isinstance(decision, dict) and decision:
            combined = _merge_pre_decisions(combined, decision)
            if combined.get("permissionDecision") == "deny":
                break
    return combined


def _fallback_post(payload: dict, home: Path) -> dict:
    contexts = []
    nudge = _load_sibling("nudge_status.py")
    if nudge is not None:
        try:
            text = nudge.decide(payload, home=home)
            if text:
                contexts.append(text)
        except Exception:
            pass
    binding = _load_sibling("bind_nudge.py")
    if binding is not None:
        try:
            text = binding.decide(payload, home=home)
            if text:
                contexts.append(text)
        except Exception:
            pass
    return (
        {"additionalContext": "\n\n".join(contexts)}
        if contexts else {}
    )


def _session_start_payload(payload: dict) -> dict:
    enriched = dict(payload)
    environment = {}
    for name in (
        "WORKTREE_ID",
        "TMUX_PANE",
        "PSMUX_PANE",
        "WORKTREE_LAUNCH_ID",
        "AGENT_WORKTREES_BIND_PROJECT",
        "AGENT_WORKTREES_BIND_WORKTREE_ID",
        "AGENT_WORKTREES_BIND_SESSION_ID",
        "AGENT_WORKTREES_HANDOFF_TOKEN",
        "AGENT_WORKTREES_PROFILE_ASSIGNMENT_TOKEN",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    if environment:
        enriched["_agentWorktreesEnvironment"] = environment
    return enriched


def decide(kind: str, payload: dict, *, home: Path | None = None) -> dict:
    home = home or Path.home()
    project_payload = dict(payload)
    # Copilot's sessionStart cwd is the worktree; the hook process itself may
    # run from the plugin directory. Use process cwd only for manual callers.
    if not isinstance(project_payload.get("cwd"), str) or not str(
        project_payload["cwd"]
    ).strip():
        project_payload["cwd"] = os.getcwd()
    request_payload = (
        _session_start_payload(payload)
        if kind == "sessionStart"
        else project_payload
        if kind == "projectResolve"
        else payload
    )
    remote = _request(kind, request_payload, home)
    if remote is not None:
        return remote
    if kind == "preToolUse":
        return _fallback_pre(payload, home)
    if kind == "postToolUse":
        return _fallback_post(payload, home)
    if kind == "sessionStart":
        return {"fallback": True}
    if kind == "projectResolve":
        return {"fallback": True}
    return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kind = argv[0] if argv else ""
    if kind not in {
        "preToolUse", "postToolUse", "sessionStart", "projectResolve"
    }:
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        result = decide(kind, payload)
        if kind == "sessionStart":
            context = {}
            if result.get("additionalContext"):
                context["additionalContext"] = result["additionalContext"]
            sys.stdout.write(
                json.dumps(context, separators=(",", ":"))
                + "\n"
                + str(result.get("projectHook") or "-")
                + "\n"
                + ("1" if result.get("fallback") else "0")
            )
        elif kind == "projectResolve":
            sys.stdout.write(
                str(result.get("projectHook") or "-")
                + "\n"
                + ("1" if result.get("fallback") else "0")
            )
        elif result:
            sys.stdout.write(json.dumps(result, separators=(",", ":")))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
