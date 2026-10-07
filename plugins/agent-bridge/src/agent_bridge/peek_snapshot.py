"""Copilot-free session **peek**: distill a target's ``events.jsonl`` to a snapshot.

The "is the most-recent head session on this target worth reusing?" primitive.
Instead of launching ``copilot --acp`` (which can stall on the ACP resume race,
dotfiles#1422), we read the Copilot CLI's own per-session transcript
(``~/.copilot/session-state/<acp_session_id>/events.jsonl``) directly and distill
a compact, ingestible snapshot: lifecycle/health, the recent message tail, a
high-level tool-call summary, usage, and a coarse reuse-worthiness verdict.

Transport-agnostic: this module only builds the remote driver command + parses
its result. The actual exec (local subprocess vs. ``agent-codespaces ssh
--remote-cmd`` for a codespace) is the caller's job via the transport-exec seam,
mirroring how ``ai_plugin_staging`` ships a stdlib driver and reads a marker line.

``events.jsonl`` schema (one JSON object per line), verified 2026-08-13:
``{type, data, id, timestamp, parentId}`` with ``type`` in {``session.start``,
``session.resume``, ``session.model_change``, ``system.message``,
``user.message``, ``assistant.turn_start``, ``assistant.message``,
``assistant.turn_end``, ``session.usage_checkpoint``, ``session.shutdown``}.
Files range ~66 KB–2.2 MB, so the driver reads only the **tail**.

**Presence** (``snapshot["presence"]``): what the session is doing now, read from
the transcript since its last ``session.start``/``session.resume`` -- never from
self-reported activity. ``busy`` when the last presence-bearing event is
``user.message``, ``assistant.turn_start``, ``tool.execution_start``/``complete``
or ``permission.completed`` (or is a sub-agent's ``assistant.turn_end``, carrying
``data.agentId``: the parent turn is still taking in its result); ``awaiting_input`` while a ``permission.requested``
is unanswered (paired by ``requestId``), or when a turn ended
(``assistant.turn_end``) in an interactive session; ``idle`` when a turn ended in
an autopilot (or headless) session -- the mode is the latest of
``session.mode_changed``, and ``agentMode`` on ``user.message`` /
``permission.requested``, defaulting to interactive; ``absent`` after
``session.shutdown``; ``unknown`` when no such event follows the boundary, when
no boundary is found at all (a truncated transcript) or within the newest 64 MiB
(an older permission request or mode signal could be unread), or when the last
line is partial or malformed. The
transcript is read backward in blocks to that boundary, never cut at a fixed
tail. Every other event carries no presence signal.
Confidence is ``scanned``, except an ended turn with no mode signal since the
boundary, which is assumed interactive and marked ``heuristic``. Event names verified against Copilot CLI 1.0.92
transcripts.

**Usage** (``snapshot["usage"]``): Copilot's ``totalPremiumRequests`` /
``totalNanoAiu`` are cumulative over the session's life (across resumes), so each
figure is the newest ``session.usage_checkpoint`` / ``session.shutdown`` that
reports it, read backward to the start of the transcript if need be (no cap), and
keeps its own provenance (``from``); token counts come only from a shutdown that is
the newest report. Unreported is ``None`` or ``{"reported": false}``, never zero.
"""

from __future__ import annotations

import base64
import json
import shlex

from agent_procutil import no_window_kwargs

# Marker the remote driver prefixes its single JSON result line with, so the
# caller can extract it from surrounding login-shell / hook noise.
RESULT_MARKER = "PEEK_JSON:"

# Default number of trailing events.jsonl lines the driver scans, and how many
# recent messages / tool calls to surface in the snapshot.
DEFAULT_TAIL_LINES = 400
DEFAULT_RECENT_MESSAGES = 8
DEFAULT_MESSAGE_CHARS = 400

