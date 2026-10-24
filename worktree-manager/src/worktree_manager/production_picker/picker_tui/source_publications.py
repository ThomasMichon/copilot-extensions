"""Private, pure F3 candidate. Nothing in the production loader imports this module.

Adapters supply source identity and serialize calls. Only negotiated F1 v2
publications are supported here. Receipt intents are private model inputs, not
an implementation or parser of the proposed F2 owner endpoint.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType

from ...worktree_feed_contract import CurrentFeed
from ...worktree_observation_contract import check_advance, check_observation_advance, FACTS
from ...worktree_rows_contract import parse_current_rows, publication_wire_rows

_MAX_ROWS = 10_000
_MAX_PARTITIONS = 128
_MAX_RECEIPTS = 128
_METADATA = {"_projection", "_availability", "_observations", "_observation_derivations"}
_FRAME_FIELDS = {
    "update": {"type", "contract_version", "project", "projection", "cursor",
               "base_cursor", "selected_count", "availability", "ok"},
    "worktree": {"type", "projection", "wt", "phase"},
    "delete": {"type", "projection", "id"},
    "observation": {"type", "projection", "observation"},
    "order": {"type", "projection", "ids"},
    "commit": {"type", "projection", "cursor"},
    "heartbeat": {"type", "generation", "cursor"},
    "availability": {"type", "projection", "availability"},
}


def _name(value: str) -> str:
    if (
        not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256
        or any(c in value for c in ("\x00", "\r", "\n"))
    ):
        raise ValueError("invalid publication identity")
    return value


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _content(row: dict) -> dict:
    return {key: value for key, value in row.items()
            if key not in {"_projection", "_availability"}}


def _observation(row: dict) -> dict:
    return {"facts": row["_observations"],
            "derivations": row.get("_observation_derivations", {})}


def _validate_frame(frame: dict) -> None:
    if (not isinstance(frame, dict) or not isinstance(frame.get("type"), str)
            or frame["type"] not in _FRAME_FIELDS
            or set(frame) - _FRAME_FIELDS[frame["type"]]):
        raise ValueError("unsupported feed transaction frame")
    if frame["type"] == "worktree" and (
        not isinstance(frame.get("wt"), dict) or set(frame["wt"]) & _METADATA
        or frame.get("phase", "current") != "current"
    ):
        raise ValueError("feed contains receiver metadata")


def _envelope(value: dict, *, complete: bool) -> tuple[dict, list[dict]]:
    if not isinstance(value, dict) or set(value) - {
        "ok", "contract_version", "project", "projection", "worktrees",
        "observations", "availability",
    }:
        raise ValueError("unsupported publication envelope")
    rows = value.get("worktrees")
    if not isinstance(rows, list) or len(rows) > _MAX_ROWS:
        raise ValueError("publication exceeds row bound")
    if value.get("contract_version") != 2:
        raise ValueError("private reducer requires negotiated observations v2")
    if not isinstance(value.get("observations"), list) or len(value["observations"]) != len(rows):
        raise ValueError("publication omitted observation evidence")
    if any(not isinstance(row, dict) or set(row) & _METADATA for row in rows):
        raise ValueError("publication contains receiver metadata")
    copied = deepcopy(value)
    admitted = parse_current_rows(copied, complete=complete)
    if copied["projection"]["quality"] != "current":
        raise ValueError("advisory publication cannot establish authoritative state")
    return copied, admitted


@dataclass(frozen=True)
class _Receipt:
    """Optional, unwired intent; revision is an exact-row observation fence."""

    request_id: str
    project: str
    worktree_id: str
    generation: str
    status: str
    revision: int | None = None
    covered_facts: tuple[str, ...] = ()

    def validate(self) -> None:
        for value in (self.request_id, self.project, self.worktree_id):
            _name(value)
        if (
            not isinstance(self.generation, str) or len(self.generation) != 32
            or any(c not in "0123456789abcdef" for c in self.generation)
            or self.status not in {"published", "pending", "degraded", "superseded", "not-found"}
            or type(self.covered_facts) is not tuple
            or len(self.covered_facts) != len(set(self.covered_facts))
            or set(self.covered_facts) - FACTS
        ):
            raise ValueError("invalid private receipt intent")
        if self.status in {"published", "not-found"}:
            if type(self.revision) is not int or self.revision < 1:
                raise ValueError("receipt lacks a committed revision")
            if self.status == "published" and not self.covered_facts:
                raise ValueError("receipt lacks exact fact coverage")
            if self.status == "not-found" and self.covered_facts:
                raise ValueError("absence receipt cannot assert fact coverage")
        elif self.revision is not None or self.covered_facts:
            raise ValueError("unconfirmed receipt is not publication proof")


@dataclass
class _Partition:
    generation: str
    feed_epoch: int = 0
    feed: CurrentFeed = field(default_factory=CurrentFeed)
    rows: dict = field(default_factory=dict)
    floors: dict = field(default_factory=dict)
    tombstones: dict = field(default_factory=dict)
    published_ids: set = field(default_factory=set)
    absence_revision: int = 0
    order: list = field(default_factory=list)
    receipts: dict = field(default_factory=dict)
    needs_snapshot: bool = False
    availability: dict | None = None
    availability_revision: int = 0

    def note_availability(self, revision: int, availability: dict | None, *, fresh=False) -> None:
        if revision < self.availability_revision:
            return
        # F1 availability can change without a publication; equal-cursor recovery needs freshness.
        if (revision == self.availability_revision and self.availability is not None
                and availability is None and not fresh):
            return
        self.availability_revision, self.availability = revision, availability

    def row(self, row: dict, revision: int) -> None:
        wid = row["id"]
        absence = self.absence_revision if wid not in self.published_ids else 0
        floor = max(self.floors.get(wid, 0), absence)
        if revision < floor:
            return
        previous = self.rows.get(wid)
        if revision == floor:
            if previous is None or _content(previous) != _content(row):
                raise ValueError("equal publication revision conflicts")
            self.rows[wid] = {**previous, "_availability": row["_availability"]}
            return
        if previous is None and row["semantic_revision"] <= max(
            self.tombstones.get(wid, 0), absence,
        ):
            raise ValueError("semantic row predates its tombstone")
        check_advance(previous, row, field="semantic_revision")
        check_observation_advance(None if previous is None else _observation(previous),
                                  _observation(row))
        self.rows[wid] = row
        self.floors[wid] = revision
        self.tombstones.pop(wid, None)
        if wid not in self.order:
            self.order.append(wid)

    def delete(self, wid: str, revision: int) -> None:
        floor = self.floors.get(wid, 0)
        if revision < floor:
            return
        if revision == floor and wid in self.rows:
            raise ValueError("equal publication revision conflicts with deletion")
        self.rows.pop(wid, None)
        self.floors[wid] = revision
        self.tombstones[wid] = revision

    def replace(self, rows: list[dict], revision: int) -> None:
        incoming = {row["id"] for row in rows}
        for row in rows:
            self.row(row, revision)
        for wid in self.floors.keys() - incoming:
            self.delete(wid, revision)
        self.published_ids, self.absence_revision = incoming, revision
        self.order = [row["id"] for row in rows if row["id"] in self.rows] + [
            wid for wid in self.order if wid not in incoming and wid in self.rows
        ]
        if len(self.floors) > _MAX_ROWS:
            raise ValueError("publication exceeds retained identity bound")

    def receipt_state(self, receipt: _Receipt) -> str:
        if receipt.generation != self.generation:
            return "invalidated"
        if receipt.status not in {"published", "not-found"}:
            return receipt.status
        if receipt.status == "not-found":
            return ("confirmed" if self.tombstones.get(receipt.worktree_id, 0)
                    >= receipt.revision else "pending")
        row = self.rows.get(receipt.worktree_id)
        facts = {} if row is None else row["_observations"]
        return "confirmed" if all(
            name in facts and facts[name]["quality"] == "current"
            and facts[name]["observation_revision"] >= receipt.revision
            for name in receipt.covered_facts
        ) else "pending"


class _SourcePublications:
    """Transactional source-qualified state with independent feed and row floors."""

    def __init__(self) -> None:
        self._epochs: dict[str, int] = {}
        self._partitions: dict[tuple[str, str], _Partition] = {}

    def begin_epoch(self, source: str) -> int:
        _name(source)
        if source not in self._epochs and len(self._epochs) >= _MAX_PARTITIONS:
            raise ValueError("source bound exceeded")
        epoch = self._epochs.get(source, 0) + 1
        self._epochs[source] = epoch
        for (selected, _), partition in self._partitions.items():
            if selected == source:
                partition.needs_snapshot = True
        return epoch

    def _current(self, source: str, epoch: int) -> bool:
        return type(epoch) is int and self._epochs.get(source) == epoch

    def admit_snapshot(self, source: str, epoch: int, envelope: dict, *,
                       project: str, fresh: bool) -> bool:
        if not self._current(source, epoch):
            return False
        if type(fresh) is not bool:
            raise ValueError("snapshot freshness must be explicit")
        envelope, rows = _envelope(envelope, complete=True)
        if envelope["project"] != project:
            raise ValueError("snapshot differs from selected project")
        key = source, envelope["project"]
        previous = self._partitions.get(key)
        head = envelope["projection"]
        if head["selected_count"] != head["roster_count"]:
            raise ValueError("snapshot must cover the full source/project roster")
        reset = previous is None or previous.generation != head["generation"]
        if reset and fresh is not True:
            return False
        if not reset and head["revision"] < previous.feed.head["revision"]:
            return False
        if not reset and head["revision"] == previous.feed.head["revision"] and (
            head != previous.feed.head
            or [_content(row) for row in rows] != [
                _content(row) for row in previous.feed.rows
            ]
        ):
            raise ValueError("equal publication revision conflicts with committed roster/order")
        if previous is None and len(self._partitions) >= _MAX_PARTITIONS:
            raise ValueError("source/project bound exceeded")
        candidate = _Partition(head["generation"]) if reset else deepcopy(previous)
        if reset and previous is not None:
            candidate.receipts = deepcopy(previous.receipts)
        feed = CurrentFeed()
        begin = {"type": "begin", "ok": True, "contract_version": 2,
                 "project": key[1], "projection": head, "selected_count": len(rows),
                 "cursor": head["revision"], "base_cursor": None,
                 "availability": envelope.get("availability")}
        feed.receive(begin)
        for row in publication_wire_rows(rows):
            feed.receive({"type": "worktree", "phase": "current", "projection": head, "wt": row})
        for item in envelope["observations"]:
            feed.receive({"type": "observation", "projection": head, "observation": item})
        feed.receive({"type": "done", "projection": head, "cursor": head["revision"]})
        candidate.replace(feed.rows, head["revision"])
        candidate.feed, candidate.feed_epoch = feed, epoch
        candidate.needs_snapshot = False
        candidate.note_availability(head["revision"], envelope.get("availability"), fresh=fresh)
        self._partitions[key] = candidate
        return True

    def admit_point(self, source: str, epoch: int, envelope: dict, *,
                    project: str, worktree_id: str) -> bool:
        if not self._current(source, epoch):
            return False
        _name(worktree_id)
        envelope, rows = _envelope(envelope, complete=False)
        if envelope["project"] != project or any(row["id"] != worktree_id for row in rows):
            raise ValueError("point differs from selected project or identity")
        candidate = deepcopy(self._partitions.get((source, envelope["project"])))
        if candidate is None:
            return False
        if candidate.generation != envelope["projection"]["generation"]:
            candidate.needs_snapshot = True
            self._partitions[source, envelope["project"]] = candidate
            return False
        if len(envelope.get("observations", [])) != len(rows):
            raise ValueError("point publication omitted observation evidence")
        for row in rows:
            candidate.row(row, envelope["projection"]["revision"])
        candidate.note_availability(
            envelope["projection"]["revision"], envelope.get("availability"),
        )
        if len(candidate.floors) > _MAX_ROWS:
            raise ValueError("publication exceeds retained identity bound")
        self._partitions[source, envelope["project"]] = candidate
        return bool(rows)

    def admit_feed_commit(self, source: str, epoch: int, project: str, frames: list) -> bool:
        if not self._current(source, epoch):
            return False
        if not isinstance(frames, list) or not 0 < len(frames) <= 3 * _MAX_ROWS + 2:
            raise ValueError("invalid bounded feed transaction")
        previous = self._partitions.get((source, project))
        if previous is None or previous.feed_epoch != epoch:
            return False
        if len(frames) == 1 and isinstance(frames[0], dict) and (
            frames[0].get("type") == "heartbeat"
        ):
            _validate_frame(frames[0])
            previous.feed.receive(frames[0])
            return False
        if len(frames) == 1 and isinstance(frames[0], dict) and (
            frames[0].get("type") == "availability"
        ):
            _validate_frame(frames[0])
            if previous.feed._project != project:
                raise ValueError("availability differs from selected project")
            candidate = deepcopy(previous)
            frame = deepcopy(frames[0])
            rows = candidate.feed.receive(frame)
            revision = candidate.feed.head["revision"]
            if revision >= candidate.availability_revision:
                candidate.note_availability(revision, frame.get("availability"), fresh=True)
                for row in rows:
                    wid = row["id"]
                    if wid in candidate.rows and candidate.floors[wid] <= revision:
                        candidate.rows[wid] = {
                            **candidate.rows[wid], "_availability": row["_availability"],
                        }
            self._partitions[source, project] = candidate
            return True
        candidate = deepcopy(previous)
        committed = False
        availability = None
        for frame in deepcopy(frames):
            _validate_frame(frame)
            if committed or (
                frame["type"] in {"heartbeat", "availability"} and len(frames) != 1
            ):
                raise ValueError("expected one complete feed transaction")
            rows = candidate.feed.receive(frame)
            if frame["type"] == "update" and (
                frame["projection"]["roster_count"] != frame["selected_count"]
            ):
                raise ValueError("feed must cover the full source/project roster")
            if frame["type"] == "update":
                availability = frame.get("availability")
            if frame["type"] == "commit":
                candidate.replace(rows, candidate.feed.head["revision"])
                candidate.note_availability(candidate.feed.head["revision"], availability)
                committed = True
        if not committed:
            raise ValueError("incomplete feed transaction")
        self._partitions[source, project] = candidate
        return True

    def note_receipt(self, source: str, epoch: int, receipt: _Receipt) -> bool:
        if not self._current(source, epoch):
            return False
        if type(receipt) is not _Receipt:
            raise ValueError("unsupported receipt wire; private intent required")
        receipt.validate()
        partition = self._partitions.get((source, receipt.project))
        if partition is None:
            return False
        key = receipt.worktree_id, receipt.request_id
        if key not in partition.receipts and len(partition.receipts) >= _MAX_RECEIPTS:
            raise ValueError("receipt bound exceeded")
        previous = partition.receipts.get(key)
        if previous is not None and previous != receipt:
            if (previous.generation != receipt.generation
                    or previous.status not in {"pending", "degraded"}):
                raise ValueError("conflicting receipt identity; use a new observation request")
        partition.receipts[key] = receipt
        return True

    def records(self, source: str | None = None):
        return _freeze({
            (selected, project, wid): deepcopy(partition.rows[wid])
            for (selected, project), partition in self._partitions.items()
            if source is None or selected == source
            for wid in partition.order if wid in partition.rows
        })

    def status(self, source: str, project: str):
        partition = self._partitions[source, project]
        return _freeze({
            "epoch": self._epochs[source], "generation": partition.generation,
            "feed_cursor": partition.feed.head["revision"],
            "stale": partition.needs_snapshot or partition.availability is not None,
            "floors": deepcopy(partition.floors), "tombstones": deepcopy(partition.tombstones),
            "receipts": {key: partition.receipt_state(value)
                         for key, value in partition.receipts.items()},
        })
