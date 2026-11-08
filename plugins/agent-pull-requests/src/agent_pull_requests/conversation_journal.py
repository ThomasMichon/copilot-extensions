"""Bounded body-free conversation metadata inside the existing storage owner."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import json
import re

from .attention import Candidate, Cursor, PRKey, _integer, _text

PROTOCOL = "agent-pull-requests.conversation/v1"
MAX_PRS = 256
MAX_EVENTS = 64
MAX_PAGE = 16
RETENTION_SECONDS = 86400
SECTIONS = ("commits", "comments", "threads", "checks", "reviews", "participants")
ROLES = ("author", "reviewer", "merger")
VERDICTS = ("unknown", "approved", "commented", "changes-requested", "dismissed")


@dataclass(frozen=True)
class Observation:
    """Already admitted owner evidence, not provider acquisition or write authority."""

    key: PRKey
    candidate: Candidate
    observed_from: int
    observed_to: int
    state: str
    complete: bool
    author: str | None
    reviewers: tuple[str, ...]
    our_roles: tuple[str, ...]
    verdict: str
    review_rounds: int | None
    counts: tuple[tuple[str, int | None], ...]
    sections: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.key) is not PRKey or type(self.candidate) is not Candidate:
            raise ValueError("typed conversation key and candidate required")
        for value in (self.observed_from, self.observed_to):
            _integer(value)
        if (
            self.observed_from > self.observed_to
            or type(self.complete) is not bool
            or self.state not in ("open", "merged", "closed-unmerged")
            or self.verdict not in VERDICTS
        ):
            raise ValueError("invalid conversation observation")
        if self.author is not None:
            _text(self.author)
        for values in (self.reviewers, self.our_roles):
            if type(values) is not tuple or len(values) > 256 or len(set(values)) != len(values):
                raise ValueError("bounded unique conversation relationships required")
            for value in values:
                _text(value)
        if set(self.our_roles) - set(ROLES):
            raise ValueError("invalid conversation role")
        if self.review_rounds is not None:
            _integer(self.review_rounds)
        for entries in (self.counts, self.sections):
            if type(entries) is not tuple or len(entries) > len(SECTIONS):
                raise ValueError("bounded conversation sections required")
            if any(type(row) is not tuple or len(row) != 2 for row in entries):
                raise ValueError("conversation sections require pairs")
            if len(dict(entries)) != len(entries) or set(dict(entries)) - set(SECTIONS):
                raise ValueError("invalid conversation section identity")
        for _, count in self.counts:
            if count is not None:
                _integer(count)
        for _, digest in self.sections:
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("conversation section requires a normalized content digest")
        if len(json.dumps(self.document(), allow_nan=False).encode("utf-8")) > 4096:
            raise ValueError("conversation summary exceeds its byte bound")

    @property
    def terminal(self) -> bool:
        return self.complete and self.state != "open"

    def document(self) -> dict:
        """Return detached JSON-compatible, body-free evidence."""
        value = asdict(self)
        for name in ("reviewers", "our_roles"):
            value[name] = sorted(value[name])
        for name in ("counts", "sections"):
            value[name] = dict(sorted(value[name]))
        return value

    @classmethod
    def restore(cls, value: dict) -> Observation:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("invalid conversation observation fields")
        result = dict(value)
        result["key"] = PRKey(**value["key"])
        result["candidate"] = Candidate(**value["candidate"])
        for name in ("reviewers", "our_roles"):
            if not isinstance(value[name], list):
                raise ValueError("invalid conversation relationships")
            result[name] = tuple(value[name])
        for name in ("counts", "sections"):
            if not isinstance(value[name], dict):
                raise ValueError("invalid conversation summary")
            result[name] = tuple(value[name].items())
        return cls(**result)


def _cursor(record: dict) -> dict:
    return {"epoch": record["epoch"], "sequence": record["sequence"]}


def _meaning(value: dict) -> dict:
    return {
        key: item for key, item in value.items() if key not in ("observed_from", "observed_to")
    }


class ConversationJournal:
    """Transactional value object; never opens files, fetches content or emits tasks."""

    def __init__(self, state: dict | None = None) -> None:
        self.records: list[dict] = []
        if state is None:
            return
        try:
            if (
                set(state) != {"protocol", "records"}
                or state["protocol"] != PROTOCOL
                or not isinstance(state["records"], list)
                or len(state["records"]) > MAX_PRS
            ):
                raise ValueError
            keys = set()
            for record in state["records"]:
                if set(record) != {"current", "epoch", "sequence", "events", "previous_terminal"}:
                    raise ValueError
                current = Observation.restore(record["current"])
                if current.key in keys or current.document() != record["current"]:
                    raise ValueError
                keys.add(current.key)
                Cursor(record["epoch"], record["sequence"])
                _integer(record["sequence"], 1)
                previous = record["previous_terminal"]
                if previous is not None:
                    old = Observation.restore(previous)
                    if (
                        not old.terminal
                        or old.sections
                        or old.key != current.key
                        or record["epoch"] <= 1
                    ):
                        raise ValueError
                events = record["events"]
                if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
                    raise ValueError
                if current.terminal and (current.sections or len(events) != 1):
                    raise ValueError
                last = events[0]["sequence"] - 1
                timestamp = 0
                for event in events:
                    if set(event) != {"sequence", "kind", "candidate", "observed_at", "changed"}:
                        raise ValueError
                    _integer(event["sequence"], 1)
                    _integer(event["observed_at"])
                    Candidate(**event["candidate"])
                    if (
                        event["sequence"] != last + 1
                        or not timestamp <= event["observed_at"] <= current.observed_to
                        or event["kind"] not in ("opened", "changed", "terminal", "reopened")
                        or not isinstance(event["changed"], list)
                        or len(event["changed"]) != len(set(event["changed"]))
                        or set(event["changed"]) - set(SECTIONS) - {"candidate", "summary"}
                    ):
                        raise ValueError
                    last, timestamp = event["sequence"], event["observed_at"]
                if last != record["sequence"] or events[-1]["candidate"] != asdict(
                    current.candidate
                ):
                    raise ValueError
                if current.terminal and events[-1]["kind"] != "terminal":
                    raise ValueError
            self.records = copy.deepcopy(state["records"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("invalid persisted conversation journal") from None

    def snapshot(self) -> dict:
        return copy.deepcopy({"protocol": PROTOCOL, "records": self.records})

    def _find(self, key: PRKey) -> dict | None:
        if type(key) is not PRKey:
            raise ValueError("typed conversation key required")
        return next((row for row in self.records if row["current"]["key"] == asdict(key)), None)

    def observe(
        self,
        value: Observation,
        *,
        expected: Cursor | None,
        reopen: bool = False,
    ) -> dict:
        """Compare-and-set one complete observation; uncertainty retries allocate no duplicate event."""
        if (
            type(value) is not Observation
            or type(reopen) is not bool
            or expected is not None
            and type(expected) is not Cursor
        ):
            raise ValueError("typed conversation observation and cursor required")
        current = value.document()
        if value.terminal:
            current["sections"] = {}
        record = self._find(value.key)
        previous_terminal = None
        epoch, sequence, events = 1, 0, []
        kind = "terminal" if value.terminal else "opened"
        changed = sorted(dict(value.sections))
        if record is not None:
            old = Observation.restore(record["current"])
            if current == record["current"]:
                return {"status": "unchanged", "cursor": _cursor(record)}
            if value.observed_from < old.observed_to:
                return {"status": "stale_observation", "cursor": _cursor(record)}
            if expected is None or asdict(expected) != _cursor(record):
                return {"status": "conflict", "cursor": _cursor(record)}
            if _meaning(current) == _meaning(record["current"]):
                record["current"] = current
                record["events"] = [
                    row for row in record["events"]
                    if row["sequence"] == record["sequence"]
                    or row["observed_at"] > value.observed_to - RETENTION_SECONDS
                ]
                return {"status": "unchanged", "cursor": _cursor(record)}
            if old.author is not None and value.author != old.author:
                raise ValueError("original conversation author cannot change or disappear")
            if not set(old.reviewers) <= set(value.reviewers) or not set(old.our_roles) <= set(
                value.our_roles
            ):
                raise ValueError("observed conversation relationships cannot disappear")
            reopening = old.terminal and value.state == "open" and value.complete and reopen
            previous_terminal = record["previous_terminal"]
            epoch, sequence = record["epoch"], record["sequence"]
            events = list(record["events"])
            if old.terminal:
                if reopening:
                    previous_terminal = old.document()
                    epoch, sequence, events, kind = epoch + 1, 0, [], "reopened"
                elif not value.terminal:
                    raise ValueError(
                        "a terminal conversation requires an explicit complete reopen"
                    )
            else:
                kind = "terminal" if value.terminal else "changed"
            if (
                old.review_rounds is not None
                and not reopening
                and (value.review_rounds is None or value.review_rounds < old.review_rounds)
            ):
                raise ValueError("known cumulative review rounds cannot disappear or decrease")
            changed = sorted(
                name
                for name in SECTIONS
                if current["sections"].get(name) != record["current"]["sections"].get(name)
            )
            if old.candidate != value.candidate:
                changed.append("candidate")
            if _meaning({**current, "sections": {}, "candidate": {}}) != _meaning(
                {**record["current"], "sections": {}, "candidate": {}},
            ):
                changed.append("summary")
        elif expected is not None:
            return {"status": "conflict", "cursor": None}
        elif len(self.records) >= MAX_PRS:
            return {"status": "unavailable", "reason": "conversation_capacity"}
        sequence += 1
        event = {
            "sequence": sequence,
            "kind": kind,
            "candidate": asdict(value.candidate),
            "observed_at": value.observed_to,
            "changed": changed,
        }
        events = [
            row for row in events if row["observed_at"] > value.observed_to - RETENTION_SECONDS
        ]
        events = [event] if value.terminal else [*events, event][-MAX_EVENTS:]
        updated = {
            "current": current,
            "epoch": epoch,
            "sequence": sequence,
            "events": events,
            "previous_terminal": previous_terminal,
        }
        if record is None:
            self.records.append(updated)
        else:
            record.update(updated)
        return {"status": "observed", "cursor": _cursor(updated)}

    def events(self, key: PRKey, *, after: Cursor | None, limit: int, now: int) -> dict:
        """Read a bounded page; returned positions never acknowledge delivery or feedback."""
        _integer(now)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ValueError("bounded conversation page required")
        if after is not None and type(after) is not Cursor:
            raise ValueError("typed conversation cursor required")
        record = self._find(key)
        if record is None:
            return {"status": "unavailable", "reason": "conversation_unobserved"}
        retained = [
            row
            for row in record["events"]
            if row["observed_at"] > now - RETENTION_SECONDS or row["kind"] == "terminal"
        ]
        low_water = retained[0]["sequence"] - 1 if retained else record["sequence"]
        result = {
            "current": copy.deepcopy(record["current"]),
            "previous_terminal": copy.deepcopy(record["previous_terminal"]),
            "high_water": _cursor(record),
            "low_water": low_water,
            "events": [],
            "more": False,
        }
        if after is None:
            return {**result, "status": "snapshot", "next": _cursor(record)}
        if after.epoch != record["epoch"] or not low_water <= after.sequence <= record["sequence"]:
            return {**result, "status": "resync_required", "next": None}
        page = [row for row in retained if row["sequence"] > after.sequence]
        return {
            **result,
            "status": "events",
            "events": copy.deepcopy(page[:limit]),
            "more": len(page) > limit,
            "next": {"epoch": after.epoch, "sequence": page[min(len(page), limit) - 1]["sequence"]}
            if page
            else asdict(after),
        }