# Remote stdlib driver. argv: [1]=session dir (…/session-state/<acp_session_id>),
# [2]=tail-lines, [3]=recent-messages, [4]=message-chars. agent-bridge names the
# CURRENT session's dir explicitly (it tracks the target's acp_session_id) -- no
# newest-by-mtime sweep across session-state. Emits exactly one ``RESULT_MARKER``
# line with the snapshot. Never throws to the channel: any failure yields
# ``{"ok": false, ...}``.
_DRIVER = r'''
import sys, os, json, collections

MARKER = "%(marker)s"

def emit(obj):
    # ASCII-escaped: survives any stdout encoding (a Windows child's is often cp1252).
    print(MARKER + json.dumps(obj))

def main():
    sdir = sys.argv[1]
    tail_lines = int(sys.argv[2])
    n_recent = int(sys.argv[3])
    msg_chars = int(sys.argv[4])

    ef = os.path.join(sdir, "events.jsonl")
    try:
        size = os.path.getsize(ef)
        mtime = os.path.getmtime(ef)
    except OSError:
        emit({"ok": False, "reason": "events.jsonl unreadable", "session_dir": sdir})
        return

    # Read only the tail: seek to a bounded window from the end, keep last N lines.
    window = min(size, 1_500_000)
    try:
        with open(ef, "rb") as fh:
            if size > window:
                fh.seek(size - window)
                fh.readline()  # drop the partial first line
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        emit({"ok": False, "reason": "events.jsonl read error", "session_dir": sdir})
        return

    all_lines = [ln for ln in raw.split("\n") if ln.strip()]  # JSONL: "\n" only, not U+2028 etc.
    lines = all_lines[-tail_lines:]
    evs = []
    last_line_bad = False
    for ln in lines:
        try:
            evs.append(json.loads(ln))
            last_line_bad = False
        except Exception:
            last_line_bad = True

    types = collections.Counter(e.get("type", "?") for e in evs)
    started = resumed = last_shutdown = None
    model = None
    last_ts = None
    recent = []
    tools = []
    for e in evs:
        t = e.get("type"); d = e.get("data") or {}; ts = e.get("timestamp")
        if ts:
            last_ts = ts
        if t == "session.start":
            started = ts
        elif t == "session.resume":
            resumed = ts
        elif t == "session.model_change":
            model = d.get("modelId") or d.get("model") or d.get("id") or d.get("name") or model
        elif t == "session.shutdown":
            last_shutdown = {"at": ts, "type": d.get("shutdownType")}
        elif t == "session.usage_checkpoint":
            if not model:
                mcs = d.get("modelCacheState")
                if isinstance(mcs, list) and mcs and isinstance(mcs[0], dict):
                    model = mcs[0].get("modelId") or model
        elif t in ("user.message", "assistant.message"):
            txt = d.get("text")
            if txt is None:
                c = d.get("content")
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    parts = [p.get("text", "") for p in c if isinstance(p, dict)]
                    txt = "".join(parts)
            txt = (txt or "").strip()
            if txt:
                role = "user" if t == "user.message" else "assistant"
                recent.append({"role": role, "at": ts,
                               "text": txt[:msg_chars] + ("..." if len(txt) > msg_chars else "")})
        elif t in ("tool_call", "assistant.tool_call", "tool.call") or "tool" in (t or ""):
            title = d.get("title") or d.get("name") or d.get("tool") or ""
            kind = d.get("kind") or d.get("status") or ""
            if title or kind:
                tools.append({"title": str(title)[:120], "kind": str(kind)[:40]})

    turns = types.get("user.message", 0)
    clean = bool(last_shutdown and last_shutdown.get("type") == "routine")
    # A session whose last lifecycle mark is a resume with no subsequent clean
    # shutdown, or that ends mid-turn, is a stall/cold-reuse risk.
    resume_after_shutdown = bool(resumed and (not last_shutdown or (last_shutdown.get("at") or "") < (resumed or "")))

    emit({
        "ok": True,
        "session_dir": sdir,
        "acp_session_id": os.path.basename(sdir),
        "events_file": ef,
        "size_bytes": size,
        "mtime": mtime,
        "last_activity_at": last_ts,
        "model": model,
        "type_counts": dict(types),
        "turns": turns,
        "lifecycle": {"started_at": started, "resumed_at": resumed,
                      "last_shutdown": last_shutdown, "clean_shutdown": clean,
                      "resume_without_clean_shutdown": resume_after_shutdown},
        "recent_messages": recent[-n_recent:],
        "recent_tool_calls": tools[-n_recent:],
        "usage": usage_of(ef, size),
        "presence": presence_of(ef, size, last_line_bad),
    })


# Presence from the transcript (see the module docstring's "Presence" section).
BUSY = ("user.message", "assistant.turn_start", "tool.execution_start",
        "tool.execution_complete", "permission.completed")
SETTLED = ("assistant.turn_end",)
BOUNDARY = ("session.start", "session.resume")

RELEVANT = BUSY + SETTLED + BOUNDARY + ("session.shutdown", "permission.requested",
                                       "session.mode_changed")

SCAN_BLOCK = 1 << 20
# How far back to look for the session boundary (overridable for tests).
SCAN_CAP = int(os.environ.get("AGENT_BRIDGE_PRESENCE_SCAN_CAP", 64 << 20))

def _lines_back(ef, size, cap=None):
    """The transcript's lines, newest first, read backward in blocks (at most *cap*
    bytes, :data:`SCAN_CAP` by default)."""
    cap = SCAN_CAP if cap is None else cap
    pos, carry, scanned = size, b"", 0
    with open(ef, "rb") as fh:
        while pos > 0 and scanned < cap:
            n = min(SCAN_BLOCK, pos, cap - scanned)
            pos -= n
            fh.seek(pos)
            chunk = fh.read(n) + carry
            scanned += n
            parts = chunk.split(b"\n")
            carry = parts[0] if pos > 0 else b""
            for raw in reversed(parts[1:] if pos > 0 else parts):
                yield raw.decode("utf-8", "replace")

def _events_back(ef, size, types, cap=None):
    """Events of *types*, newest first. A cheap substring prefilter, then the parsed
    ``type`` decides -- an event name appearing as a payload value is not that event."""
    for ln in _lines_back(ef, size, cap):
        if not any('"' + t + '"' in ln for t in types):
            continue
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if isinstance(e, dict) and e.get("type") in types:
            yield e

def scan_back(ef, size):
    """Presence-relevant events from the end of the transcript back to its latest
    session boundary (newest last), read backward in blocks so a long session's
    unanswered permission request or mode signal is never cut off by a fixed tail.
    A cheap substring prefilter, then the parsed ``type`` decides -- an event name
    appearing as a payload value is not that event. Returns ``(events, complete)``;
    *complete* is True only when a boundary was found: with none (a truncated file,
    or none within :data:`SCAN_CAP` bytes) the events since the session started are
    not all known, and presence is ``unknown`` whatever the file's size."""
    out = []
    for e in _events_back(ef, size, RELEVANT):
        out.append(e)
        if e.get("type") in BOUNDARY:
            out.reverse()
            return out, True
    out.reverse()
    return out, False

USAGE = ("session.usage_checkpoint", "session.shutdown")
TOKENS = ("input", "output", "cache_read", "cache_write")

def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

def usage_of(ef, size):
    """What the session reported spending so far. Copilot's totals are cumulative over
    the session's whole life (they carry across resumes), so each figure is the newest
    ``session.usage_checkpoint`` / ``session.shutdown`` that reports it -- read back to
    the start of the transcript if need be (no cap: a long stretch with no report must
    not read as none), stopping once every figure is found. Each figure keeps its own
    provenance (``from``: when and which kind of report). A figure no event reports is
    ``None`` (not reported), never zero; token counts come only from a shutdown that is
    itself the newest report."""
    newest, found, origin = None, {}, {}
    for e in _events_back(ef, size, USAGE, cap=size):
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        kind = "shutdown" if e.get("type") == "session.shutdown" else "checkpoint"
        if newest is None:
            newest, details = e, d.get("tokenDetails")
        for key, field in (("premium_requests", "totalPremiumRequests"), ("nano_aiu", "totalNanoAiu")):
            if key not in found and _num(d.get(field)) is not None:
                found[key] = _num(d.get(field))
                origin[key] = {"at": e.get("timestamp"), "source": kind}
        if len(found) == 2:
            break
    if newest is None:
        return {"reported": False, "reason": "no usage checkpoint or shutdown in the transcript"}
    shutdown = newest.get("type") == "session.shutdown"
    tokens = {k: _num((details.get(k) or {}).get("tokenCount") if isinstance(details.get(k), dict) else None)
              for k in TOKENS} if shutdown and isinstance(details, dict) else None
    if tokens is not None:
        origin["tokens"] = {"at": newest.get("timestamp"), "source": "shutdown"}
    return {"reported": True, "reported_at": newest.get("timestamp"),
            "source": "shutdown" if shutdown else "checkpoint",
            "premium_requests": found.get("premium_requests"), "nano_aiu": found.get("nano_aiu"),
            "tokens": tokens, "from": origin}

def presence_of(ef, size, last_line_bad):
    evs, complete = scan_back(ef, size)
    return presence(evs, last_line_bad, complete)

def presence(evs, last_line_bad, complete=True):
    def out(state, reason, last=None, mode=None, pending=0, confidence="scanned"):
        return {"state": state, "confidence": confidence, "reason": reason,
                "last_event": (last or {}).get("type"), "last_event_at": (last or {}).get("timestamp"),
                "mode": mode, "pending_permissions": pending}
    if last_line_bad:
        return out("unknown", "the transcript's last line is partial or malformed")
    if not complete:  # an older permission request or mode signal may be unread
        return out("unknown", "no session start or resume found in the transcript (read back up "
                   "to %%d bytes)" %% SCAN_CAP)
    start = 0
    for i, e in enumerate(evs):
        if e.get("type") in BOUNDARY:
            start = i
    mode = None
    pending = {}
    last = None
    busy = False
    for e in evs[start:]:
        t = e.get("type"); d = e.get("data") or {}
        if t == "session.mode_changed" and d.get("newMode"):
            mode = d.get("newMode")
        elif t in ("user.message", "permission.requested") and d.get("agentMode"):
            mode = d.get("agentMode")
        if t == "permission.requested":
            pending[d.get("requestId") or e.get("id")] = e
        elif t == "permission.completed":
            pending.pop(d.get("requestId"), None)
        if t in BUSY or t in SETTLED or t == "session.shutdown" or t == "permission.requested":
            last = e
            # A sub-agent's turn end (``data.agentId``) doesn't settle the session: the
            # parent turn is still taking in its result.
            busy = t in BUSY or (t in SETTLED and bool(isinstance(d, dict) and d.get("agentId")))
    if last is None:
        return out("unknown", "no presence-bearing event since the session started", mode=mode)
    kind = last.get("type")
    if kind == "session.shutdown":
        return out("absent", "the session shut down", last, mode)
    if pending:
        return out("awaiting_input", "a permission request is unanswered", last, mode, len(pending))
    if busy:
        return out("busy", "mid-turn", last, mode)
    if mode in ("autopilot", "headless"):
        return out("idle", "the turn ended (autopilot: nothing is asked of a human)", last, mode)
    if mode is None:
        return out("awaiting_input", "the turn ended; no mode signal, so assumed interactive", last,
                   "interactive", confidence="heuristic")
    return out("awaiting_input", "the turn ended and the session is interactive", last, mode)

try:
    main()
except Exception as exc:  # never break the channel
    print(MARKER + json.dumps({"ok": False, "reason": "driver error: %%s" %% exc}))
''' % {"marker": RESULT_MARKER}


