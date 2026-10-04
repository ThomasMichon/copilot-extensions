"""Opaque, versioned result position and detail-reference tokens.

Split out of ``result_snapshot`` (module-size cap): encoding, decoding and the
session/kind checks every delegated-result token goes through.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

_MAX_DETAIL_TOKEN_CHARS = 2048
_TOKEN_PREFIX = "abr1."


class ResultTokenError(ValueError):
    """An opaque result position or detail reference is malformed."""


class ResultHistoryChangedError(ResultTokenError):
    """A valid detail reference names a replaced event-log history."""



def _encode_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"v": 1, **payload}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return _TOKEN_PREFIX + encoded


def _decode_token(
    token: str,
    *,
    source: str,
    session_id: str,
    kinds: frozenset[str],
    retired_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """``retired_ids``: earlier ids of the same session (a resume renamed it);
    their tokens stay valid, still subject to the history-continuity checks."""
    if not token or len(token) > _MAX_DETAIL_TOKEN_CHARS or not token.startswith(
        _TOKEN_PREFIX
    ):
        raise ResultTokenError("invalid result token")
    encoded = token[len(_TOKEN_PREFIX):]
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.b64decode(
            encoded + padding, altchars=b"-_", validate=True
        )
        value = json.loads(raw.decode("utf-8"))
    except (
        binascii.Error,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ResultTokenError("invalid result token") from exc
    if not isinstance(value, dict) or value.get("v") != 1:
        raise ResultTokenError("unsupported result token version")
    token_session = value.get("session_id")
    if value.get("source") != source or not isinstance(token_session, str) or (
        token_session != session_id and token_session not in retired_ids
    ):
        raise ResultTokenError("result token targets a different session")
    if value.get("kind") not in kinds:
        raise ResultTokenError("result token has the wrong kind")
    return value


def _position_token(
    source: str, session_id: str, continuity: str, event_id: int
) -> str:
    return _encode_token(
        {
            "kind": "position",
            "source": source,
            "session_id": session_id,
            "continuity": continuity,
            "event_id": event_id,
        }
    )


def _event_ref(
    source: str, session_id: str, continuity: str, event_id: int
) -> str:
    return _encode_token(
        {
            "kind": "event",
            "source": source,
            "session_id": session_id,
            "continuity": continuity,
            "event_id": event_id,
        }
    )


def _span_ref(
    source: str,
    session_id: str,
    continuity: str,
    start_event_id: int,
    end_event_id: int,
    *,
    scope: str | None = None,
) -> str:
    payload = {
        "kind": "span",
        "source": source,
        "session_id": session_id,
        "continuity": continuity,
        "start_event_id": start_event_id,
        "end_event_id": end_event_id,
    }
    if scope:
        payload["scope"] = scope
    return _encode_token(payload)


def _turn_ref(session_id: str, turn_index: int) -> str:
    return _encode_token(
        {
            "kind": "turn",
            "source": "owned",
            "session_id": session_id,
            "turn_index": turn_index,
        }
    )


def retarget(token: str | None, merged: dict[str, tuple[str, dict[int, int]]]) -> str | None:
    """Rewrite a token minted on a log that was since merged into another
    (``merged``: old continuity -> (new continuity, old event id -> new id)),
    so its event ids and continuity name the merged history. Anything else --
    including a malformed token -- is returned unchanged for normal validation."""
    if (not token or not merged or len(token) > _MAX_DETAIL_TOKEN_CHARS
            or not token.startswith(_TOKEN_PREFIX)):
        return token
    encoded = token[len(_TOKEN_PREFIX):]
    try:
        value = json.loads(base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError):
        return token
    if not isinstance(value, dict) or value.get("v") != 1:
        return token  # unsupported/missing version: leave it for validation to reject
    continuity_id = value.get("continuity")
    # Only a string can name a log; anything else (e.g. [] or {}) is left for
    # the routes' own token validation rather than raising TypeError here.
    if not isinstance(continuity_id, str) or continuity_id not in merged:
        return token
    if value.get("kind") == "position" and not _is_id(value.get("event_id")):
        # Only an exact integer can be mapped; "1", 1.0 or true keep their old
        # continuity so validation reports the replaced history instead.
        return token
    # Follow the merge chain (C->B->A), translating at every step; a cycle stops it.
    seen: set[str] = set()
    while isinstance(continuity_id, str) and continuity_id in merged and continuity_id not in seen:
        seen.add(continuity_id)
        continuity_id, ids = merged[continuity_id]
        value = {**value, "continuity": continuity_id}
        if value.get("kind") == "position":
            # A read cursor: the furthest merged id at or before it (never moves back).
            value["event_id"] = max([v for k, v in ids.items() if k <= value["event_id"]],
                                    default=0)
        elif not _retarget_detail(value, ids):
            return token  # unmapped or no longer contiguous: normal validation reports it
    value.pop("v", None)
    return _encode_token(value)


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _retarget_detail(value: dict[str, Any], ids: dict[int, int]) -> bool:
    """Map an event/span reference to the exact merged events, in place. A span
    is kept only while its members are still one contiguous run in order (a
    merge that dropped duplicates can interleave other events). False when the
    reference can't be mapped exactly -- including any non-integer id."""
    exact = {k: v for k, v in ids.items() if k > 0}
    if value.get("kind") != "span":
        if not _is_id(value.get("event_id")) or value["event_id"] not in exact:
            return False
        value["event_id"] = exact[value["event_id"]]
        return True
    start, end = value.get("start_event_id"), value.get("end_event_id")
    if not (_is_id(start) and _is_id(end)):
        return False
    # Client-controlled bounds: never iterate wider than the mappings.
    if not 0 < start <= end or end - start + 1 > len(exact):
        return False
    members = [exact.get(k) for k in range(start, end + 1)]
    if not members or None in members or members != list(
        range(members[0], members[0] + len(members))
    ):
        return False
    value["start_event_id"], value["end_event_id"] = members[0], members[-1]
    return True
