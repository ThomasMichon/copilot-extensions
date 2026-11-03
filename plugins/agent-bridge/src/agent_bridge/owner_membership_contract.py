"""Strict owner membership reads and source-qualified response validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .owner_membership import CAPABILITY
from .owner_membership_records import MEMBER_FIELDS, owner_ref
from .session_observation_contract import ObservationError, encode, read_position, text


@dataclass(frozen=True)
class OwnerRead:
    owner: str
    follow: bool = False
    after: str | None = None

    @classmethod
    def parse(cls, pairs: list[tuple[str, str]]) -> OwnerRead:
        query: dict[str, str] = {}
        for key, value in pairs:
            if key in query:
                raise ObservationError("invalid-request", "duplicate membership request field")
            query[key] = value
        if (
            set(query) - {"owner_ref", "contract_version", "follow", "after"}
            or not {"owner_ref", "contract_version"} <= query.keys()
        ):
            raise ObservationError(
                "invalid-request", "unknown or missing membership request field"
            )
        if query["contract_version"] != "1":
            raise ObservationError("unsupported-version", "membership contract_version must be 1")
        follow = query.get("follow", "false")
        if follow not in {"true", "false"}:
            raise ObservationError("invalid-request", "follow must be true or false")
        after = query.get("after")
        if after is not None:
            text(after, "membership position", maximum=4096)
        return cls(owner_ref(query["owner_ref"]), follow == "true", after)


def validate(body: dict[str, Any], request: OwnerRead) -> None:
    """Positions and safe membership data stay in this exact owner/generation."""
    if (
        body.get("schema") != CAPABILITY
        or type(body.get("version")) is not int
        or body["version"] != 1
        or body.get("kind") not in {"snapshot", "delta", "heartbeat", "gap", "error", "going-away"}
    ):
        raise ObservationError("invalid-frame", "wrong membership response contract")
    source = body.get("source")
    if not isinstance(source, dict) or source.get("owner_ref") != request.owner:
        raise ObservationError("invalid-frame", "membership response targets another owner")
    if body["kind"] in {"error", "gap", "going-away"}:
        return
    if (
        source.get("provider") != "bridge"
        or source.get("head_session_id") is not None
        or source.get("journal_incarnation") != source.get("hosting_generation")
    ):
        raise ObservationError("invalid-frame", "invalid membership source identity")
    text(source.get("hosting_generation"), "hosting generation")
    text(source.get("hosting_authority"), "hosting authority")
    read_position(body.get("position"), source)
    if body["kind"] in {"snapshot", "delta"}:
        for field in ("members", "changes"):
            values = body.get(field)
            if not isinstance(values, list) or len(values) > 64:
                raise ObservationError("invalid-frame", "invalid membership frame size")
        seen: set[str] = set()
        for member in body["members"]:
            if (
                not isinstance(member, dict)
                or member.get("owner_ref") != request.owner
                or not isinstance(member.get("member_id"), str)
                or member["member_id"] in seen
                or set(member) - MEMBER_FIELDS
            ):
                raise ObservationError("invalid-frame", "duplicate or foreign membership fact")
            seen.add(member["member_id"])
        previous = -1
        for change in body["changes"]:
            if not isinstance(change, dict) or not isinstance(change.get("member"), dict):
                raise ObservationError("invalid-frame", "invalid membership change")
            if change["member"].get("owner_ref") != request.owner:
                raise ObservationError("invalid-frame", "foreign membership change")
            if (
                set(change["member"]) - MEMBER_FIELDS
                or change.get("operation") not in {"upsert", "tombstone"}
                or change.get("grants_claim_release") is not False
            ):
                raise ObservationError("invalid-frame", "invalid membership projection fields")
            sequence = read_position(change.get("id"), source)
            if sequence <= previous:
                raise ObservationError("invalid-frame", "duplicate or unordered membership change")
            previous = sequence
        if body.get("base_position") is not None:
            read_position(body["base_position"], source)
    encode(body)