def default_session_state_root() -> str:
    """The Copilot CLI per-session transcript root on the *target*.

    ``$HOME`` is resolved on the target by the shell, so this returns a shell
    expression, not a host-resolved path.
    """
    return "$HOME/.copilot/session-state"


def build_peek_command(
    acp_session_id: str,
    *,
    session_state_root: str | None = None,
    tail_lines: int = DEFAULT_TAIL_LINES,
    recent_messages: int = DEFAULT_RECENT_MESSAGES,
    message_chars: int = DEFAULT_MESSAGE_CHARS,
) -> str:
    """Bash to base64-ship + run the distiller against the CURRENT session's dir.

    ``acp_session_id`` is the Copilot session id agent-bridge tracks for the
    target (``sessions.acp_session_id``); its transcript lives at
    ``<session_state_root>/<acp_session_id>/events.jsonl`` on the target. No
    newest-by-mtime sweep -- the caller names the current session explicitly.

    Robust to login-shell noise and quoting: the driver is base64-encoded and
    decoded on the target, run under ``python3`` (falling back to ``python``).
    Never aborts the channel: on any failure it emits an empty result marker.
    """
    if not acp_session_id or any(
        ch not in "abcdefABCDEF0123456789-._" for ch in acp_session_id
    ):
        raise ValueError(f"implausible acp_session_id: {acp_session_id!r}")
    root = session_state_root or default_session_state_root()
    # root may contain ``$HOME`` -> keep it inside double quotes so the target
    # shell expands it; acp_session_id is validated above.
    session_dir = f'{root}/{acp_session_id}'
    b64 = base64.b64encode(_DRIVER.encode("utf-8")).decode("ascii")
    empty = f'{RESULT_MARKER}{{"ok": false, "reason": "no python or driver failed"}}'
    return (
        f'PY=$(command -v python3 || command -v python); '
        f'if [ -n "$PY" ]; then '
        f'printf %s {shlex.quote(b64)} | base64 -d | "$PY" - "{session_dir}" '
        f'{int(tail_lines)} {int(recent_messages)} {int(message_chars)} '
        f'2>/dev/null || echo {shlex.quote(empty)}; '
        f'else echo {shlex.quote(empty)}; fi'
    )


