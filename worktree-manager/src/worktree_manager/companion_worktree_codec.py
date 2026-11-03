"""Manager-owned projection of the selected worktree observation wire contract."""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

from .companion_timeline_contract import (
    ActorKind, Availability, Completeness, Durability, EvidenceKind, HistoryPage,
    Provider, Qualification, RelationKind, SourceIdentity, SourcePosition,
    SourceToken, TimelineActor, TimelineContractError, TimelineEvent, TimelineGap,
    TimelineKind, TimelineRelation, TimelineSnapshot, check_wire_body, decode_wire_frame,
    identifier,
)
from .companion_timeline_controller import TimelineSourceError

FEED_CAPABILITY = "worktree-observations-feed-v1"
HISTORY_CAPABILITY = "worktree-observations-history-v1"
HEADER = frozenset(
    "type contract_version source source_generation resident_generation project "
    "worktree_id head_session_id cursor".split()
)
QUALIFIED = "availability completeness durability history_scope history_floor observed_at"
BODY = {
    "snapshot": set((QUALIFIED + " replay_barrier base_cursor entries replay_complete facts").split()),
    "delta": set((QUALIFIED + " base_cursor entries facts").split()),
    "history-page": set((QUALIFIED + " before_cursor next_before entries has_more").split()),
    "gap": set((QUALIFIED + " base_cursor reason details reconcile source_ref").split()),
    "heartbeat": set(),
    "going-away": {"reason"},
}
ENTRY = set(
    "kind evidence_kind native_kind occurred_at observed_at actor correlation relations "
    "record_fence source_ref summary details reason phase payload event_id cursor head_session_id".split()
)
OWNER_KINDS = frozenset(
    "worktree-created purpose-changed effort-bound effort-released claim-added claim-released "
    "claim-settled contribution-reported source-gap observed-change source-event".split()
)


