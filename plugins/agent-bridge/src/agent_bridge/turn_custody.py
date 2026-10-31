"""Original turn handoff observations; not native authentication or send proof."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sqlite3
import uuid

from .inbox_custody import observed_time
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, encode, nonblank, positive


def turn_index(value: int) -> None:
    if type(value) is not int or value < 0:
        raise RuntimeCustodyError("Native turn index must be a nonnegative integer")


@dataclass(frozen=True)
class NativeTurnConsumer:
    """Exact transport observations supplied and rechecked by its actual owner."""

    session_id: str
    native_session_id: str
    incarnation: str
    pid: int
    process_start: str

    def __post_init__(self) -> None:
        for value in (self.session_id, self.native_session_id, self.incarnation, self.process_start):
            nonblank(value)
        positive(self.pid)

    def document(self) -> dict[str, object]:
        return asdict(self)

def new_handoff(
    session_id: str, index: int, admission: RuntimeAdmission | None, consumer: NativeTurnConsumer,
) -> str:
    """Called only for the transaction inserting the original turn."""
    if type(consumer) is not NativeTurnConsumer or consumer.session_id != session_id:
        raise RuntimeCustodyError("Native turn admission requires its exact consumer")
    if type(admission) is not RuntimeAdmission:
        raise RuntimeCustodyError("Native turn handoff requires retained producer admission")
    turn_index(index)
    return encode({
        "version": 1, "id": uuid.uuid4().hex, "session_id": session_id, "turn_index": index,
        "consumer": consumer.document(), "admission": admission.document(),
    })


def decode_handoff(raw: str) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeCustodyError("Duplicate native turn handoff field")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if (
            type(value) is not dict
            or set(value) != {"version", "id", "session_id", "turn_index", "consumer", "admission"}
            or type(value["version"]) is not int or value["version"] != 1
        ):
            raise RuntimeCustodyError("Unsupported native turn handoff")
        nonblank(value["id"])
        nonblank(value["session_id"])
        turn_index(value["turn_index"])
        if type(value["consumer"]) is not dict or type(value["admission"]) is not str:
            raise RuntimeCustodyError("Unsupported native turn observations")
        consumer = NativeTurnConsumer(**value["consumer"])
        RuntimeAdmission.parse(value["admission"])
        if consumer.session_id != value["session_id"]:
            raise RuntimeCustodyError("Native turn consumer belongs to another session")
        return value
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeCustodyError("Malformed retained native turn handoff") from exc


def original_handoff(row: sqlite3.Row | dict) -> dict[str, object]:
    handoff = decode_handoff(row["native_handoff_json"])
    if (
        handoff["session_id"] != row["session_id"]
        or handoff["turn_index"] != row["turn_index"]
        or handoff["admission"] != row["runtime_custody_json"]
    ):
        raise RuntimeCustodyError("Original native turn observations changed")
    for name in ("native_handoff_started_at", "native_accepted_at", "native_consumed_at"):
        if row[name] is not None:
            observed_time(row[name])
    if row["native_outcome"] not in (None, "unknown") or (
        row["native_handoff_started_at"] is None
        and any(row[name] is not None for name in (
            "native_accepted_at", "native_consumed_at", "native_outcome",
        ))
    ):
        raise RuntimeCustodyError("Malformed original native turn outcome")
    return handoff


def handoff_stage(row: sqlite3.Row | dict) -> str:
    if row["native_consumed_at"] is not None:
        return "consumed"
    if row["native_accepted_at"] is not None:
        return "native_accepted"
    if row["native_outcome"] == "unknown":
        return "unknown"
    return "sending" if row["native_handoff_started_at"] is not None else "offered"
