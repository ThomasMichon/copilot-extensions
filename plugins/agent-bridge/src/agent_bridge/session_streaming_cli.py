"""Streaming, wait, read, and result commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from typing import Any


def _core():
    from . import __main__ as core

    return core


def _turn_settled(client, session_id: str, cursor: int) -> bool:
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


def _stream_feed(
    client,
    session_id: str,
    *,
    caller_id: str | None,
    renderer,
    command_timeout: float = 0.0,
    attention_reasons: list[str] | None = None,
    attention_position: str | None = None,
    follow_handle: str | None = None,
) -> str | dict[str, Any]:
    import time

    core = _core()
    from .client import BridgeClientError, BridgeConnectionError

    try:
        cursor = client.get_cursor(session_id, caller_id=caller_id)
    except Exception:
        cursor = 0

    start = time.monotonic()
    last_activity = start
    deadline = (start + command_timeout) if command_timeout else None
    turn_complete_seen = False
    first_404_at: float | None = None
    attention_result: dict[str, Any] | None = None
    follow_successor_pending = False

    def _probe_attention() -> dict[str, Any] | None:
        if not attention_reasons:
            return None
        return client.wait_for_attention(session_id, reasons=attention_reasons, position=attention_position, timeout_seconds=0)

    def _ack(up_to: int) -> None:
        try:
            client.ack_cursor(session_id, up_to, caller_id=caller_id)
        except Exception:
            pass

    def _follow_successor(*, require_successor: bool) -> bool:
        nonlocal session_id, cursor, turn_complete_seen, first_404_at
        new_session_id = core._wait_for_worktree_read_target(
            client,
            follow_handle or "",
            prior_session_id=session_id,
            require_successor=require_successor,
        )
        if not new_session_id:
            return False
        if new_session_id != session_id:
            print(f"\n[>] Session handed off -> {new_session_id}; continuing to follow.", file=sys.stderr)
            session_id = new_session_id
            try:
                cursor = client.get_cursor(session_id, caller_id=caller_id)
            except Exception:
                cursor = 0
        turn_complete_seen = False
        first_404_at = None
        return True

    for _attempt in range(100000):
        stream = None
        try:
            attention_result = _probe_attention()
            if attention_result and attention_result.get("settled"):
                boundary = int(attention_result.get("boundary_event_id") or 0)
                if boundary <= cursor:
                    return attention_result
            if attention_result and attention_result.get("identity", {}).get("successor_id"):
                return attention_result
            stream = client.stream_events(session_id, after=cursor, caller_id=caller_id)
            for evt in stream:
                now = time.monotonic()
                etype = evt.get("event", "")
                if etype == "_heartbeat":
                    if not turn_complete_seen and now - last_activity >= core._PROGRESS_INTERVAL:
                        sys.stdout.write(renderer.heartbeat_line(now - start))
                        sys.stdout.flush()
                        last_activity = now
                    if deadline and now > deadline:
                        print("\n[>] Timed out waiting for turn (remote still running)", file=sys.stderr)
                        return "timeout"
                    if _turn_settled(client, session_id, cursor):
                        if follow_handle and follow_successor_pending:
                            if _follow_successor(require_successor=True):
                                follow_successor_pending = False
                                break
                            core._report_unavailable_read_target(follow_handle)
                            return "error"
                        return "complete"
                    continue
                if etype == "tool_progress":
                    if not turn_complete_seen and now - last_activity >= core._PROGRESS_INTERVAL:
                        sys.stdout.write(renderer.tool_progress_line(evt.get("data", {})))
                        sys.stdout.flush()
                        last_activity = now
                    if deadline and now > deadline:
                        print("\n[>] Timed out waiting for turn (remote still running)", file=sys.stderr)
                        return "timeout"
                    if _turn_settled(client, session_id, cursor):
                        if follow_handle and follow_successor_pending:
                            if _follow_successor(require_successor=True):
                                follow_successor_pending = False
                                break
                            core._report_unavailable_read_target(follow_handle)
                            return "error"
                        return "complete"
                    continue

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
                if etype == "session_handoff":
                    data = evt.get("data", {})
                    rolled_to = data.get("rolled_to") or data.get("successor_id")
                    if follow_handle and isinstance(rolled_to, str) and rolled_to and rolled_to != session_id:
                        follow_successor_pending = True

                if new_id > cursor:
                    cursor = new_id
                    _ack(cursor)
                    first_404_at = None

                attention_result = _probe_attention()
                if attention_result and attention_result.get("settled"):
                    boundary = int(attention_result.get("boundary_event_id") or 0)
                    if boundary <= cursor:
                        return attention_result
                if attention_result and attention_result.get("identity", {}).get("successor_id"):
                    return attention_result

                if etype == "turn_complete":
                    turn_complete_seen = True
                    if _turn_settled(client, session_id, cursor):
                        if follow_handle and follow_successor_pending:
                            if _follow_successor(require_successor=True):
                                follow_successor_pending = False
                                break
                            core._report_unavailable_read_target(follow_handle)
                            return "error"
                        return "complete"
                if etype == "error":
                    return "error"
                if deadline and now > deadline:
                    print("\n[>] Timed out (remote still running)", file=sys.stderr)
                    return "timeout"
        except KeyboardInterrupt:
            print(f"\n[>] Interrupted -- delivered through event {cursor}", file=sys.stderr)
            return "interrupted"
        except BridgeConnectionError:
            first_404_at = None
            client.refresh_endpoint()
        except (OSError, urllib.error.URLError):
            first_404_at = None
            client.refresh_endpoint()
        except BridgeClientError as exc:
            if exc.status == 404:
                if follow_handle:
                    if _follow_successor(require_successor=follow_successor_pending):
                        follow_successor_pending = False
                        continue
                    core._report_unavailable_read_target(follow_handle)
                    return "error"
                now = time.monotonic()
                if first_404_at is None:
                    first_404_at = now
                if now - first_404_at < core._STREAM_404_GRACE_S:
                    client.refresh_endpoint()
                else:
                    print(
                        f"\n[RETRY] Session {session_id} is not currently registered (the bridge may be mid-restart); if it exists it is preserved and resumable -- re-run shortly.",
                        file=sys.stderr,
                    )
                    return "error"
        finally:
            close_stream = getattr(stream, "close", None)
            if close_stream is not None:
                close_stream()

        now = time.monotonic()
        if deadline and now > deadline:
            return "timeout"
        if _turn_settled(client, session_id, cursor):
            if follow_handle and follow_successor_pending:
                if _follow_successor(require_successor=True):
                    follow_successor_pending = False
                    continue
                core._report_unavailable_read_target(follow_handle)
                return "error"
            return "complete"
        time.sleep(core._RECONNECT_BACKOFF)
    return "gaveup"


def _cmd_wait(args: argparse.Namespace) -> None:
    core = _core()
    client = core._get_client()
    caller_id = core._caller_id_for(args)
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
    timeouts = core._phased_timeouts()
    renderer = core._make_renderer(args)
    _stream_feed(client, args.session_id, caller_id=caller_id, renderer=renderer, command_timeout=timeouts.command)


def _render_attention_result(result: dict[str, Any]) -> str:
    identity = result.get("identity") or {}
    current = identity.get("current_session_id") or identity.get("observed_session_id")
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


def _contract_changed_result(prior: dict[str, Any], successor_id: str) -> dict[str, Any]:
    identity = dict(prior.get("identity") or {})
    identity["current_session_id"] = successor_id
    identity["successor_id"] = successor_id
    return {
        "settled": True,
        "reason": "contract_changed",
        "identity": identity,
        "position": prior.get("position"),
        "boundary_event_id": None,
        "reference": {"kind": "successor", "ref": successor_id, "availability": "available"},
        "limitations": ["the successor daemon explicitly rejected the selected attention protocol"],
    }


def _cmd_attention_wait(client, args: argparse.Namespace, caller_id: str | None, reasons: list[str]) -> None:
    import time

    core = _core()
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
    timeouts = core._phased_timeouts()
    deadline = time.monotonic() + timeouts.command if timeouts.command else None
    session_id = args.session_id
    position = getattr(args, "position", None)
    renderer = core._make_renderer(args)
    last_result: dict[str, Any] | None = None

    while True:
        remaining = deadline - time.monotonic() if deadline is not None else None
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
                time.sleep(core._RECONNECT_BACKOFF)
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
                    result = client.wait_for_attention(session_id, reasons=reasons, position=position, timeout_seconds=0)
                else:
                    return
        last_result = result
        successor_id = str((result.get("identity") or {}).get("successor_id") or "")
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
                time.sleep(core._RECONNECT_BACKOFF)
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
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    caller_id = core._caller_id_for(args)
    target = args.session_id
    renderer = core._make_renderer(args)
    session_id, follow_handle = core._resolve_read_target(client, target)
    if follow_handle and session_id is None:
        session_id = core._wait_for_worktree_read_target(client, follow_handle)
        if not session_id:
            core._report_unavailable_read_target(follow_handle)
            sys.exit(1)
    session_id = session_id or target

    rng = getattr(args, "range", None)
    evt = getattr(args, "event", None)
    tail = getattr(args, "tail", None)
    since = getattr(args, "since", None)
    if rng or evt is not None or tail is not None or since is not None:
        if evt is not None:
            start_id, end_id = evt, evt
        elif tail is not None:
            try:
                head = client.get_cursor_info(session_id, caller_id=caller_id).get("head_id", 0)
            except BridgeClientError as exc:
                print(f"[FAIL] {exc.detail}", file=sys.stderr)
                sys.exit(1)
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
        try:
            events = client.read_range(session_id, start=start_id, end=end_id)
        except BridgeClientError as exc:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            core._json_out({"session_id": session_id, "events": events})
            return
        out = renderer.render_events(events)
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
        if not out:
            print("(no events in range)")
        return

    if getattr(args, "no_follow", False):
        try:
            start_id = client.get_cursor(session_id, caller_id=caller_id)
            events = client.read_range(session_id, start=start_id + 1)
        except BridgeClientError as exc:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            core._json_out({"session_id": session_id, "events": events})
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

    timeouts = core._phased_timeouts()
    _stream_feed(
        client,
        session_id,
        caller_id=caller_id,
        renderer=renderer,
        command_timeout=timeouts.command,
        follow_handle=follow_handle,
    )


def _cmd_result(args: argparse.Namespace) -> None:
    from .client import BridgeClientError
    from .protocol import REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION

    core = _core()
    client = core._get_client(ensure=False)
    try:
        represented = client.daemon_supports(REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION) and bool(
            client.resolve_live_result_target(args.session_ref)
        )
        if args.expand:
            payload = (
                client.expand_live_result_ref(args.session_ref, args.expand)
                if represented
                else client.expand_result_ref(args.session_ref, args.expand)
            )
        else:
            method = client.get_live_result_snapshot if represented else client.get_result_snapshot
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
        core._json_out(payload)
        return

    identity = payload.get("identity") or {}
    state = payload.get("state") or {}
    fidelity = payload.get("fidelity") or {}
    latest = payload.get("latest_result") or {}
    incremental = payload.get("incremental") or {}
    print(f"  {identity.get('logical_delegate_id', args.session_ref)}  [{state.get('session_status', '')}]  fidelity={fidelity.get('level', 'unknown')}")
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
        print(f"    Attention: {attention.get('availability')} ({attention.get('reason') or 'evidence unavailable'})")
    active = state.get("active_work") or {}
    if active.get("value"):
        print(f"    Active:    {json.dumps(active['value'], ensure_ascii=False)}")
    pending = state.get("pending_input") or {}
    if pending.get("availability") != "available":
        print(f"    Input:     {pending.get('availability')} ({pending.get('reason') or 'evidence unavailable'})")
    elif pending.get("value"):
        print(f"    Input:     {json.dumps(pending['value'], ensure_ascii=False)}")

    print(f"    Latest:    {latest.get('availability', 'unknown')}")
    latest_value = latest.get("value") or {}
    if latest_value:
        stop = latest_value.get("stop_reason")
        print(f"      turn {latest_value.get('turn_index')}" + (f" ({stop})" if stop else ""))
        text = latest_value.get("text")
        if text:
            for line in str(text).splitlines() or [str(text)]:
                print(f"      {line}")
    if latest.get("detail_ref"):
        print(f"      expand: agent-bridge result {args.session_ref} --expand {latest['detail_ref']}")

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


def register_session_streaming_commands(sub: argparse._SubParsersAction) -> None:
    core = _core()
    wait_p = sub.add_parser("wait", help="Wait for current turn to complete")
    wait_p.add_argument("session_id", help="Session ID")
    wait_p.add_argument("--attention", action="append", choices=["turn_complete", "turn_cancelled", "failed", "input_required", "permission_required", "unreachable", "policy_required", "contract_changed", "stopped", "ended"], help="Settle on this attention reason (repeatable); omitted keeps the legacy turn-only wait")
    wait_p.add_argument("--all-attention", action="store_true", help="Settle on any stable attention reason")
    wait_p.add_argument("--position", help="Resume from an opaque cursor-neutral attention position")
    wait_p.add_argument("--json", action="store_true", help="Print one structured settlement without opening the delivery stream")
    core._add_stream_args(wait_p)
    wait_p.set_defaults(func=_cmd_wait)

    read_p = sub.add_parser("read", help="Read/resume a session's conversation from the delivery cursor")
    read_p.add_argument("session_id", help="Session ID or a live session's worktree handle")
    read_p.add_argument("--no-follow", action="store_true", help="Deliver everything pending since the cursor, then exit (do not wait for completion)")
    read_p.add_argument("--range", metavar="A:B", help="Random-access read of event ids A..B (inclusive). Does NOT move the delivery cursor.")
    read_p.add_argument("--event", type=int, metavar="N", help="Random-access read of a single event id N. Does NOT move the delivery cursor.")
    read_p.add_argument("--tail", type=int, metavar="N", help="Random-access read of the last N events. Does NOT move the delivery cursor.")
    read_p.add_argument("--since", type=int, metavar="ID", help="Random-access read of events after event id ID (incremental only-new). Does NOT move the delivery cursor.")
    core._add_stream_args(read_p)
    read_p.set_defaults(func=_cmd_read)

    result_p = sub.add_parser("result", help="Read a bounded delegated-result snapshot without moving a cursor")
    result_p.add_argument("session_ref", help="Session ID, ACP session ID, or authoritative worktree handle")
    result_p.add_argument("--position", help="Opaque position returned by a prior result read (incremental mode)")
    result_p.add_argument("--max-items", type=int, default=None, help="Maximum projected work items (server default: 20, maximum: 100)")
    result_p.add_argument("--max-text-chars", type=int, default=None, help="Maximum caller-content characters (server default: 6000)")
    result_p.add_argument("--expand", metavar="REF", help="Resolve an opaque event or turn detail reference")
    result_p.add_argument("--json", action="store_true", help="Emit JSON")
    result_p.set_defaults(func=_cmd_result)
