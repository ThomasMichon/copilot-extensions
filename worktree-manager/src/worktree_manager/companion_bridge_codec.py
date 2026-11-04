"""Passive Bridge membership projection; linkage and disappearance are not custody."""

from __future__ import annotations

import base64
import binascii
from hashlib import sha256
from typing import Any

from .companion_timeline_contract import (
    ActorKind, Availability, Completeness, EvidenceKind, LiveDelta, MAX_BODY_BYTES, Provider, Qualification,
    RelationKind, SourceIdentity, SourcePosition, SourceToken, TimelineActor,
    TimelineContractError, TimelineEvent, TimelineKind, TimelineRelation, TimelineSnapshot,
    check_wire_body, decode_wire_frame, identifier,
)
from .companion_worktree_codec import OwnerReadError, encoded, object_fields, stamp

CAPABILITY = "bridge-owner-membership-v1"
MEMBER_FIELDS = frozenset(
    "member_id owner_ref registry bridge_session_id durable_session_id project worktree_id "
    "head_session_id current_answerability liveness freshness link_qualification retirement state "
    "transport incarnation admission_phase relationship_id uncertain_remote_outcome "
    "relationship_qualification".split()
)
SOURCE_FIELDS = frozenset(
    "provider owner_ref project worktree_id head_session_id hosting_generation "
    "journal_incarnation hosting_authority".split()
)
FRAME_FIELDS = frozenset(
    "schema version kind source availability completeness durability head_session_id observed_at "
    "retained base_position position members changes has_more current_answerability limitations".split()
)
RETIREMENTS = frozenset({
    None, "session-retired", "confirmed-relationship-retired", "registration-expired",
    "registration-taken-over", "unreserved-relationship-closed", "local-admission-retired",
})


def selected_owner(value: Any, project: str, worktree: str) -> str:
    if not isinstance(value, str):
        raise OwnerReadError("unavailable", "selected worktree has no qualified owner reference")
    owner = value.partition("#")[0]
    parts = owner.split("/")
    if (
        len(owner) > 1024 or len(parts) < 3 or parts[1] != project
        or "/".join(parts[2:]) != worktree
        or any(not part or part in {".", ".."} for part in parts)
        or any(ord(char) < 33 or char == "\\" for char in owner)
    ):
        raise TimelineContractError("Bridge owner reference does not match the selected worktree")
    return owner


def offset(value: Any, source: dict[str, Any]) -> int:
    if not isinstance(value, str) or not value.startswith("abso1.") or len(value) > 4096:
        raise TimelineContractError("invalid Bridge membership position")
    try:
        raw = value[6:]
        body = decode_wire_frame(base64.b64decode(
            raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True,
        ))
    except (ValueError, binascii.Error) as error:
        raise TimelineContractError("invalid Bridge membership position") from error
    object_fields(body, {"version", "source", "event_id"})
    if (
        type(body["version"]) is not int or body["version"] != 1
        or body["source"] != source or type(body["event_id"]) is not int
        or not 0 <= body["event_id"] <= 2**63 - 1
    ):
        raise TimelineContractError("Bridge membership position changed its source or sequence")
    return body["event_id"]


