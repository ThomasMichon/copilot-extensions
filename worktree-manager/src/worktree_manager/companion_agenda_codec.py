"""Passive Manager projection of the independent accepted-operation reader."""

from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import json
import math
import re
from typing import Any

from .companion_timeline_contract import (
    ActorKind, Availability, Completeness, Durability, EvidenceKind, HistoryPage,
    MAX_BODY_BYTES, MAX_FRAME_BYTES, Provider, Qualification,
    SourceIdentity, SourcePosition, SourceToken, TimelineActor, TimelineContractError,
    TimelineEvent, TimelineGap, TimelineKind, TimelineSnapshot, canonical_payload, identifier,
)
from .companion_worktree_codec import OwnerReadError, encoded, object_fields, stamp

CAPABILITY = "agenda-history-page-v1"
MAX_REVISION = (1 << 63) - 1
HEADER = {"schema", "contract_version", "type", "source", "cursor"}
COMMON = {"history_scope", "completeness", "committed_at", "history_floor"}
ENTRY = set(
    "event_id cursor evidence_kind kind operation operation_id committed_at actor result omission".split()
)
RESULT = set(
    "schema version mode persisted store_id revision operation_id operation source committed_at "
    "actor transition record_ids records delivery".split()
)
OPERATIONS = {
    "init", "assign-root", "append", "outcome", "expand", "return", "revisit",
    "wait", "defer", "recover", "refine",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TimelineContractError(message)


def _text(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    identifier(value, label)
    return value


def _integer(value: Any, *, minimum: int = 0, maximum: int = MAX_REVISION) -> int:
    _require(type(value) is int and minimum <= value <= maximum, "invalid agenda integer")
    return value


def _digest(value: Any) -> str:
    _require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None,
             "invalid agenda digest")
    return value


def _values(value: Any) -> None:
    # The frame byte bound contains traversal; domain JSON has no display-depth/control policy.
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            item.encode("utf-8")
        elif type(item) is float:
            _require(math.isfinite(item), "agenda JSON contains a nonfinite number")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in items:
        _require(key not in result, "agenda JSON contains a duplicate key")
        result[key] = item
    return result


def _constant(value: str) -> None:
    raise TimelineContractError(f"agenda JSON contains {value}")


def _load(raw: bytes, *, limit: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    _require(isinstance(raw, bytes) and len(raw) <= limit, "agenda wire exceeds its byte bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        _values(value)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise TimelineContractError(f"invalid agenda wire: {error}") from error
    _require(isinstance(value, dict), "agenda wire must be an object")
    return value


def _actor(value: Any) -> dict[str, Any]:
    actor = object_fields(value, {"actor", "session", "qualification"})
    for key in ("actor", "session"):
        _text(actor[key], "asserted " + key, nullable=True)
    expected = "caller-asserted" if actor["actor"] or actor["session"] else "unknown"
    _require(actor["qualification"] == expected, "agenda actor is not caller-qualified")
    return actor


class AgendaCodec:
    """Admit only one selected worktree/store, qualified by the outer selected head."""

    def __init__(self, project: str, worktree: str, session: str, store_id: str) -> None:
        for value in (project, worktree, session):
            _text(value, "selected agenda identity")
        _require(isinstance(store_id, str) and re.fullmatch("[0-9a-f]{32}", store_id) is not None,
                 "invalid agenda store identity")
        self.project, self.worktree, self.session, self.store_id = project, worktree, session, store_id
        self._source = {
            "provider": "agenda", "worktree": worktree, "store_id": store_id, "store_version": 2,
        }
        self._key = "agenda:" + sha256(encoded([project, worktree, store_id]).encode()).hexdigest()

    def token(self, epoch: int) -> SourceToken:
        """Return a selected-head token; store identity does not assert a restore epoch."""
        return SourceToken(SourceIdentity(
            Provider.AGENDA, self._key, self.project, self.worktree, self.session, self.store_id,
        ), epoch)

    def _cursor(self, revision: int, source: dict[str, Any] | None = None) -> str:
        namespace = sha256(encoded(self._source if source is None else source).encode()).hexdigest()
        body = encoded({"namespace": namespace, "revision": revision}).encode()
        return "ag1." + base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")

    def _revision(self, cursor: Any, source: dict[str, Any] | None = None) -> int:
        _text(cursor, "agenda cursor")
        _require(cursor.startswith("ag1."), "invalid agenda cursor prefix")
        try:
            raw = base64.b64decode(cursor[4:] + "=" * (-len(cursor[4:]) % 4),
                                   altchars=b"-_", validate=True)
            value = object_fields(_load(raw, limit=512), {"namespace", "revision"})
        except (ValueError, binascii.Error) as error:
            raise TimelineContractError("invalid agenda cursor encoding") from error
        revision = _integer(value["revision"])
        _require(cursor == self._cursor(revision, source), "agenda cursor is not canonical for selected source")
        return revision

    def position(self, cursor: str) -> SourcePosition:
        """Validate an actual owner cursor without fabricating a wire position."""
        self.revision(cursor)
        return SourcePosition(self._key, self.store_id, cursor)

    def revision(self, cursor: str) -> int:
        """Return the revision carried by a canonical selected-source cursor."""
        return self._revision(cursor)

    def _source_fields(self, value: Any, *, legacy: bool = False) -> dict[str, Any]:
        source = object_fields(value, {"provider", "worktree", "store_id", "store_version"})
        version = _integer(source["store_version"], minimum=1, maximum=2)
        _require(source["provider"] == "agenda" and source["worktree"] == self.worktree
                 and source["store_id"] == self.store_id, "agenda response changed selected source")
        if not legacy and version != 2:
            raise OwnerReadError("unsupported", "legacy agenda store has no rich history")
        return source

    @staticmethod
    def _version(value: dict[str, Any], schema: str) -> None:
        _require(value.get("schema") == schema and type(value.get("contract_version")) is int
                 and value["contract_version"] == 1, "unsupported agenda reader contract")

    def _error(self, value: dict[str, Any]) -> None:
        if value.get("type") == "error":
            object_fields(value, {"schema", "contract_version", "type", "error"})
            error = object_fields(value["error"], {"code", "message"})
            _text(error["code"], "agenda error code")
            _text(error["message"], "agenda error message")
            raise OwnerReadError(error["code"], error["message"])

    def info(self, raw: bytes) -> dict[str, Any]:
        """Validate actual reader support, retaining unsupported legacy diagnostics."""
        value = _load(raw)
        if value.get("type") == "error":
            self._version(value, "efforts.agenda-history")
            self._error(value)
        self._version(value, "efforts.agenda-reader-info")
        object_fields(value, {
            "schema", "contract_version", "source", "cursor", "revision", "committed_at",
            "capabilities", "availability", "history_scope", "reason",
        })
        source = self._source_fields(value["source"], legacy=True)
        _require(self._revision(value["cursor"], source) == _integer(value["revision"], minimum=1),
                 "agenda reader revision and cursor disagree")
        stamp(value["committed_at"])
        _require(value["history_scope"] == "accepted-operations", "invalid agenda history scope")
        supported = source["store_version"] == 2
        _require(value["capabilities"] == ([CAPABILITY] if supported else [])
                 and value["availability"] == ("supported" if supported else "observed-only"),
                 "agenda capability disagrees with store format")
        if supported:
            _require(value["reason"] is None, "supported agenda reader supplied a legacy reason")
        else:
            _text(value["reason"], "legacy agenda reason")
        return value

    def _entry(self, item: Any) -> int:
        object_fields(item, ENTRY)
        revision = self._revision(item["cursor"])
        _require(revision > 0, "agenda history floor is not an event")
        _require(item["kind"] == "agenda-transition" and item["evidence_kind"] == "retained-event",
                 "unsupported agenda event evidence")
        _text(item["operation_id"], "accepted operation identity")
        _require(isinstance(item["operation"], str) and item["operation"] in OPERATIONS,
                 "unsupported agenda operation")
        expected = sha256(encoded([self._source, item["operation_id"]]).encode()).hexdigest()
        _require(item["event_id"] == expected, "agenda event identity is not its immutable operation")
        stamp(item["committed_at"])
        _actor(item["actor"])
        if item["result"] is None:
            omission = object_fields(item["omission"], {"utf8_bytes", "sha256", "payload_ref"})
            _integer(omission["utf8_bytes"], minimum=MAX_BODY_BYTES - 8192 + 1, maximum=131072)
            _digest(omission["sha256"])
            reference = object_fields(omission["payload_ref"], {
                "kind", "store_id", "store_version", "revision", "operation_id",
            })
            _require(reference == {
                "kind": "accepted-operation", "store_id": self.store_id, "store_version": 2,
                "revision": revision, "operation_id": item["operation_id"],
            } and type(reference["store_version"]) is int and type(reference["revision"]) is int,
                "agenda omission changed accepted-operation identity")
        else:
            _require(item["omission"] is None, "agenda result and omission cannot coexist")
            self._result(item["result"], item, revision)
        return revision

    def _result(self, result: Any, item: dict[str, Any], revision: int) -> None:
        object_fields(result, RESULT)
        _require(len(encoded(result).encode()) <= MAX_BODY_BYTES - 8192,
                 "agenda result exceeds the owner's unomitted payload bound")
        _require(result["schema"] == "efforts.agenda-result" and type(result["version"]) is int
                 and result["version"] == 2 and result["mode"] == "context-qualified"
                 and result["persisted"] is True and result["store_id"] == self.store_id
                 and type(result["revision"]) is int and result["revision"] == revision,
                 "agenda result lost persisted operation identity")
        for name in ("operation", "operation_id", "committed_at", "actor"):
            _require(result[name] == item[name], "agenda result disagrees with its event")
        source = object_fields(result["source"], {"worktree", "store_id", "session"})
        _require(source == {"worktree": self.worktree, "store_id": self.store_id,
                            "session": item["actor"]["session"]},
                 "agenda operation changed its historical source")
        _require(isinstance(result["transition"], dict) and isinstance(result["delivery"], dict)
                 and isinstance(result["records"], list) and isinstance(result["record_ids"], list),
                 "agenda result has invalid rich structure")
        identities = result["record_ids"]
        for identity in identities:
            _text(identity, "immutable agenda record identity")
        _require(len(set(identities)) == len(identities), "agenda record identities repeat")
        found = []
        for record in result["records"]:
            object_fields(record, {"schema", "version", "id", "kind", "body", "created_at", "actor"})
            _text(record["id"], "immutable agenda record identity")
            _text(record["kind"], "display-only agenda record kind")
            _require(record["schema"] == "efforts.agenda-record" and type(record["version"]) is int
                     and record["version"] == 2 and isinstance(record["body"], dict)
                     and record["actor"] == item["actor"] and record["created_at"] == item["committed_at"],
                     "agenda record lost its accepted metadata")
            found.append(record["id"])
        _require(identities == found, "agenda record identities disagree with records")

    def decode(self, raw: bytes) -> dict[str, Any]:
        """Admit one bounded owner page/gap, or surface its exact owner error."""
        value = _load(raw)
        self._version(value, "efforts.agenda-history")
        self._error(value)
        kind = value.get("type")
        _require(kind in ("history-page", "gap"), "unsupported agenda history frame")
        fields = {"before_cursor", "entries", "has_more", "next_before"} if kind == "history-page" else {
            "base_cursor", "reason", "reconcile", "details",
        }
        object_fields(value, HEADER | COMMON | fields)
        _require(len(encoded({key: item for key, item in value.items() if key not in HEADER}).encode()) + 1
                 <= MAX_BODY_BYTES, "agenda body exceeds its byte bound")
        self._source_fields(value["source"])
        head = self._revision(value["cursor"])
        _require(head > 0 and self._revision(value["history_floor"]) == 0,
                 "agenda head or history floor is invalid")
        stamp(value["committed_at"])
        _require(value["history_scope"] == "accepted-operations" and value["completeness"] == "partial",
                 "agenda history must qualify accepted-operation scope as partial")
        if kind == "gap":
            _require(self._revision(value["base_cursor"]) == 0 and value["reason"] == "retention"
                     and value["reconcile"] == "reader-info-and-history", "invalid agenda gap boundary")
            _text(value["details"], "agenda gap details")
            return value
        before = None if value["before_cursor"] is None else self._revision(value["before_cursor"])
        _require(before is None or 1 <= before <= head + 1, "agenda page boundary is unavailable")
        entries = value["entries"]
        _require(isinstance(entries, list) and len(entries) <= 64, "agenda page exceeds its entry bound")
        revisions = [self._entry(item) for item in entries]
        end = head if before is None else before - 1
        _require(revisions == list(range(end - len(entries) + 1, end + 1))
                 and (bool(entries) or end == 0), "agenda page is discontinuous or lost its tail")
        _require(len({item["event_id"] for item in entries}) == len(entries), "agenda events repeat")
        more = bool(revisions and revisions[0] > 1)
        _require(type(value["has_more"]) is bool and value["has_more"] == more
                 and value["next_before"] == (entries[0]["cursor"] if more else None),
                 "agenda history lost its continuation")
        if entries and revisions[-1] == head:
            _require(entries[-1]["committed_at"] == value["committed_at"],
                     "agenda head timestamp disagrees with accepted operation")
        return value

    def _check(self, frame: dict[str, Any], token: SourceToken) -> None:
        _require(token == self.token(token.request_epoch), "agenda token changed selected source or head")
        self.decode(encoded(frame).encode())

    @staticmethod
    def _detail(item: dict[str, Any]) -> tuple[str, bool]:
        raw = encoded(item).encode()
        try:
            detail = canonical_payload(raw.decode())
        except TimelineContractError as error:
            display_limit = str(error)
        else:
            assert detail is not None
            return detail, False
        summary = {
            "schema": "worktree-manager.agenda-detail-omission", "version": 1,
            "display_only": True, "encoded_bytes": len(raw), "sha256": sha256(raw).hexdigest(),
            "event_id": item["event_id"], "cursor": item["cursor"],
            "owner_omission": item["omission"], "display_limit": display_limit,
            "message": "Accepted-operation detail omitted from display because it exceeds Timeline "
                       "payload limits; no payload retrieval is asserted.",
        }
        detail = canonical_payload(encoded(summary))
        assert detail is not None
        return detail, True

    def events(self, frame: dict[str, Any], token: SourceToken) -> tuple[TimelineEvent, ...]:
        """Project immutable accepted operations, retaining historical caller qualification."""
        self._check(frame, token)
        _require(frame["type"] == "history-page", "agenda events require an accepted-operation page")
        events = []
        for item in frame["entries"]:
            actor = item["actor"]
            asserted = actor["qualification"] == "caller-asserted"
            identity = actor["session"] or actor["actor"]
            detail, omitted = self._detail(item)
            events.append(TimelineEvent(
                token.identity, self.position(item["cursor"]), item["event_id"],
                TimelineActor(identity, ActorKind.SESSION if actor["session"] else ActorKind.UNKNOWN,
                              Qualification.REPORTED if asserted else Qualification.UNKNOWN,
                              identity or "Unknown actor"),
                TimelineKind.AGENDA_TRANSITION, EvidenceKind.RETAINED_EVENT,
                stamp(item["committed_at"]), "Accepted agenda operation: " + item["operation"],
                occurred_at=stamp(item["committed_at"]), durability=Durability.COMMITTED,
                details=("Caller metadata is reported, not authoritative. Rich record bodies, transition "
                         "and delivery are display-only; accepted operations do not prove physical actions."
                         + (" Detail omitted from display due to Timeline payload limits; "
                            "integrity evidence retained." if omitted else "")
                         + (" Owner omitted the original payload; see its integrity/reference evidence."
                            if item["omission"] is not None else "")),
                data_json=detail,
            ))
        return tuple(events)

    def snapshot(self, frame: dict[str, Any], token: SourceToken) -> TimelineSnapshot:
        """Project the latest history page; historical callers never become the selected head."""
        self._check(frame, token)
        _require(frame["type"] == "history-page" and frame["before_cursor"] is None,
                 "agenda snapshot requires an initial latest page")
        return TimelineSnapshot(
            token, self.position(frame["cursor"]), Availability.OBSERVED, Completeness.PARTIAL,
            self.events(frame, token), as_of=stamp(frame["committed_at"]),
            before=self.position(frame["next_before"]) if frame["has_more"] else None,
        )

    def page(
        self, frame: dict[str, Any], token: SourceToken, before: SourcePosition,
    ) -> HistoryPage | TimelineGap:
        """Project exactly the requested exclusive owner boundary."""
        self._check(frame, token)
        _require(isinstance(before, SourcePosition) and before.matches(token.identity),
                 "agenda page boundary changed source")
        _require(before == self.position(before.cursor), "agenda page boundary changed source")
        boundary = frame["base_cursor"] if frame["type"] == "gap" else frame["before_cursor"]
        _require(boundary == before.cursor, "agenda page changed the requested before boundary")
        if frame["type"] == "gap":
            return TimelineGap(
                token, self.position(frame["cursor"]), self.position(frame["history_floor"]),
                frame["reason"], frame["details"], Availability.OBSERVED, Completeness.PARTIAL,
                before, source_ref_json=encoded({"owner_reconcile": frame["reconcile"]}),
            )
        return HistoryPage(
            token, before, self.position(frame["next_before"]) if frame["has_more"] else None,
            self.events(frame, token), Completeness.PARTIAL,
        )
