"""Bounded conversation-tail access for Copilot session ``events.jsonl``."""

from __future__ import annotations

import json
from collections import deque

from . import sessions

_OFFER_HINTS = (
    "would you like",
    "want me to",
    "if you want me to",
    "i can continue",
    "i can keep going",
    "happy to continue",
    "let me know if you want",
    "say the word",
)


def _iter_session_events(session_id: str):
    """Yield parsed events recorded for a single Copilot session."""
    valid_session_id = sessions.validate_session_id(session_id)
    if valid_session_id is None:
        return
    session_dir = sessions._session_state_dir()
    events_file = session_dir / valid_session_id / "events.jsonl"
    if not events_file.is_file():
        return

    try:
        with open(events_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except OSError:
        return


def _event_data(ev: dict) -> dict:
    """Typed access to an event's ``data`` dict."""
    data = ev.get("data")
    return data if isinstance(data, dict) else {}


def _looks_like_offer(text: str) -> bool:
    """Best-effort signal that an assistant close offered more work."""
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False
    if "?" in normalized and (
        "would you like" in normalized
        or "want me to" in normalized
        or "if you want" in normalized
    ):
        return True
    return any(hint in normalized for hint in _OFFER_HINTS)


def session_message_tail(session_id: str, *, limit: int = 3) -> dict:
    """Return the last *limit* message-bearing turns for one session as JSON."""
    lim = max(1, int(limit))
    tail: deque[dict] = deque(maxlen=lim)
    assistant_turns: dict[str, dict] = {}
    assistant_seq = 0
    anonymous_turn_key: str | None = None
    last_event_type: str | None = None
    last_event_timestamp: str = ""

    def _assistant_key(turn_id: object, *, create: bool, standalone: bool = False) -> str | None:
        nonlocal assistant_seq, anonymous_turn_key
        if isinstance(turn_id, str) and turn_id.strip():
            return turn_id
        if anonymous_turn_key is None and create:
            assistant_seq += 1
            key = f"assistant:{assistant_seq}"
            if not standalone:
                anonymous_turn_key = key
            return key
        return anonymous_turn_key

    def _ensure_assistant_turn(turn_id: object, timestamp: str, *, standalone: bool = False):
        key = _assistant_key(turn_id, create=True, standalone=standalone)
        assert key is not None
        turn = assistant_turns.get(key)
        if turn is None:
            turn = {
                "role": "assistant",
                "text_parts": [],
                "timestamp": timestamp,
                "tool_names": [],
                "tool_seen": set(),
                "closed": False,
                "materialized": False,
                "ordinal": len(assistant_turns),
            }
            assistant_turns[key] = turn
        elif timestamp and not turn["timestamp"]:
            turn["timestamp"] = timestamp
        return key, turn

    for ev in _iter_session_events(session_id):
        typ = str(ev.get("type", ""))
        data = _event_data(ev)
        ts = str(ev.get("timestamp", ""))
        if typ:
            last_event_type = typ
            last_event_timestamp = ts

        if typ == "user.message":
            text = sessions._event_text(ev)
            if text:
                tail.append(
                    {
                        "role": "user",
                        "text": text,
                        "timestamp": ts,
                        "tool_names": [],
                    }
                )
            continue

        if typ == "assistant.turn_start":
            _ensure_assistant_turn(data.get("turnId"), ts)
            continue

        if typ == "assistant.message":
            standalone = anonymous_turn_key is None and not (
                isinstance(data.get("turnId"), str) and data.get("turnId").strip()
            )
            key, turn = _ensure_assistant_turn(data.get("turnId"), ts, standalone=standalone)
            text = sessions._event_text(ev)
            if text:
                parts = turn["text_parts"]
                if not parts or parts[-1] != text:
                    parts.append(text)
                if not turn["materialized"]:
                    tail.append(turn)
                    turn["materialized"] = True
            if standalone:
                turn["closed"] = True
                assistant_turns.pop(key, None)
            continue

        if typ == "tool.execution_start":
            _, turn = _ensure_assistant_turn(data.get("turnId"), ts)
            name = data.get("toolName") or data.get("tool_name")
            if isinstance(name, str) and name and name not in turn["tool_seen"]:
                turn["tool_names"].append(name)
                turn["tool_seen"].add(name)
            continue

        if typ == "assistant.turn_end":
            key = _assistant_key(data.get("turnId"), create=False)
            turn = assistant_turns.get(key)
            if turn is not None:
                turn["closed"] = True
                assistant_turns.pop(key, None)
            if key is not None and key == anonymous_turn_key:
                anonymous_turn_key = None

    turns: list[dict] = []
    for turn in tail:
        if turn["role"] == "assistant":
            text = "\n\n".join(turn["text_parts"]).strip()
            if not text:
                continue
            turns.append(
                {
                    "role": "assistant",
                    "text": text,
                    "timestamp": str(turn["timestamp"]),
                    "tool_names": list(turn["tool_names"]),
                }
            )
            continue
        turns.append(turn)

    open_turn = next((turn for turn in assistant_turns.values() if not turn["closed"]), None)

    ending_state = "complete"
    if open_turn is not None:
        ending_state = "assistant_turn_in_progress"
    elif turns and turns[-1]["role"] == "assistant" and _looks_like_offer(turns[-1]["text"]):
        ending_state = "assistant_offer_pending"

    return {
        "session_id": session_id,
        "turns": turns,
        "count": len(turns),
        "ending": {
            "state": ending_state,
            "cut_off_mid_turn": ending_state == "assistant_turn_in_progress",
            "ended_on_unanswered_offer": ending_state == "assistant_offer_pending",
            "last_event_type": last_event_type,
            "last_event_timestamp": last_event_timestamp,
        },
    }
