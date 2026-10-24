"""Typed conversation relationships and bounded, non-consuming projections."""

from __future__ import annotations

import json
import re

from .store import ChatError, MAX_PAGE_BYTES, _identifier, _integer, _printable, _unicode, json_bytes

KINDS = {
    "notice", "question", "answer", "finding", "result", "proposal", "review",
    "decision", "resolve",
}
RELATIONS = {
    "answer": {"question"}, "review": {"finding", "result", "proposal"},
    "resolve": {"question", "proposal"},
}
MAX_INBOX_CANDIDATES = 100


def normalize(value):
    try:
        _unicode(value)
    except RecursionError:
        raise ChatError("invalid", "Invalid collaboration nesting.") from None
    if (type(value) is not dict
            or not {"schema", "version", "kind"} <= value.keys()
            or value.keys() - {
                "schema", "version", "kind", "to", "evidence", "reply_to", "supersedes",
            }
            or value["schema"] != "agent-chat.collaboration"
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["kind"]) is not str or value["kind"] not in KINDS):
        raise ChatError("invalid", "Invalid collaboration fields or version.")
    result = {**value, "to": value.get("to", []), "evidence": value.get("evidence", [])}
    for name, maximum in (("to", 16), ("evidence", 8)):
        items = result[name]
        if type(items) is not list or len(items) > maximum:
            raise ChatError("invalid", f"Invalid collaboration {name}.")
        for item in items:
            if name == "to":
                _identifier(item, "recipient")
            else:
                _printable(item, "evidence", 512)
        if name == "to" and len(items) != len(set(items)):
            raise ChatError("invalid", "Duplicate collaboration recipients.")
    for name in ("reply_to", "supersedes"):
        if name in result:
            _integer(result[name], name, 1, 2**63 - 1)
    if result["kind"] in RELATIONS and "reply_to" not in result:
        raise ChatError("invalid", "This collaboration kind requires reply_to.")
    if len(json_bytes(result)) > 2048:
        raise ChatError("invalid", "Collaboration metadata is too large.")
    return result


def validate_view(request):
    _integer(request.get("limit", 50), "limit", 1, 100)
    _integer(request.get("max_bytes", MAX_PAGE_BYTES), "max_bytes", 1024, MAX_PAGE_BYTES)
    _integer(request.get("after", 0), "after", 0, 2**63 - 1)
    if ("epoch" in request) != ("head" in request):
        raise ChatError("invalid", "A complete captured history boundary is required.")
    if "epoch" in request:
        if type(request["epoch"]) is not str or not re.fullmatch(r"[a-f0-9]{32}", request["epoch"]):
            raise ChatError("invalid", "Invalid captured epoch.")
        _integer(request["head"], "head", 0, 2**63 - 1)
    if request.get("after", 0) and "epoch" not in request:
        raise ChatError("invalid", "Continuation requires a captured history boundary.")
    if request["op"] == "thread":
        _integer(request["event_id"], "event_id", 1, 2**63 - 1)
    else:
        _integer(request.get("recent_limit", 10), "recent_limit", 0, 100)
        _integer(request.get("recent_after", 0), "recent_after", 0, 2**63 - 1)
        if request.get("recent_after", 0) and "epoch" not in request:
            raise ChatError("invalid", "Continuation requires a captured history boundary.")


def metadata(store, event_id):
    return store._db.execute(
        "SELECT * FROM collaborations WHERE event_id=?", (event_id,),
    ).fetchone()


def relationship(store, request, value):
    channel, codename = request["channel"], request["codename"]
    for recipient in value["to"]:
        if store._db.execute(
            "SELECT 1 FROM memberships WHERE channel=? AND codename=?", (channel, recipient),
        ).fetchone() is None:
            raise ChatError("invalid", "A recipient is not a recorded channel member.")
    reply = value.get("reply_to")
    root = reply_author = None
    if reply is not None:
        target = store._db.execute(
            "SELECT channel, codename FROM events WHERE id=?", (reply,),
        ).fetchone()
        relation = metadata(store, reply)
        if target is None or target["channel"] != channel:
            raise ChatError("invalid", "Reply target is not in this channel.")
        if value["kind"] in RELATIONS and (
            relation is None or relation["kind"] not in RELATIONS[value["kind"]]
        ):
            raise ChatError("invalid", "Reply target does not support this relation.")
        if value["kind"] == "resolve" and target["codename"] != codename:
            raise ChatError("invalid", "Only the recorded author can resolve this request.")
        root = relation["thread_root"] if relation else reply
        reply_author = target["codename"]
    if "supersedes" in value:
        previous = store._db.execute(
            """SELECT c.*, e.channel, e.codename FROM collaborations c
            JOIN events e ON e.id=c.event_id WHERE c.event_id=?""",
            (value["supersedes"],),
        ).fetchone()
        if (previous is None or previous["channel"] != channel
                or previous["codename"] != codename or previous["kind"] != value["kind"]
                or reply is not None and reply != previous["reply_to"]):
            raise ChatError("invalid", "Supersession must preserve author, kind and reply target.")
        reply, root = previous["reply_to"], previous["thread_root"]
        reply_author = previous["reply_author"]
    return reply, root, reply_author