class OwnerReadError(TimelineSourceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def object_fields(value: Any, fields: set[str] | frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise TimelineContractError("selected observation has invalid fields")
    return value


def reference(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise TimelineContractError("owner reference must be text")
    identifier(value, "owner reference")
    if len(value.encode("utf-8")) > 512 or any(
        char.isspace() or char in "[]<>" for char in value
    ):
        raise TimelineContractError("invalid owner reference")
    return value


def generation(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{32}", value) is None:
        raise TimelineContractError("invalid owner generation")
    return value


def stamp(value: Any, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise TimelineContractError("invalid owner timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimelineContractError("invalid owner timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise TimelineContractError("owner timestamp requires a timezone")
    return result


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def owner_values(value: Any) -> None:
    if type(value) is int and not -(2**63 - 1) <= value <= 2**63 - 1:
        raise TimelineContractError("owner observation exceeds its integer bound")
    if isinstance(value, str) and "\x00" in value:
        raise TimelineContractError("owner observation contains NUL")
    if isinstance(value, dict):
        for key, item in value.items():
            owner_values(key)
            owner_values(item)
    elif isinstance(value, list):
        for item in value:
            owner_values(item)


class WorktreeCodec:
    def __init__(self, project: str, worktree: str, session: str, resident: str) -> None:
        self.project, self.worktree, self.session = project, worktree, session
        self.resident = generation(resident)
        self._incarnation: str | None = None

    def decode(self, raw: bytes) -> dict[str, Any]:
        value = decode_wire_frame(raw)
        owner_values(value)
        if type(value.get("contract_version")) is not int or value["contract_version"] != 1:
            raise TimelineContractError("unsupported owner observation version")
        kind = value.get("type")
        if kind == "error":
            object_fields(value, {"type", "contract_version", "error"})
            error = object_fields(value["error"], {"code", "message"})
            code = reference(error["code"])
            if not isinstance(error["message"], str) or not error["message"]:
                raise TimelineContractError("owner error omitted its diagnostic")
            raise OwnerReadError(str(code), error["message"])
        if not isinstance(kind, str) or kind not in BODY:
            raise TimelineContractError("unknown owner observation frame")
        object_fields(value, HEADER | BODY[kind])
        check_wire_body({key: item for key, item in value.items() if key not in HEADER})
        if (value["project"], value["worktree_id"]) != (self.project, self.worktree):
            raise TimelineContractError("owner response changed exact selected worktree")
        if generation(value["resident_generation"]) != self.resident:
            raise OwnerReadError("source-replaced", "resident observation generation changed")
        generation(value["source_generation"])
        reference(value["source"])
        reference(value["cursor"])
        reference(value["head_session_id"], nullable=True)
        if kind in {"snapshot", "delta", "history-page", "gap"}:
            Availability(value["availability"])
            Completeness(value["completeness"])
            Durability(value["durability"])
            if value["history_scope"] != "retained-range":
                raise TimelineContractError("owner history must qualify its retained range")
            reference(value["history_floor"])
            stamp(value["observed_at"])
        if kind in {"snapshot", "delta"}:
            reference(value["base_cursor"], nullable=kind == "snapshot")
            if value["availability"] == "observed":
                if value["head_session_id"] != self.session:
                    raise TimelineContractError("owner facts belong to another selected head")
                self._facts(value["facts"])
            elif value["facts"] is not None:
                raise TimelineContractError("unqualified owner facts cannot remain active")
        if kind == "snapshot" and (
            type(value["replay_complete"]) is not bool or value["replay_barrier"] != value["cursor"]
        ):
            raise TimelineContractError("owner snapshot lost its atomic replay barrier")
        if kind in {"snapshot", "delta", "history-page"}:
            entries = value["entries"]
            if not isinstance(entries, list) or len(entries) > 64:
                raise TimelineContractError("owner history exceeded its bounded page")
            seen = set()
            for entry in entries:
                object_fields(entry, ENTRY)
                if not isinstance(entry["kind"], str) or entry["kind"] not in OWNER_KINDS:
                    raise TimelineContractError("unsupported owner event kind")
                for name in ("summary", "details", "reason", "phase"):
                    if entry[name] is not None and not isinstance(entry[name], str):
                        raise TimelineContractError("owner event prose must be literal text or unknown")
                for name in ("event_id", "cursor"):
                    reference(entry[name])
                if entry["event_id"] in seen:
                    raise TimelineContractError("duplicate owner event identity")
                seen.add(entry["event_id"])
            if kind == "delta" and (
                value["base_cursor"] == value["cursor"]
                or not entries or entries[-1]["cursor"] != value["cursor"]
            ):
                raise TimelineContractError("owner delta lost its advancing complete replay")
            if kind == "snapshot" and value["base_cursor"] is not None and value["replay_complete"]:
                if value["base_cursor"] == value["cursor"] and entries or (
                    value["base_cursor"] != value["cursor"]
                    and (not entries or entries[-1]["cursor"] != value["cursor"])
                ):
                    raise TimelineContractError("owner snapshot lost its replay tail")
        if kind == "history-page":
            reference(value["before_cursor"], nullable=True)
            reference(value["next_before"], nullable=True)
            if type(value["has_more"]) is not bool or (
                value["has_more"] and (
                    not value["entries"] or value["next_before"] != value["entries"][0]["cursor"]
                )
            ) or not value["has_more"] and value["next_before"] is not None:
                raise TimelineContractError("owner history lost its continuation")
        if kind == "gap":
            reference(value["base_cursor"], nullable=True)
            if value["reconcile"] != "snapshot-and-history":
                raise TimelineContractError("owner gap omitted reconciliation")
        if kind == "going-away":
            reference(value["reason"])
        return value

    def _facts(self, facts: Any) -> dict[str, Any]:
        value = object_fields(facts, {"origin", "coverage", "head_basis", "row"})
        if (
            value["origin"] != "resident-current-roster"
            or value["coverage"] != "sampled-selected-fields-without-timestamp-metadata"
            or value["head_basis"] != "asserted-listing-head" or not isinstance(value["row"], dict)
        ):
            raise TimelineContractError("unsupported or unqualified owner fact provenance")
        row = value["row"]
        if (
            row.get("id") != self.worktree or row.get("repo") != self.project
            or row.get("last_session_id") != self.session or row.get("head_yielded") is True
        ):
            raise TimelineContractError("current facts changed worktree or canonical head")
        incarnation = reference(row.get("creation_nonce"))
        if self._incarnation is not None and incarnation != self._incarnation:
            raise OwnerReadError("source-replaced", "selected record incarnation changed")
        self._incarnation = incarnation
        for name in ("summary", "title"):
            if row.get(name) is not None and not isinstance(row[name], str):
                raise TimelineContractError("current purpose fields must be literal text or unknown")
        if "head_yielded" in row and type(row["head_yielded"]) is not bool:
            raise TimelineContractError("owner head qualification must be boolean")
        return row

    def token(self, value: dict[str, Any], epoch: int) -> SourceToken:
        return SourceToken(SourceIdentity(
            Provider.WORKTREES, value["source"], self.project, self.worktree, self.session,
            value["source_generation"], value["resident_generation"],
        ), epoch)

    def check_source(self, value: dict[str, Any], token: SourceToken) -> None:
        if self.token(value, token.request_epoch) != token:
            raise OwnerReadError("source-replaced", "selected journal or record incarnation changed")
        if value["head_session_id"] != self.session and value.get("availability") != "head-changed":
            raise OwnerReadError("head-changed", "selected canonical head is no longer qualified")

    @staticmethod
    def position(token: SourceToken, cursor: str) -> SourcePosition:
        return SourcePosition(token.identity.source_key, token.identity.generation, cursor)

    def events(self, value: dict[str, Any], token: SourceToken) -> tuple[TimelineEvent, ...]:
        rows = []
        for item in value["entries"]:
            actor = object_fields(item["actor"], {"kind", "id", "qualification", "session_id"})
            source = object_fields(item["source_ref"], {"source", "generation", "record_id", "payload_ref"})
            reference(source["source"])
            reference(source["generation"])
            object_fields(item["correlation"], set(
                "session_id launch_id task_id tool_call_id agent_id parent_event_id request_id".split()
            ))
            for ref in (*source.values(), *item["correlation"].values(), item["native_kind"], item["head_session_id"]):
                reference(ref, nullable=True)
            for ref in (actor["id"], actor["session_id"]):
                reference(ref, nullable=True)
            if not isinstance(item["payload"], dict) or not isinstance(item["record_fence"], dict):
                raise TimelineContractError("owner event payload/fence must be objects")
            if not isinstance(item["relations"], list) or len(item["relations"]) > 32:
                raise TimelineContractError("owner event relations exceed their bound")
            relations = []
            for relation in item["relations"]:
                object_fields(relation, {"kind", "ref", "qualification"})
                reference(relation["ref"])
                relations.append(TimelineRelation(
                    RelationKind(relation["kind"]), Qualification(relation["qualification"]),
                    reference=relation["ref"],
                ))
            kind, evidence = TimelineKind(item["kind"]), EvidenceKind(item["evidence_kind"])
            if evidence is EvidenceKind.OBSERVED_CHANGE and kind not in {
                TimelineKind.OBSERVED_CHANGE, TimelineKind.SOURCE_EVENT,
            }:
                raise TimelineContractError("a sampled change cannot manufacture a canonical mutation")
            if evidence is EvidenceKind.PERSISTED_MUTATION and not item["record_fence"]:
                raise TimelineContractError("persisted mutation lacks its source fence")
            if evidence is EvidenceKind.RETAINED_EVENT and source["record_id"] is None:
                raise TimelineContractError("retained event lacks its native identity")
            if evidence is EvidenceKind.REPORTED_LIFECYCLE and (
                actor["qualification"] == "authoritative" or kind not in {
                    TimelineKind.CONTRIBUTION_REPORTED, TimelineKind.SOURCE_EVENT,
                }
            ):
                raise TimelineContractError("reported lifecycle cannot assert canonical mutation")
            observed = stamp(item["observed_at"])
            if observed is None:
                raise TimelineContractError("owner event omitted observation time")
            rows.append(TimelineEvent(
                token.identity, self.position(token, item["cursor"]), item["event_id"],
                TimelineActor(actor["id"], ActorKind(actor["kind"]), Qualification(actor["qualification"]),
                              actor["id"] or "Unknown actor"),
                kind, evidence, observed, item["summary"] if item["summary"] else kind.value,
                occurred_at=stamp(item["occurred_at"], nullable=True),
                details=item["details"] or "", relations=tuple(relations),
                payload_ref=source["payload_ref"], reported_phase=item["phase"],
                durability=Durability(value["durability"]),
                data_json=encoded(item),
            ))
        return tuple(rows)

    def snapshot(self, value: dict[str, Any], token: SourceToken) -> TimelineSnapshot:
        self.check_source(value, token)
        available = Availability(value["availability"])
        row = self._facts(value["facts"]) if available is Availability.OBSERVED else {}
        effort = row.get("active_effort")
        if effort is not None and not isinstance(effort, dict):
            raise TimelineContractError("owner active effort must be an object or unknown")
        if effort:
            for name in ("path", "slug", "state"):
                if effort.get(name) is not None and (
                    not isinstance(effort[name], str) or not effort[name]
                ):
                    raise TimelineContractError("owner effort fields must be text or unknown")
        effort_ref = None if not effort or effort.get("state") in {"closed", "done"} else (
            effort.get("path") or effort.get("slug")
        )
        entries = value["entries"]
        before = entries[0]["cursor"] if entries else value["cursor"]
        return TimelineSnapshot(
            token, self.position(token, value["cursor"]), available, Completeness(value["completeness"]),
            self.events(value, token) if available is Availability.OBSERVED else (),
            as_of=stamp(value["observed_at"]), purpose=row.get("summary") or row.get("title"),
            effort_ref=effort_ref, creation_context=None,
            before=self.position(token, before) if before != value["history_floor"] else None,
            facts_json=encoded(value["facts"]) if available is Availability.OBSERVED else None,
        )

    def page(
        self, value: dict[str, Any], token: SourceToken, before: SourcePosition,
    ) -> HistoryPage | TimelineGap:
        self.check_source(value, token)
        if value["type"] == "gap":
            if value["base_cursor"] != before.cursor:
                raise TimelineContractError("owner gap changed the requested page boundary")
            return self.gap(value, token, before=before)
        if value["type"] != "history-page" or value["before_cursor"] != before.cursor:
            raise TimelineContractError("owner page changed the requested before boundary")
        return HistoryPage(
            token, before,
            self.position(token, value["next_before"]) if value["next_before"] is not None else None,
            self.events(value, token), Completeness(value["completeness"]),
            Availability(value["availability"]),
        )

    def gap(
        self, value: dict[str, Any], token: SourceToken, *, before: SourcePosition | None = None,
    ) -> TimelineGap:
        self.check_source(value, token)
        return TimelineGap(
            token, self.position(token, value["cursor"]), self.position(token, value["history_floor"]),
            value["reason"], value["details"], Availability(value["availability"]),
            Completeness(value["completeness"]), before, value["reconcile"],
            encoded(value["source_ref"]) if value["source_ref"] is not None else None,
        )
