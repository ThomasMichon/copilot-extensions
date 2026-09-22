"""Prompt submission and target-resolution commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from typing import Any


def _core():
    from . import __main__ as core

    return core


def _read_prompt_from_file(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print(f"[FAIL] --prompt-file: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _resolve_prompt(args: argparse.Namespace, *, required: bool) -> str | None:
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
    core = _core()
    if getattr(args, "new", False):
        print(
            "[FAIL] `agent-bridge send --new` has been removed. `send` always "
            "reuses (and resumes) this caller's existing session.\n"
            "       For a brand-new session, use:\n"
            f"         agent-bridge create {args.target} \"<prompt>\"",
            file=sys.stderr,
        )
        sys.exit(2)

    client = core._get_client()
    target = args.target
    prompt = _resolve_prompt(args, required=True)

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

    caller_id = core._caller_id_for(args)
    session_id = _resolve_target(client, target, force=getattr(args, "force", False))
    if not getattr(args, "full_history", False):
        _mark_resume_if_behind(client, session_id, caller_id=caller_id)
    _submit_and_stream(client, args, session_id, prompt, caller_id=caller_id)


def _caller_worktree_handle() -> str | None:
    wt_dir = _core()._worktrees_get("worktree-dir")
    if not wt_dir:
        return None
    return os.path.basename(wt_dir.rstrip("/")) or None


def _live_reply_to(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "reply_to", None)
    if explicit:
        return explicit
    handle = _caller_worktree_handle()
    if handle:
        return handle
    return os.environ.get("AGENT_BRIDGE_SESSION_ID") or os.environ.get("SESSION_ID")


def _live_sender_label(args: argparse.Namespace) -> str:
    explicit = getattr(args, "sender", None)
    if explicit:
        return explicit
    return _core()._get_caller_id() or os.environ.get("USER") or socket.gethostname()


def _live_message_kind(args: argparse.Namespace) -> str:
    if getattr(args, "notify", False):
        return "notify"
    if getattr(args, "status_check", False):
        return "status-check"
    return getattr(args, "kind", None) or "prompt"


def _deliver_to_live_session(client, args: argparse.Namespace, session_id: str, prompt: str) -> None:
    core = _core()
    sender = core._live_sender_label(args)
    reply_to = core._live_reply_to(args)
    kind = core._live_message_kind(args)
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
        session_id,
        sender=sender,
        body=prompt,
        reply_to=reply_to,
        kind=kind,
        wait=wait,
        wait_timeout=wait_timeout,
        **delivery_options,
    )
    if args.json:
        core._json_out({"delivered": True, "target": session_id, **result})
        return
    mid = result.get("message_id")
    kind_note = "" if kind == "prompt" else f", kind {kind}"
    print(f"[>] Delivered to live session {session_id} (message {mid}, from {sender}{kind_note})")
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
        print(f"\n[..] No reply within {wait_timeout:g}s (message is queued and will still be delivered).")


def _submit_and_stream(
    client,
    args: argparse.Namespace,
    session_id: str,
    prompt: str,
    *,
    caller_id: str | None,
) -> None:
    core = _core()
    queue = getattr(args, "queue", False)
    result = client.submit_prompt(
        session_id,
        prompt,
        queue=queue,
        caller_id=caller_id,
        request_timeout=core._startup_request_timeout(resume=True, fresh_fallback=True),
    )

    if result.get("queued"):
        ident = core._connection_identity(client, session_id)
        if args.json:
            core._json_out({"session_id": session_id, "connection": ident, **result})
            return
        pos = result.get("position")
        qid = result.get("queue_id")
        print(
            f"[~] Session {session_id} busy -- prompt queued durably "
            f"(id {qid}, position {pos}). It sends when the current turn settles."
        )
        core._print_connection_identity(ident)
        return

    turn_index = result.get("turn_index", 0)
    ident = core._connection_identity(client, session_id)

    if args.json:
        core._json_out({"session_id": session_id, "connection": ident, **result})
        return

    print(f"[>] Session {session_id} -- turn {turn_index}")
    core._print_connection_identity(ident)

    if args.no_wait:
        print("[>] Prompt submitted (--no-wait)")
        return

    timeouts = core._phased_timeouts()
    renderer = core._make_renderer(args)
    core._stream_feed(
        client,
        session_id,
        caller_id=caller_id,
        renderer=renderer,
        command_timeout=timeouts.command,
    )


class _AgentSessionConflict(Exception):
    def __init__(self, agent_name: str, existing_session_id: str) -> None:
        self.agent_name = agent_name
        self.existing_session_id = existing_session_id
        super().__init__(f"Agent '{agent_name}' already has an active session {existing_session_id}")


def _busy_session_message(client, session_id: str, agent_name: str, caller_id: str | None) -> str:
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
    lines.append(f"    - WAIT / OBSERVE:  agent-bridge wait {session_id}     (block until the turn settles, then re-send)")
    lines.append(f"                       agent-bridge read {session_id} --tail 30   (peek without consuming)")
    lines.append(
        f"    - TAKE OVER:       agent-bridge end {session_id}, then re-send -- or re-run with --force (discards the in-flight turn's work)"
    )
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
    core = _core()
    from .client import BridgeClientError

    try:
        session = client.get_session(target)
        if session:
            status = session.get("status", "")
            if status == "idle":
                return target
            elif status == "stopped":
                print(f"[>] Resuming stopped session {target}...")
                client.resume_session(target, request_timeout=core._startup_request_timeout(resume=True))
                return target
            else:
                agent = session.get("agent_name") or ""
                if not force:
                    print(
                        _busy_session_message(client, target, agent or target, session.get("caller_id")),
                        file=sys.stderr,
                    )
                    sys.exit(core._SEND_BUSY_EXIT)
                print(f"[>] --force: ending busy session {target} to take over...")
                try:
                    client.end_session(target)
                except Exception:
                    pass
                if agent:
                    return core._start_agent_session(client, agent, force=False)
                print(
                    f"[FAIL] Session {target} ended; no agent recorded -- re-send to the agent name to start a fresh session.",
                    file=sys.stderr,
                )
                sys.exit(1)
    except BridgeClientError as exc:
        if exc.status != 404:
            raise

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
        return core._start_agent_session(
            client,
            matches[0],
            force_new=force_new,
            refuse_on_conflict=refuse_on_conflict,
            force=force,
            model=model,
            effort=effort,
            target_dir=target_dir,
            worktree_id=worktree_id,
        )

    try:
        return core._start_agent_session(
            client,
            target,
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

    print(f"[FAIL] '{target}' is not a known agent name or session ID", file=sys.stderr)
    sys.exit(1)


def _match_agents(target: str, agents: list[dict]) -> list[str]:
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
            if a.get("bare_addressable", True) is False:
                continue
            bare_forms = {f.split(":", 1)[1].casefold() for f in forms if ":" in f}
            if target_key in bare_forms and name not in matches:
                matches.append(name)
    return matches


def _cmd_create(args: argparse.Namespace) -> None:
    core = _core()
    client = core._get_client()
    target = args.target
    caller_id = core._caller_id_for(args)

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
    from .resume_handoff_cli import reject_singleton_create

    reject_singleton_create(client=client, target=target, match_agents=core._match_agents)

    try:
        session_id = core._resolve_target(
            client,
            target,
            force_new=True,
            refuse_on_conflict=True,
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
            print(f"[FAIL] Could not write --session-id-file {session_id_file!r}: {exc}", file=sys.stderr)
            sys.exit(1)

    prompt = _resolve_prompt(args, required=False)
    if not prompt:
        ident = core._connection_identity(client, session_id)
        if args.json:
            core._json_out({"session_id": session_id, "connection": ident})
        else:
            core._print_connection_identity(ident)
            print(f"[OK] Session {session_id} created -- send work with: agent-bridge send {session_id} \"<prompt>\"")
        return

    _submit_and_stream(client, args, session_id, prompt, caller_id=caller_id)


def _write_session_id_file(path_value: str, session_id: str) -> None:
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


def _mark_resume_if_behind(client, session_id: str, *, caller_id: str | None) -> bool:
    try:
        info = client.get_cursor_info(session_id, caller_id=caller_id)
    except Exception:
        return False
    if info.get("last_acked_id", 0) != 0:
        return False

    try:
        session = client.get_session(session_id)
    except Exception:
        return False
    turn_count = session.get("turn_count", 0) or 0
    head = info.get("head_id", 0) or 0
    if turn_count <= 0 or head <= 0:
        return False

    try:
        client.ack_cursor(session_id, head, caller_id=caller_id)
    except Exception:
        return False
    print(
        f"[>] Resuming existing session {session_id} ({turn_count} prior turn(s)) -- earlier conversation hidden. "
        f"Run `agent-bridge read {session_id} --range 1-{head}` to view it, or "
        "`agent-bridge send --full-history` to replay it. For a clean session, "
        "end this one and use `agent-bridge create`."
    )
    return True


def register_session_targeting_commands(sub: argparse._SubParsersAction) -> None:
    core = _core()
    send_p = sub.add_parser("send", help="Send a prompt to an agent or session (reuses/resumes this caller's existing session)")
    send_p.add_argument("target", help="Agent name, session ID, or a live session's worktree handle (a worktree handle resolves to whichever session is live now)")
    send_p.add_argument("prompt", nargs="?", default=None, help="Prompt text to send (omit and use --prompt-file for multi-line prompts that shouldn't transit the shell's argv)")
    send_p.add_argument("--prompt-file", dest="prompt_file", default=None, metavar="PATH", help="Read the prompt from PATH (or '-' for stdin) instead of the positional argument. Use this for multi-line prompts to avoid shell argv mangling (e.g. PowerShell word-splitting a prompt at the first embedded double-quote). Mutually exclusive with the positional prompt.")
    send_p.add_argument("--sender", "--from", dest="sender", default=None, help="Attribution label when the target is a live interactive session (default: this caller's worktree/user). Legibility only, not routing.")
    send_p.add_argument("--reply-to", dest="reply_to", default=None, help="Routable handle a reply should target when delivering to a live session -- a worktree handle (survives handoff) or a session id (default: this caller's own worktree handle, else its session id from the environment). Rendered as the envelope's reply-to.")
    send_p.add_argument("--kind", choices=["prompt", "notify", "status-check"], default="prompt", help="Typed intent when delivering to a live session: 'prompt' (a work directive, default) vs 'notify'/'status-check' (asks only for a terse out-of-band ack, never treated as new work).")
    send_p.add_argument("--notify", action="store_true", help="Shorthand for --kind notify (informational; no work expected).")
    send_p.add_argument("--status-check", dest="status_check", action="store_true", help="Shorthand for --kind status-check (asks for a terse status ack).")
    send_p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for response")
    send_p.add_argument("--reply-timeout", type=float, default=120.0, metavar="SECONDS", help="When delivering to a live interactive session, how long to wait for the receiver's reply turn before returning (default 120; the message is queued and still delivered on timeout). Ignored with --no-wait.")
    send_p.add_argument("--new", action="store_true", help=argparse.SUPPRESS)
    send_p.add_argument("--full-history", action="store_true", help="When resuming an existing session, replay its prior conversation instead of fast-forwarding past it (default hides the backlog and prints a marker)")
    send_p.add_argument("--force", action="store_true", help="If the target's session is busy running a turn, terminate that in-flight turn and start a fresh session to deliver this prompt (discards the in-flight turn's work). Without --force, a busy target is rejected with guidance to wait/observe or end it.")
    send_p.add_argument("--queue", action="store_true", help="If the target's session is busy, durably queue this prompt server-side (in the bridge's pending_prompts table) for FIFO delivery when the current turn settles -- surviving a caller remount and a bridge/host restart -- instead of rejecting it. The opposite of --force: it preserves the in-flight turn.")
    send_p.add_argument("--idempotency-key", help="stable producer key; retries return the original live-message id instead of enqueuing a duplicate")
    send_p.add_argument("--expected-session-id", help="deliver only if the target still resolves to this exact live session id (checked again atomically when enqueuing)")
    core._add_stream_args(send_p)
    send_p.set_defaults(func=_cmd_send)

    create_p = sub.add_parser("create", help="Create a fresh session for an agent (optionally send a first prompt). Refuses if a one-session-per-CodeSpace agent is busy.")
    create_p.add_argument("target", help="Agent name (not a session ID)")
    create_p.add_argument("prompt", nargs="?", default=None, help="Optional first prompt to send to the new session")
    create_p.add_argument("--prompt-file", dest="prompt_file", default=None, metavar="PATH", help="Read the first prompt from PATH (or '-' for stdin) instead of the positional argument. Use this for multi-line prompts to avoid shell argv mangling (e.g. PowerShell word-splitting a prompt at the first embedded double-quote). Mutually exclusive with the positional prompt.")
    create_p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for response")
    create_p.add_argument("--model", dest="model", default=None, metavar="MODEL", help="Run THIS session on MODEL (e.g. gpt-5.6-sol). Copilot ignores --model in ACP mode, so the bridge applies it per-session via session/set_config_option, at highest precedence over the daemon default model. Omit to keep the daemon's default model.")
    create_p.add_argument("--session-id-file", dest="session_id_file", default=None, metavar="PATH", help="Atomically write this process's exact created session id before streaming the first turn.")
    create_p.add_argument("--effort", dest="effort", default=None, metavar="EFFORT", help="Reasoning-effort override for THIS session (e.g. low|medium|high), applied the same per-session way as --model.")
    create_p.add_argument("--target-dir", dest="target_dir", default=None, metavar="PATH", help="Run this agent session in an existing checkout directory.")
    create_p.add_argument("--worktree-id", dest="worktree_id", default=None, metavar="ID", help="Bind the created session to an existing agent-worktrees worktree id.")
    core._add_stream_args(create_p)
    create_p.set_defaults(func=_cmd_create)
