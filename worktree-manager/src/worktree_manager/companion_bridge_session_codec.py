"""Selected known-session evidence; never infer answering, head or resource authority."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from .companion_bridge_codec import MembershipCodec, offset
from .companion_bridge_current_requests import CurrentRequestState, decode_current_requests
from .companion_timeline_contract import (
    ActorKind, Availability, Completeness, EvidenceKind, HistoryPage, Provider, Qualification,
    RelationKind, SourceIdentity, SourcePosition, SourceToken, TimelineActor, TimelineContractError,
    TimelineEvent, TimelineKind, TimelineRelation, TimelineSnapshot, check_wire_body,
    decode_wire_frame, identifier,
)
from .companion_worktree_codec import OwnerReadError, encoded, object_fields, stamp

OBSERVATIONS = "bridge-session-observations-v1"
HISTORY = "bridge-session-observation-history-v1"
SOURCE_FIELDS = frozenset(
    "provider project worktree_id head_session_id session_id durable_session_id "
    "hosting_generation registry transport journal_incarnation".split()
)
FRAME_FIELDS = frozenset(
    "schema version kind source availability coverage durability retained position base_position "
    "before next_before has_more entries current_requests limitations".split()
)
ENTRY_FIELDS = frozenset(
    "id native_event_id native_kind kind evidence_kind occurred_at timestamp_basis details "
    "omitted_fields payload_ref payload_omitted answerability authority content_sha256".split()
)
KINDS = {
    "input-request": TimelineKind.INPUT_REQUEST, "permission-request": TimelineKind.PERMISSION_REQUEST,
    "compaction": TimelineKind.COMPACTION, "context-pressure": TimelineKind.CONTEXT_PRESSURE,
    "contribution-reported": TimelineKind.CONTRIBUTION_REPORTED,
    "input-cleared": TimelineKind.SOURCE_EVENT, "permission-cleared": TimelineKind.SOURCE_EVENT,
    "policy-request": TimelineKind.SOURCE_EVENT, "reported-lifecycle": TimelineKind.SOURCE_EVENT,
    "source-event": TimelineKind.SOURCE_EVENT,
}
EVENT_LABELS = {
    "tool_call_start": "Tool started",
    "tool_call_update": "Tool update",
    "subagent_started": "Reported agent start",
    "subagent_completed": "Reported agent completion",
    "subagent_failed": "Reported agent failure",
    "ask_user_request": "Input requested",
    "ask_user_resolved": "Input request resolved",
    "ask_user_withdrawn": "Input request withdrawn",
    "permission_request": "Permission requested",
    "permission_resolved": "Permission request resolved",
    "policy_required": "Policy input required",
    "compaction_start": "Compaction started",
    "compaction_complete": "Compaction completed",
    "context_pressure": "Context pressure reported",
    "session_handoff": "Session handoff reported",
    "turn_complete": "Turn completed",
    "session_state_changed": "Session state reported",
}


def _event_summary(item: dict[str, Any]) -> str:
    native = item["native_kind"]
    label = EVENT_LABELS.get(native)
    if label is None:
        return "Bridge session: " + native
    details = item["details"]
    if native == "tool_call_update":
        status = details.get("status")
        if isinstance(status, str):
            label = {
                "pending": "Tool pending", "in_progress": "Tool in progress",
                "completed": "Tool completed", "failed": "Tool failed",
                "cancelled": "Tool cancelled", "canceled": "Tool cancelled",
            }.get(status, label)
    if native in {"subagent_started", "subagent_completed", "subagent_failed"}:
        fields = ("agent_display_name", "agent_name")
    elif native in {"tool_call_start", "tool_call_update"}:
        fields = ("title",)
    else:
        fields = ()
    for field in fields:
        value = details.get(field)
        if isinstance(value, str) and len(value) <= 512 and value.strip():
            value = value.strip()
            return label + ": " + value[:160] + ("..." if len(value) > 160 else "")
    return label


class SessionCodec:
    def __init__(
        self, project: str, worktree: str, owner: str, member: dict[str, Any],
        hosting_generation: str,
    ) -> None:
        membership = MembershipCodec(project, worktree, owner)
        membership.member(member)
        if member["registry"] not in {"owned", "represented"} or member["worktree_id"] is None:
            raise OwnerReadError("unsupported", "member has no locally admitted known-session binding")
        if member["registry"] == "represented" and member["project"] is not None:
            raise TimelineContractError("represented session project must remain unknown")
        identifier(hosting_generation, "Bridge hosting generation")
        self.project, self.worktree, self.owner = project, worktree, membership.owner
        self.member = json.loads(encoded(member))
        self.hosting_generation = hosting_generation
        self.source: dict[str, Any] | None = None
        self.current_state: CurrentRequestState | None = None

    @property
    def selection(self) -> dict[str, Any]:
        return {
            "session_id": self.member["bridge_session_id"], "project": self.member["project"],
            "worktree_id": self.member["worktree_id"], "head_session_id": None,
        }

    def decode(
        self, raw: dict[str, Any], *, history: bool = False, current_requests: bool = False,
    ) -> dict[str, Any]:
        # Preserve provider field order: its entry hash covers its compact JSON encoding.
        frame = decode_wire_frame(json.dumps(raw, ensure_ascii=True, allow_nan=False).encode())
        check_wire_body(frame)
        if (
            frame.get("schema") != (HISTORY if history else OBSERVATIONS)
            or type(frame.get("version")) is not int or frame["version"] != 1
            or not isinstance(frame.get("kind"), str)
        ):
            raise TimelineContractError("wrong Bridge session action or version")
        kind = frame["kind"]
        if kind in {"error", "gap", "going-away"}:
            if "selection" in frame and frame["selection"] != self.selection:
                raise TimelineContractError("Bridge failure changed its selected session")
            code, message = frame.get("code"), frame.get("message")
            if kind == "going-away" and not history:
                raise OwnerReadError("source-stopping", "Bridge session hosting generation stopped")
            if not isinstance(code, str) or not isinstance(message, str):
                raise TimelineContractError("Bridge session failure omitted its diagnostic")
            raise OwnerReadError(code, message)
        source = object_fields(frame.get("source"), SOURCE_FIELDS)
        if (
            source["provider"] != "bridge" or source["registry"] != self.member["registry"]
            or any(source[key] != value for key, value in self.selection.items())
            or source["hosting_generation"] != self.hosting_generation
            or (self.member["durable_session_id"] is not None
                and source["durable_session_id"] != self.member["durable_session_id"])
        ):
            raise TimelineContractError("Bridge session source changed its admitted binding")
        for field in ("hosting_generation", "transport"):
            identifier(source[field], field)
        for field in ("journal_incarnation", "durable_session_id"):
            if source[field] is not None:
                identifier(source[field], field)
        if self.source is not None and source != self.source:
            # An empty log may acquire its first real continuity, never replace one.
            if self.source["journal_incarnation"] is not None or (
                {**self.source, "journal_incarnation": source["journal_incarnation"]} != source
            ):
                raise OwnerReadError("source-replaced", "Bridge session incarnation changed; Refresh required")
        if kind == "heartbeat" and not history:
            object_fields(frame, {"schema", "version", "kind", "source", "position"})
            if frame["position"] is not None:
                offset(frame["position"], source)
            return frame
        optional = current_requests and not history
        object_fields(frame, FRAME_FIELDS | ({"current_request_state"} if optional else set()))
        current_state = decode_current_requests(frame["current_request_state"]) if optional else None
        if (
            kind not in ({"history-page"} if history else {"snapshot", "delta"})
            or frame["availability"] != ("observed" if source["journal_incarnation"] else "unknown")
            or frame["coverage"] != "partial" or frame["durability"] != "unknown"
            or frame["current_requests"] != "unknown" or type(frame["has_more"]) is not bool
        ):
            raise TimelineContractError("Bridge session strengthened unavailable evidence")
        retained = object_fields(frame["retained"], {"floor", "head", "complete"})
        if (
            any(type(retained[key]) is not int or not 0 <= retained[key] <= 2**63 - 1
                for key in ("floor", "head"))
            or retained["floor"] > retained["head"] + 1 or retained["complete"] is not False
            or not isinstance(frame["entries"], list) or len(frame["entries"]) > 64
            or not isinstance(frame["limitations"], list)
            or any(not isinstance(item, str) for item in frame["limitations"])
        ):
            raise TimelineContractError("invalid Bridge session retention or page")
        for field in ("position", "base_position", "before", "next_before"):
            if frame[field] is not None:
                offset(frame[field], source)
        if (frame["position"] is None) != (source["journal_incarnation"] is None):
            raise TimelineContractError("Bridge session position lost its continuity qualification")
        prior = None
        for item in frame["entries"]:
            object_fields(item, ENTRY_FIELDS)
            sequence = offset(item["id"], source)
            if (
                type(item["native_event_id"]) is not int or item["native_event_id"] != sequence or sequence < 1
                or not retained["floor"] <= sequence <= retained["head"]
                or (prior is not None and sequence != prior + 1)
                or item["payload_ref"] != item["id"] or item["payload_omitted"] is not True
                or item["answerability"] != "unknown" or item["authority"] != "reported"
                or item["timestamp_basis"] != "bridge-event-recorded"
                or not isinstance(item["kind"], str) or item["kind"] not in KINDS
                or item["evidence_kind"] not in ("retained-event", "reported-lifecycle")
                or not isinstance(item["details"], dict) or not isinstance(item["omitted_fields"], list)
                or any(not isinstance(value, str) for value in item["omitted_fields"])
            ):
                raise TimelineContractError("invalid Bridge session native event or qualification")
            identifier(item["native_kind"], "Bridge native event kind")
            stamp(item["occurred_at"], nullable=True)
            digest = sha256(json.dumps(
                {key: value for key, value in item.items() if key != "content_sha256"},
                ensure_ascii=True, allow_nan=False, separators=(",", ":"),
            ).encode("ascii")).hexdigest()
            if item["content_sha256"] != digest:
                raise TimelineContractError("Bridge native event content hash changed")
            prior = sequence
        position = offset(frame["position"], source) if frame["position"] is not None else 0
        if position > retained["head"] or (prior is not None and position != prior):
            raise TimelineContractError("Bridge session position does not end at its delivered evidence")
        if kind == "delta":
            base = offset(frame["base_position"], source)
            if frame["before"] is not None or (prior is None and position != base) or (
                frame["entries"] and frame["entries"][0]["native_event_id"] != base + 1
            ):
                raise TimelineContractError("Bridge session delta lost its base")
        elif frame["base_position"] is not None:
            raise TimelineContractError("Bridge session non-delta acquired a live base")
        if not history and frame["before"] is not None:
            raise TimelineContractError("Bridge session observation acquired a history boundary")
        if kind != "delta":
            end = retained["head"]
            if history and frame["before"] is not None:
                boundary = offset(frame["before"], source)
                if not retained["floor"] <= boundary <= retained["head"] + 1:
                    raise TimelineContractError("Bridge history boundary is outside the retained range")
                end = boundary - 1
            if (prior is not None and prior != end) or (
                prior is None and retained["floor"] > 0 and end >= retained["floor"]
            ):
                raise TimelineContractError("Bridge page omitted events before its retained boundary")
        next_before = frame["next_before"]
        expected_before = (
            frame["entries"][0]["id"] if frame["entries"]
            and frame["entries"][0]["native_event_id"] > retained["floor"] else None
        )
        if next_before != expected_before or (
            kind != "delta" and frame["has_more"] != (next_before is not None)
        ) or (kind == "delta" and frame["has_more"] != (position < retained["head"])):
            raise TimelineContractError("Bridge history omitted or invented retained range")
        self.source = source
        if not history:
            self.current_state = current_state
        return frame

    def token(self, frame: dict[str, Any], epoch: int) -> SourceToken:
        source = frame["source"]
        if source["journal_incarnation"] is None:
            raise OwnerReadError("source-unavailable", "Bridge log has no real event continuity yet")
        generation = sha256(encoded(source).encode()).hexdigest()
        return SourceToken(SourceIdentity(
            Provider.BRIDGE, "bridge-session:" + sha256(encoded({
                "owner": self.owner, **self.selection, "registry": self.member["registry"],
            }).encode()).hexdigest(),
            self.project, self.worktree, None, generation, self.owner,
        ), epoch)

    def position(self, token: SourceToken, wire: str) -> SourcePosition:
        if self.source is None:
            raise TimelineContractError("Bridge session source is not admitted")
        return SourcePosition(token.identity.source_key, token.identity.generation,
                              "event:" + str(offset(wire, self.source)))

    def events(self, frame: dict[str, Any], token: SourceToken) -> tuple[TimelineEvent, ...]:
        result = []
        for item in frame["entries"]:
            payload = encoded(item)
            if len(payload.encode()) > 4096:
                payload = encoded({
                    "detail_omitted": True, "content_sha256": item["content_sha256"],
                    "native_event_id": item["native_event_id"], "native_kind": item["native_kind"],
                    "encoded_bytes": len(encoded(item).encode()),
                    "payload_ref": item["payload_ref"] if len(item["payload_ref"].encode()) <= 2048 else None,
                    "payload_ref_sha256": sha256(item["payload_ref"].encode()).hexdigest(),
                    "payload_ref_bytes": len(item["payload_ref"].encode()),
                })
            occurred = stamp(item["occurred_at"], nullable=True)
            detail = (
                f"Native {item['native_kind']}; partial retained event; current requests and "
                "answerability unknown. Reported linkage is not custody."
            )
            if item["kind"] == "compaction":
                detail += " Pre-context classification unknown; active pre-window capacity is unqualified."
            result.append(TimelineEvent(
                token.identity, self.position(token, item["id"]), "event:" + str(item["native_event_id"]),
                TimelineActor(self.member["bridge_session_id"], ActorKind.BRIDGE_SESSION,
                              Qualification.REPORTED, self.member["bridge_session_id"]),
                KINDS[item["kind"]], EvidenceKind(item["evidence_kind"]), occurred,
                _event_summary(item), details=detail,
                relations=(TimelineRelation(RelationKind.PARENT, Qualification.REPORTED, reference=self.owner),),
                payload_ref=item["payload_ref"] if len(item["payload_ref"].encode()) <= 1024 else None,
                data_json=payload,
            ))
        return tuple(result)

    def snapshot(self, frame: dict[str, Any], token: SourceToken) -> TimelineSnapshot:
        entries = self.events(frame, token)
        return TimelineSnapshot(
            token, self.position(token, frame["position"]) if frame["position"] else None,
            Availability.OBSERVED if frame["position"] else Availability.UNKNOWN, Completeness.PARTIAL,
            entries, as_of=datetime.now(timezone.utc),
            before=entries[0].position if frame["next_before"] and entries else None,
            facts_json=encoded({
                "session_source": frame["source"], "retained": frame["retained"],
                "current_requests": "unknown", "answerability": "unknown", "limitations": frame["limitations"],
            }),
        )

    def page(self, frame: dict[str, Any], token: SourceToken, before: SourcePosition) -> HistoryPage:
        return HistoryPage(
            token, before, self.position(token, frame["next_before"]) if frame["next_before"] else None,
            self.events(frame, token), Completeness.PARTIAL,
        )