def receipt(store, row, duplicate, value):
    result = {"event": store._event(row), "duplicate": duplicate}
    if value is not None:
        result.update(collaboration=value, thread_root=metadata(store, row["id"])["thread_root"])
    return result


SELECT_ENTRY = """
SELECT e.*, c.metadata AS collaboration_metadata, c.thread_root
FROM events e LEFT JOIN collaborations c ON c.event_id=e.id
"""

LIVE_QUESTION = """
NOT EXISTS (
    SELECT 1 FROM collaborations amendment
    WHERE amendment.supersedes=q.event_id AND amendment.event_id<=:head
) AND NOT EXISTS (
    SELECT 1 FROM collaborations resolution
    WHERE resolution.reply_to=q.event_id AND resolution.kind='resolve'
    AND resolution.event_id<=:head
)
"""

# Bound candidate examination before applying snapshot retirement filters.
PENDING = f"""
WITH directed AS (
    SELECT recipient.event_id, 'reply' AS attention, recipient.event_id AS question_id
    FROM collaboration_recipients recipient INDEXED BY collaboration_recipient_events
    WHERE recipient.channel=:channel AND recipient.codename=:viewer
    AND recipient.kind='question'
    AND recipient.event_id>:after AND recipient.event_id<=:head
    ORDER BY recipient.event_id LIMIT :scan_limit
), reviewed AS (
    SELECT answer.event_id, 'review' AS attention, answer.reply_to AS question_id
    FROM collaborations answer INDEXED BY collaborations_review
    WHERE answer.channel=:channel AND answer.reply_author=:viewer AND answer.kind='answer'
    AND answer.event_id>:after AND answer.event_id<=:head
    ORDER BY answer.event_id LIMIT :scan_limit
), pending AS (
    SELECT event_id, attention, question_id FROM directed
    UNION ALL SELECT event_id, attention, question_id FROM reviewed
    ORDER BY event_id LIMIT :scan_limit
)
SELECT p.event_id AS candidate_id, p.attention, e.*,
       c.metadata AS collaboration_metadata, c.thread_root
FROM pending p CROSS JOIN collaborations q ON q.event_id=p.question_id
LEFT JOIN events e ON e.id=p.event_id AND q.kind='question' AND {LIVE_QUESTION}
AND (p.attention='review' OR NOT EXISTS (
    SELECT 1 FROM collaborations answer INDEXED BY collaborations_answer
    WHERE answer.kind='answer' AND answer.reply_to=q.event_id
    AND answer.author_codename=:viewer AND answer.event_id<=:head
))
AND (p.attention='reply' OR NOT EXISTS (
    SELECT 1 FROM collaborations amendment
    WHERE amendment.supersedes=p.event_id AND amendment.event_id<=:head
))
LEFT JOIN collaborations c ON c.event_id=e.id
ORDER BY p.event_id
"""


def _entry(store, row, attention=None):
    return {
        "event": store._event(row),
        "collaboration": json.loads(row["collaboration_metadata"])
        if row["collaboration_metadata"] is not None else None,
        "thread_root": row["thread_root"] if row["thread_root"] is not None else row["id"],
        "attention": attention,
    }