def parse_peek_result(output: str) -> dict:
    """Extract the last ``RESULT_MARKER`` JSON line -> snapshot dict.

    Fail-safe: returns ``{"ok": False, "reason": ...}`` when no valid marker
    line is present (login-shell noise, driver crash, empty output).
    """
    snap: dict = {"ok": False, "reason": "no PEEK_JSON marker in output"}
    for line in (output or "").splitlines():
        idx = line.find(RESULT_MARKER)
        if idx < 0:
            continue
        payload = line[idx + len(RESULT_MARKER):].strip()
        try:
            snap = json.loads(payload)
        except Exception:
            continue
    return snap


def snapshot_local(
    acp_session_id: str,
    *,
    session_state_root: str | None = None,
    tail_lines: int = DEFAULT_TAIL_LINES,
    recent_messages: int = DEFAULT_RECENT_MESSAGES,
    message_chars: int = DEFAULT_MESSAGE_CHARS,
) -> dict:
    """Distill a **local** target's current-session transcript, in-process.

    Runs the exact same ``_DRIVER`` (single source of truth) via this
    interpreter against ``<root>/<acp_session_id>/events.jsonl`` on *this* host --
    no shell, so it is cross-platform (the codespace path goes through
    ``build_peek_command`` + the transport seam instead). Fail-safe -> a
    ``{"ok": False}`` snapshot.
    """
    import os
    import subprocess
    import sys

    if not acp_session_id or any(
        ch not in "abcdefABCDEF0123456789-._" for ch in acp_session_id
    ):
        return {"ok": False, "reason": f"implausible acp_session_id: {acp_session_id!r}"}
    root = session_state_root or os.path.expanduser("~/.copilot/session-state")
    session_dir = os.path.join(root, acp_session_id)
    try:
        proc = subprocess.run(
            [sys.executable, "-", session_dir, str(int(tail_lines)),
             str(int(recent_messages)), str(int(message_chars))],
            input=_DRIVER, capture_output=True, text=True, timeout=30,
            **no_window_kwargs(),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "reason": f"local driver failed: {exc}"}
    return parse_peek_result(proc.stdout)


def reuse_verdict(snap: dict, *, stale_after_seconds: float = 6 * 3600) -> tuple[str, str]:
    """Coarse reuse-worthiness verdict from a snapshot -> ``(verdict, reason)``.

    verdict in {``reusable``, ``cold``, ``risky``, ``none``}. Advisory only --
    the caller decides resume-vs-fresh. ``risky`` flags a session that resumed
    without a clean shutdown (the exact stall signature), so a caller can prefer
    end+create.
    """
    import time

    if not snap.get("ok"):
        return "none", snap.get("reason", "no session transcript")
    life = snap.get("lifecycle") or {}
    if life.get("resume_without_clean_shutdown"):
        return "risky", "resumed with no clean shutdown (possible stalled/wedged session)"
    mtime = snap.get("mtime")
    if isinstance(mtime, (int, float)) and (time.time() - mtime) > stale_after_seconds:
        return "cold", f"last activity {int((time.time()-mtime)//3600)}h ago"
    turns = snap.get("turns") or 0
    if turns <= 0:
        return "cold", "no user turns recorded"
    if life.get("clean_shutdown"):
        return "reusable", f"{turns} turn(s), clean shutdown, recent"
    return "reusable", f"{turns} turn(s), recent activity"
