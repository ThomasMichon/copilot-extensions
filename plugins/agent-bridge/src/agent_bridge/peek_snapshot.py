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
    print(MARKER + json.dumps(obj, ensure_ascii=False))

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

    lines = [ln for ln in raw.splitlines() if ln.strip()][-tail_lines:]
    evs = []
    for ln in lines:
        try:
            evs.append(json.loads(ln))
        except Exception:
            pass

    types = collections.Counter(e.get("type", "?") for e in evs)
    started = resumed = last_shutdown = None
    model = None
    last_ts = None
    recent = []
    tools = []
    usage = {}
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
            usage = {"premium_requests": d.get("totalPremiumRequests"),
                     "nano_aiu": d.get("totalNanoAiu")}
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
        "usage": usage,
    })

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