def view(store, request):
    channel, viewer = request["channel"], request["codename"]
    store._member(channel, viewer)
    head = store._db.execute(
        "SELECT coalesce(max(id),0) FROM events WHERE channel=?", (channel,),
    ).fetchone()[0]
    if "epoch" in request:
        captured = request["head"]
        if (request["epoch"] != store.log_epoch or captured > head or captured and
                store._db.execute(
                    "SELECT 1 FROM events WHERE channel=? AND id=?", (channel, captured),
                ).fetchone() is None):
            raise ChatError("invalid", "Captured history boundary is no longer available.")
        head = captured
    after, recent_after = request.get("after", 0), request.get("recent_after", 0)
    if after > head or recent_after > head:
        raise ChatError("invalid", "Continuation is outside the captured boundary.")
    limit = request.get("limit", 50)
    controls = {
        "channel": channel, "viewer": viewer, "head": head, "after": after, "limit": limit + 1,
    }
    result = {
        "schema": "agent-chat.collaboration-view", "version": 1, "view": request["op"],
        "channel": channel, "codename": viewer, "epoch": store.log_epoch, "head": head,
        "entries": [], "recent": [], "count": 0, "more": False, "next": None,
    }
    recent = []
    pending_more, pending_after = False, after
    if request["op"] == "thread":
        target = store._db.execute(
            SELECT_ENTRY + " WHERE e.channel=? AND e.id=? AND e.id<=?",
            (channel, request["event_id"], head),
        ).fetchone()
        if target is None:
            raise ChatError("invalid", "Thread target is outside the captured channel history.")
        root = target["thread_root"] if target["thread_root"] is not None else target["id"]
        result["thread_root"] = root
        entries = store._db.execute(
            """WITH thread_ids AS (
                SELECT event_id AS id FROM collaborations
                WHERE thread_root=:root AND event_id>:after AND event_id<=:head
                UNION SELECT :root AS id WHERE :root>:after AND :root<=:head
                ORDER BY id LIMIT :limit
            ) """ + SELECT_ENTRY + """
            WHERE e.id IN (SELECT id FROM thread_ids) ORDER BY e.id""",
            {"root": root, "after": after, "head": head, "limit": limit + 1},
        ).fetchall()
    else:
        candidates = store._db.execute(
            PENDING, {**controls, "scan_limit": MAX_INBOX_CANDIDATES + 1},
        ).fetchall()
        pending_more = len(candidates) > MAX_INBOX_CANDIDATES
        examined = candidates[:MAX_INBOX_CANDIDATES]
        pending_after = examined[-1]["candidate_id"] if pending_more else head
        entries = [row for row in examined if row["id"] is not None]
        recent = store._db.execute(
            SELECT_ENTRY + """
            WHERE e.id IN (SELECT id FROM events WHERE channel=? AND id<=?
                          ORDER BY id DESC LIMIT ?)
            AND e.id>? ORDER BY e.id LIMIT ?""",
            (channel, head, request.get("recent_limit", 10), recent_after, limit + 1),
        ).fetchall()
    remaining = len(entries) + len(recent)
    for name, rows in (("entries", entries), ("recent", recent)):
        for row in rows:
            if len(result["entries"]) + len(result["recent"]) >= limit:
                break
            item = _entry(store, row, row["attention"] if "attention" in row.keys() else None)
            result[name].append(item)
            # Reserve the worst-size continuation before accepting the next item.
            result["more"] = True
            result["count"] = limit
            result["next"] = {
                "epoch": store.log_epoch, "head": head,
                "after": head, **({"recent_after": head, "recent_limit": request.get("recent_limit", 10)}
                                if request["op"] == "inbox" else {}),
            }
            if len(json_bytes(result)) + 1 > request.get("max_bytes", MAX_PAGE_BYTES):
                result[name].pop()
                break
        if len(result[name]) != len(rows):
            break
    returned = len(result["entries"]) + len(result["recent"])
    result["count"] = returned
    if remaining and not returned:
        raise ChatError("capacity", "View byte bound cannot hold the next event.")
    result["more"] = returned < remaining or pending_more
    result["next"] = None
    if result["more"]:
        position = result["entries"][-1]["event"]["id"] if result["entries"] else after
        if request["op"] == "inbox" and len(result["entries"]) == len(entries):
            position = pending_after
        result["next"] = {
            "epoch": store.log_epoch, "head": head,
            "after": position,
            **({
                "recent_after": result["recent"][-1]["event"]["id"]
                if result["recent"] else recent_after,
                "recent_limit": request.get("recent_limit", 10),
            } if request["op"] == "inbox" else {}),
        }
    return result