class MembershipCodec:
    def __init__(self, project: str, worktree: str, owner: str) -> None:
        self.project, self.worktree = project, worktree
        self.owner = selected_owner(owner, project, worktree)
        self.source: dict[str, Any] | None = None
        self.members: dict[str, dict[str, Any]] = {}
        self.replaying = False

    def decode(self, raw: bytes) -> dict[str, Any]:
        frame = decode_wire_frame(raw)
        check_wire_body(frame)
        if (
            frame.get("schema") != CAPABILITY or type(frame.get("version")) is not int
            or frame["version"] != 1
        ):
            raise TimelineContractError("unsupported Bridge membership contract")
        source = frame.get("source")
        if not isinstance(source, dict) or source.get("owner_ref") != self.owner:
            raise TimelineContractError("Bridge membership response belongs to another owner")
        kind = frame.get("kind")
        if not isinstance(kind, str):
            raise TimelineContractError("invalid Bridge membership frame kind")
        if kind in {"error", "gap"}:
            if not isinstance(frame.get("code"), str) or not isinstance(frame.get("message"), str):
                raise TimelineContractError("Bridge membership failure omitted its diagnostic")
            raise OwnerReadError(frame["code"], frame["message"])
        if kind == "going-away":
            raise OwnerReadError("source-stopping", "Bridge membership hosting generation stopped")
        object_fields(source, SOURCE_FIELDS)
        if (
            source["provider"] != "bridge" or source["project"] != self.project
            or source["worktree_id"] != self.worktree or source["head_session_id"] is not None
            or source["journal_incarnation"] != source["hosting_generation"]
        ):
            raise TimelineContractError("Bridge membership source changed its selection or qualification")
        for field in ("hosting_generation", "hosting_authority"):
            identifier(source[field], field)
        if self.source is not None and source != self.source:
            raise OwnerReadError("source-replaced", "Bridge membership generation changed; Refresh required")
        offset(frame.get("position"), source)
        if kind == "heartbeat":
            object_fields(frame, {"schema", "version", "kind", "source", "position"})
            return frame
        object_fields(frame, FRAME_FIELDS)
        if (
            kind not in {"snapshot", "delta"} or frame["availability"] != "observed"
            or frame["completeness"] != "partial" or frame["durability"] != "unknown"
            or frame["head_session_id"] is not None or frame["current_answerability"] != "unknown"
            or type(frame["has_more"]) is not bool
        ):
            raise TimelineContractError("Bridge membership strengthened unavailable authority")
        stamp(frame["observed_at"])
        retained = object_fields(frame["retained"], {"floor", "head", "complete"})
        if (
            any(type(retained[key]) is not int or not 0 <= retained[key] <= 2**63 - 1
                for key in ("floor", "head"))
            or retained["floor"] > retained["head"] + 1 or retained["complete"] is not False
            or offset(frame["position"], source) > retained["head"]
        ):
            raise TimelineContractError("invalid Bridge membership retained range")
        if not isinstance(frame["limitations"], list) or any(
            not isinstance(item, str) for item in frame["limitations"]
        ):
            raise TimelineContractError("invalid Bridge membership coverage")
        for field in ("members", "changes"):
            if not isinstance(frame[field], list) or len(frame[field]) > 64:
                raise TimelineContractError("Bridge membership frame exceeded its record bound")
        seen: set[str] = set()
        for member in frame["members"]:
            self.member(member)
            if member["member_id"] in seen:
                raise TimelineContractError("duplicate Bridge member")
            seen.add(member["member_id"])
        previous = -1
        if kind == "snapshot":
            if frame["base_position"] is not None or frame["changes"] or frame["has_more"]:
                raise TimelineContractError("Bridge membership snapshot is not an atomic bootstrap")
        else:
            previous = offset(frame["base_position"], source)
            if frame["members"]:
                raise TimelineContractError("Bridge membership delta cannot replace current members")
        for change in frame["changes"]:
            object_fields(change, {
                "kind", "operation", "member", "evidence_kind", "grants_claim_release", "observed_at", "id",
            })
            self.member(change["member"])
            sequence = offset(change["id"], source)
            if (
                sequence <= previous or sequence > offset(frame["position"], source)
                or not isinstance(change["operation"], str)
                or change["operation"] not in {"upsert", "tombstone"}
                or change["evidence_kind"] != "observed-change"
                or change["grants_claim_release"] is not False
                or not isinstance(change["kind"], str)
            ):
                raise TimelineContractError("Bridge membership change lost continuity or custody qualification")
            stamp(change["observed_at"])
            previous = sequence
        if kind == "delta" and offset(frame["position"], source) < previous:
            raise TimelineContractError("Bridge membership cursor regressed")
        return frame

    def member(self, member: Any) -> None:
        required = MEMBER_FIELDS - {
            "relationship_id", "uncertain_remote_outcome", "relationship_qualification",
        }
        if not isinstance(member, dict) or not required <= member.keys() or set(member) - MEMBER_FIELDS:
            raise TimelineContractError("invalid Bridge member fields")
        for field in ("registry", "link_qualification", "retirement", "relationship_qualification"):
            if field == "retirement" and member[field] is None:
                continue
            if field in member and not isinstance(member[field], str):
                raise TimelineContractError("invalid Bridge member " + field)
        if (
            member["owner_ref"] != self.owner or member["registry"] not in {"owned", "represented", "relationship"}
            or member["head_session_id"] is not None or member["current_answerability"] != "unknown"
            or member["liveness"] != "unknown" or member["freshness"] != "unknown"
            or member["link_qualification"] not in {"reported", "host-confirmed"}
            or member["retirement"] not in RETIREMENTS
            or not isinstance(member["incarnation"], dict)
        ):
            raise TimelineContractError("Bridge member changed its owner or evidence qualification")
        for key in ("member_id", "bridge_session_id", "state", "transport", "admission_phase"):
            identifier(member[key], "Bridge member " + key)
        for key in ("durable_session_id", "project", "worktree_id", "relationship_id"):
            if member.get(key) is not None:
                identifier(member[key], "Bridge member " + key)
        if "uncertain_remote_outcome" in member and type(member["uncertain_remote_outcome"]) is not bool:
            raise TimelineContractError("invalid remote outcome qualification")
        registry = member["registry"]
        incarnation_fields = {
            "owned": {"created_at"}, "represented": {"pid", "process_started_at", "registered_at"},
            "relationship": {"relationship_id"},
        }
        object_fields(member["incarnation"], incarnation_fields[registry])
        if registry == "relationship":
            identifier(member.get("relationship_id"), "Bridge member relationship_id")
            if (
                not {"relationship_id", "uncertain_remote_outcome", "relationship_qualification"} <= member.keys()
                or member["incarnation"]["relationship_id"] != member["relationship_id"]
                or member["relationship_qualification"] not in {
                    "unconfirmed", "unknown", "host-confirmed", "reservation-absence-confirmed",
                }
            ):
                raise TimelineContractError("invalid Bridge relationship qualification")
        elif set(member) != required or member["link_qualification"] != "reported":
            raise TimelineContractError("registration/session linkage is only reported")
        native_id = member["relationship_id"] if registry == "relationship" else member["bridge_session_id"]
        if member["member_id"] != registry + ":" + native_id:
            raise TimelineContractError("Bridge member changed its native identity")

    def token(self, frame: dict[str, Any], epoch: int) -> SourceToken:
        self.source = frame["source"]
        digest = sha256(encoded(self.source).encode()).hexdigest()
        return SourceToken(SourceIdentity(
            Provider.BRIDGE, "bridge-membership:" + sha256(self.owner.encode()).hexdigest(),
            self.project, self.worktree, None, digest, self.owner,
        ), epoch)

    def position(self, token: SourceToken, cursor: str) -> SourcePosition:
        if self.source is None:
            raise TimelineContractError("Bridge membership source is not admitted")
        # Preserve the full opaque wire token in the adapter. The internal
        # position is the validated generation-qualified membership sequence.
        return SourcePosition(
            token.identity.source_key, token.identity.generation,
            "membership:" + str(offset(cursor, self.source)),
        )

    def apply(self, frame: dict[str, Any], token: SourceToken) -> TimelineSnapshot:
        members = dict(self.members)
        if frame["kind"] == "snapshot":
            members = {member["member_id"]: member for member in frame["members"]}
        else:
            for change in frame["changes"]:
                member = change["member"]
                if change["operation"] == "tombstone":
                    members.pop(member["member_id"], None)
                else:
                    members[member["member_id"]] = member
        # A capture orders upserts and tombstones by native ID, then pages
        # them. Two valid 64-member states may temporarily coexist in replay.
        if len(members) > (128 if frame["has_more"] else 64):
            raise TimelineContractError("Bridge membership state exceeded its admitted bound")
        if len(encoded(list(members.values())).encode()) > 2 * MAX_BODY_BYTES:
            raise TimelineContractError("Bridge membership replay exceeded its byte bound")
        facts = encoded({
            "members": [] if frame["has_more"] else [members[key] for key in sorted(members)],
            "limitations": [
                *frame["limitations"], *(["membership-replay-incomplete"] if frame["has_more"] else []),
            ],
            "current_answerability": "unknown",
        })
        result = TimelineSnapshot(
            token, self.position(token, frame["position"]), Availability.OBSERVED, Completeness.PARTIAL,
            as_of=stamp(frame["observed_at"]), facts_json=facts,
        )
        self.members = members
        self.replaying = frame["has_more"]
        return result

    def delta(self, frame: dict[str, Any], token: SourceToken) -> LiveDelta:
        events = []
        for change in frame["changes"]:
            member = change["member"]
            payload = encoded(change)
            if len(payload.encode()) > 4096:
                payload = encoded({
                    "detail_omitted": True, "sha256": sha256(payload.encode()).hexdigest(),
                    "member_id": member["member_id"], "native_kind": change["kind"],
                })
            events.append(TimelineEvent(
                token.identity, self.position(token, change["id"]),
                "membership-change:" + str(offset(change["id"], frame["source"])),
                TimelineActor(member["member_id"], ActorKind.BRIDGE_SESSION, Qualification.REPORTED,
                              member["bridge_session_id"], member["state"]),
                TimelineKind.BRIDGE_MEMBER_CHANGED, EvidenceKind.OBSERVED_CHANGE,
                stamp(change["observed_at"]), "Bridge membership: " + change["kind"],
                details=(
                    f"{change['operation']}; {member['registry']}; {member['transport']}; "
                    f"link {member['link_qualification']}; retirement {member['retirement'] or 'unknown'}. "
                    "Liveness, freshness, canonical head and answerability unknown; no claim release granted."
                ),
                relations=(TimelineRelation(RelationKind.PARENT, Qualification.REPORTED, reference=self.owner),),
                data_json=payload,
            ))
        return LiveDelta(
            token, self.position(token, frame["base_position"]),
            self.position(token, frame["position"]), tuple(events),
        )
